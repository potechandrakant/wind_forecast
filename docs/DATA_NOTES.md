# Phase 0 data notes

## Live verification status

I checked the public BMRS docs pages and attempted to hit the live endpoints directly from this environment. The direct API calls failed with connection errors (`curl: (28) Failed to connect to api.bmreports.com:443`), so the exact live payloads could not be fetched from this machine at the time of review. The notes below therefore reflect the dataset contract in the challenge brief and the BMRS docs app metadata, plus the validation rules we must enforce in the ingestion script.

## Confirmed dataset contract

### Actuals: FUELHH stream
- Dataset: `FUELHH`
- Filter: `fuelType == "WIND"`
- Key fields: `startTime`, `generation`
- Resolution: 30-minute settlement periods
- Unit: MW
- Timestamp format: Elexon/BMRS settlement datetime (likely ISO-8601 with UTC / Europe/London handling). The app must normalise to a consistent timezone before analysis.

### Forecasts: WINDFOR stream
- Dataset: `WINDFOR`
- Key fields: `startTime`, `publishTime`, `generation`
- Resolution in the downloaded snapshot: hourly target times, with a separate `publishTime` that identifies when the forecast was created
- Unit: MW
- This means the same delivery half-hour can appear multiple times with different `publishTime` values; the decision-time logic must choose the latest forecast published on or before the cut-off.

## Decision-time interpretation

The challenge brief makes the following pattern explicit:
- Actuals are one value per half-hour target time.
- Forecasts are one value per `startTime` + `publishTime` pair.
- For a given target half-hour `T`, the relevant forecast is the latest forecast for `T` whose `publishTime` is at or before the relevant cut-off.

This is not a single forecast per target time; it is a rolling series of forecast updates over time.

## Answers to the Phase 0 questions

### 1) Forecast resolution (hourly or half-hourly)?
Actuals are half-hourly, but the downloaded `WINDFOR` snapshot is hourly: forecast target times occur on the hour while actual target times occur on both the hour and half-hour. This was verified from the full snapshot. The analysis explicitly repeats the hourly forecast for the following half-hour; it does not interpolate.

### 2) How many forecasts exist per target time?
Potentially multiple revisions exist for each hourly forecast target time. The presence of `publishTime` indicates a forecast revision history, not a single static forecast. The number is not fixed; the app treats each `(startTime, publishTime)` pair as a separate record and then selects the latest valid revision before the cut-off.

### 3) How far ahead do they run?
The exact horizon is not fully confirmed from this environment because the live API was unreachable, but the challenge is clearly concerned with day-ahead decisions and the D-1 cut-off. That implies the forecast stream spans at least the relevant delivery day(s) and includes earlier forecast revisions before the decision time.

### 4) How often are they published?
The dataset does not imply a fixed publication cadence per half-hour; instead, it supports repeated forecast revisions over time. In practice, the app should assume rolling updates and not assume one forecast per target hour. The ingestion layer must preserve all rows and later select by `publishTime`.

### 5) Are there gaps or duplicates in actuals?
We should not assume there are no issues. The challenge explicitly requires validation for:
- duplicate keys
- missing half-hours within the needed range
- negative generation values
- row counts and coverage checks

So the working assumption is: actuals should be contiguous for each settlement period, but the loader must validate and flag gaps or duplicates rather than silently accepting them.

## Implementation assumptions for the project

1. We will use the BMRS `FUELHH` actuals for `fuelType == "WIND"` only.
2. We will treat `WINDFOR` as a revision history by `startTime` and `publishTime`.
3. We will keep the decision cut-off configurable and default it to `09:00 Europe/London` on D-1.
4. We will treat half-hourly actuals as the canonical target resolution and repeat each hourly forecast for the immediately following half-hour.
5. If the live API remains unavailable in later phases, the committed snapshot under `data/` will be the source of truth and the app will not hit the live API at runtime.

## What to do next

## Phase 1 ingestion implementation

The ingestion script is [scripts/fetch_data.py](../scripts/fetch_data.py). It:

- requests the target window in seven-day chunks by default;
- filters `FUELHH` rows to `fuelType == "WIND"`;
- preserves every `WINDFOR` revision using `(startTime, publishTime)` as its key;
- normalises timestamps to UTC ISO-8601 strings;
- validates duplicate keys, missing half-hours, negative values, numeric generation, and response shape;
- writes `data/actuals_wind.csv`, `data/forecasts_wind.csv`, and `data/quality_report.json` only after all chunks pass validation.

The default target window is `[2026-01-02, 2026-02-02)`, matching the documented interpretation of January 2026 D-1 decisions. The endpoint, date window, chunk size, timeout, and output directory are configurable:

```powershell
python scripts\fetch_data.py
python scripts\fetch_data.py --start 2026-01-02 --end 2026-02-02 --chunk-days 7
```

The default endpoint is `https://data.elexon.co.uk/bmrs/api/v1/datasets/{dataset}/stream`. The working API parameters discovered during Phase 1 are `settlementDateFrom`/`settlementDateTo` for `FUELHH` and `publishDateTimeFrom`/`publishDateTimeTo` for `WINDFOR`, plus `format=json` and `fuelType=WIND` for actuals. The script filters forecast records by target `startTime` after fetching the publication window, so forecasts published before 1 January can be retained without including out-of-scope delivery periods. A failed request, wrong-window response, or validation failure exits non-zero and does not create partial output.

The focused validation tests are in [tests/test_fetch_data.py](../tests/test_fetch_data.py) and can be run with:

```powershell
python -m unittest discover -s tests -v
```

### Verified full snapshot

The full download completed successfully on 28 October 2026:

- 1,488 actual rows, which is 31 delivery days × 48 half-hours;
- 18,080 in-scope forecast revisions;
- zero duplicate actual keys;
- zero duplicate forecast `(startTime, publishTime)` keys;
- zero missing actual half-hours;
- zero negative actual or forecast values.

The committed snapshot files are [data/actuals_wind.csv](../data/actuals_wind.csv), [data/forecasts_wind.csv](../data/forecasts_wind.csv), and [data/quality_report.json](../data/quality_report.json).

## Phase 2 analysis definitions

The pure analysis code is in [backend/app/analysis/wind.py](../backend/app/analysis/wind.py).

- **Decision window:** a delivery date from 2 January through 1 February 2026 represents a decision made on D-1 for a January 2026 purchase decision.
- **Cut-off:** `09:00 Europe/London` on D-1 by default. The cut-off is converted to UTC before comparing it with BMRS `publishTime`.
- **Forecast selection:** for each target half-hour, select the forecast with the greatest `publishTime` satisfying `publishTime <= cut-off`. Because WINDFOR is hourly, a target at `HH:30` uses the selected forecast for `HH:00`. A forecast after the cut-off is never eligible.
- **Missing forecasts:** returned as `status = "missing_forecast"` with null forecast, publish time, error, and horizon; they are not silently filled or included in accuracy metrics.
- **Error:** `forecast - actual`; positive error means over-forecast.
- **MAPE:** mean absolute percentage error for actual values greater than the near-zero guard (`1e-9 MW`); near-zero actuals are excluded from MAPE only.
- **Normalised MAE:** MAE divided by mean actual generation.
- **Percentiles:** linear-interpolated percentiles of absolute errors and positive errors.
- **Risk buffer:** the requested percentile of positive errors only. For example, a P95 buffer is the extra MW that covers 95% of observed over-forecast errors, subject to the selected records.
- **Horizon buckets:** `<6h`, `6-12h`, `12-24h`, `24-36h`, and `36h+`.
- **Wind levels:** low, medium, and high based on the one-third and two-thirds empirical quantiles of actual generation in the selected records.

The analysis tests are in [tests/test_wind_analysis.py](../tests/test_wind_analysis.py). They include hand-computed metric examples, an exact-at-cut-off selection case, a no-look-ahead leakage case, explicit missing forecasts, and risk-buffer percentiles.

## Phase 3 backend API

The FastAPI application is [backend/app/main.py](../backend/app/main.py). It loads the committed CSV snapshot once when the process starts and exposes:

- `GET /api/health`
- `GET /api/meta`
- `GET /api/decision?delivery_date=YYYY-MM-DD&cutoff=HH:MM`
- `GET /api/accuracy/summary?start=YYYY-MM-DD&end=YYYY-MM-DD`
- `GET /api/accuracy/by-hour`
- `GET /api/accuracy/by-horizon`
- `GET /api/accuracy/by-wind-level`
- `GET /api/accuracy/by-day`
- `GET /api/accuracy/error-distribution`
- `GET /api/risk-buffer?start=&end=&confidence=0.95`

Date ranges are validated against the available snapshot dates. The decision endpoint returns all 48 half-hours, including explicit missing-forecast statuses if a different cut-off creates gaps. Accuracy endpoints exclude missing forecasts from metric calculations. CORS is enabled for the future Vite frontend.

Install the backend dependencies from [backend/requirements.txt](../backend/requirements.txt) and run locally with:

```powershell
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.app.main:app --reload
```

Interactive API documentation is available at `http://localhost:8000/docs`.

The API tests are in [tests/test_api.py](../tests/test_api.py). Together with the ingestion and analysis tests, the full suite currently passes 17 tests.

## Phase 4 frontend

The React/Vite frontend is in [frontend/](../frontend/). It provides:

- **Decision view:** delivery-date and cut-off controls, forecast-versus-actual chart, headline decision cards, the 48-period table, and CSV export.
- **Accuracy dashboard:** MAE, RMSE, bias, P95 error, MAPE, normalised MAE, error-by-hour, error-by-horizon, and error-distribution charts.
- **Risk planning:** a P50–P99 confidence slider, overall positive-error buffer, and hourly buffer chart.
- Loading, empty/error states, responsive layout, plain-English metric explanations, and an About/Assumptions dialog.

For local development:

```powershell
cd frontend
npm install
npm run dev
```

The Vite development proxy sends `/api` requests to `http://localhost:8000`. Set `VITE_API_BASE` when the backend runs elsewhere. The production build is created with:

```powershell
npm run build
```

The production build completed successfully during Phase 4. Recharts is used for the charts; the generated `frontend/dist/` and `frontend/node_modules/` directories are excluded from source control.

## Phase 5 Docker

The container setup consists of:

- [backend/Dockerfile](../backend/Dockerfile): Python 3.13 slim image running Uvicorn and the committed snapshot.
- [frontend/Dockerfile](../frontend/Dockerfile): Node build stage followed by nginx serving the Vite bundle.
- [frontend/nginx.conf](../frontend/nginx.conf): serves the SPA and proxies `/api/` to the compose `backend` service.
- [docker-compose.yml](../docker-compose.yml): starts the backend with a health check, then the frontend on port 8080.
- [.dockerignore](../.dockerignore) and [.env.example](../.env.example).

From a machine with Docker Desktop or Docker Engine installed:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. The frontend container serves the UI and forwards API requests internally to `http://backend:8000`. To change the host port, copy [.env.example](../.env.example) to `.env` and set `FRONTEND_PORT`.

Docker validation could not be executed in this environment because the `docker` command is not installed. The frontend production build and the complete Python test suite were already validated outside the containers.

## Phase 6 Vercel deployment

The Vercel deployment files are:

- [vercel.json](../vercel.json): builds the Vite app from `frontend/`, publishes `frontend/dist`, rewrites `/api/*` to the Python function, and includes the backend code and committed snapshot in the function bundle.
- [api/index.py](../api/index.py): exports the FastAPI app for Vercel's Python runtime.
- [requirements.txt](../requirements.txt): root-level serverless dependency manifest.
- [.vercelignore](../.vercelignore): excludes local caches, tests, documentation, and generated frontend files from deployment.

The frontend uses a relative API base by default, so the deployed browser calls the same Vercel origin. The local Vite proxy remains available for development.

In the Vercel dashboard, import the repository with the project root as the root directory and deploy with the configuration in [vercel.json](../vercel.json). The expected build is equivalent to:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

After deployment, verify these URLs in an incognito window:

- `https://<deployment>/`
- `https://<deployment>/api/health`
- `https://<deployment>/api/docs`

This environment has no Vercel credentials or project URL, so a live deployment and end-to-end URL check could not be performed here. The local frontend build, FastAPI tests, and serverless import are validated instead.
