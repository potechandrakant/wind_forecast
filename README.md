# Windwise: UK wind forecast portal

Windwise is a decision-support prototype for a UK energy utility's day-ahead power-buying team. It shows the forecast that was available at the decision cut-off, compares it with actual wind generation, and turns historical forecast error into a transparent MW risk buffer.

The application uses only the Elexon BMRS `FUELHH` and `WINDFOR` datasets required by the challenge.

> **Submission status:** The live Vercel URL, anonymous public GitHub repository URL, and unlisted video URL are intentionally left as placeholders until the deployment and recording steps are completed.

## What the app provides

### Decision view

- Select a delivery date and decision cut-off.
- See the forecast selected at the D-1 cut-off versus actual wind generation.
- Review all 48 half-hour periods.
- See forecast publication time, error, and missing-forecast status.
- Export the decision table as CSV.

### Accuracy dashboard

- MAE, RMSE, bias, P95 absolute error, MAPE, and normalised MAE.
- Error by hour of day.
- Error by forecast horizon.
- Error distribution histogram.
- Date-range filtering.

### Risk planning

- Select a confidence level from P50 to P99.
- See the positive forecast-error buffer in MW.
- Compare buffer requirements by hour of day.
- Read plain-English explanations of the risk calculation and its limitations.

## Architecture

```text
Elexon BMRS API
       |
       v
scripts/fetch_data.py
       |
       v
Committed CSV snapshot under data/
       |
       +--> FastAPI analysis/API service
       |       |
       |       +--> /api/health
       |       +--> /api/meta
       |       +--> /api/decision
       |       +--> /api/accuracy/*
       |       +--> /api/risk-buffer
       |
       v
React + Vite frontend
       |
       v
Charts, tables, CSV export, assumptions panel
```

The deployed application reads the committed snapshot rather than calling Elexon at runtime. This avoids rate limits, API availability issues, and serverless cold-start dependency on the external service.

## Directory and file guide

| Path | Purpose |
| --- | --- |
| `backend/app/main.py` | FastAPI application and API endpoints |
| `backend/app/analysis/wind.py` | Pure forecast-selection, metrics, grouping, and risk-buffer logic |
| `backend/app/analysis/csv_loader.py` | Loads the committed CSV snapshot |
| `scripts/fetch_data.py` | Chunked BMRS downloader and data-quality validator |
| `data/actuals_wind.csv` | Committed FUELHH wind actuals |
| `data/forecasts_wind.csv` | Committed WINDFOR forecast revisions |
| `data/quality_report.json` | Snapshot validation report |
| `frontend/src/App.jsx` | React views and API integration |
| `frontend/src/styles.css` | Responsive visual design |
| `frontend/vite.config.js` | Vite configuration and local API proxy |
| `frontend/nginx.conf` | Production SPA serving and API proxy |
| `tests/test_fetch_data.py` | Ingestion validation tests |
| `tests/test_wind_analysis.py` | Pure analysis tests |
| `tests/test_api.py` | FastAPI endpoint tests |
| `docker-compose.yml` | Local multi-container orchestration |
| `vercel.json` | Vercel build, rewrite, and serverless bundle configuration |
| `api/index.py` | Vercel FastAPI entry point |
| `docs/DATA_NOTES.md` | Detailed data discovery, assumptions, and phase notes |

## Quick start with Docker

Docker Desktop or Docker Engine is required.

```powershell
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080).

The frontend container serves the React application and proxies `/api/` requests to the backend container. To use another host port, copy [.env.example](.env.example) to `.env` and set:

```text
FRONTEND_PORT=8080
```

Stop the stack with:

```powershell
docker compose down
```

## Local development

### Backend

Create or activate a Python environment, then install:

```powershell
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.app.main:app --reload
```

The API is available at [http://localhost:8000](http://localhost:8000), with interactive documentation at [http://localhost:8000/docs](http://localhost:8000/docs).

### Frontend

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal. The Vite proxy sends `/api` requests to `http://localhost:8000`. To use another backend URL:

```powershell
$env:VITE_API_BASE = "https://your-backend.example.com"
npm run dev
```

Build the production frontend with:

```powershell
npm run build
```

## Data and assumptions

### Scope

“Decisions made in January 2026” is interpreted as D-1 decisions for delivery dates from **2 January 2026 through 1 February 2026**. The first day is excluded because there is no preceding January decision day in the configured scope.

### Data sources

- Actuals: Elexon BMRS `FUELHH`, filtered to `fuelType == "WIND"`.
- Forecasts: Elexon BMRS `WINDFOR`, retaining forecast revisions by `(startTime, publishTime)`.
- Units: MW.
- Actual resolution: half-hourly.
- Downloaded forecast target resolution: hourly.

### Forecast selection

The default decision cut-off is **09:00 Europe/London on D-1**. The code converts this local time to UTC before comparing it with `publishTime`.

For each target period, the selected forecast is the latest revision satisfying:

```text
publishTime <= decision cut-off
```

Forecasts published after the cut-off are never used. This prevents look-ahead leakage.

Because the downloaded WINDFOR snapshot is hourly while actuals are half-hourly, the forecast for `HH:00` is repeated for `HH:30`. No interpolation is performed.

### Metrics

- `error = forecast - actual`
- Positive error means wind was over-forecast.
- MAE is the mean absolute error.
- RMSE penalises large errors more heavily.
- MAPE excludes near-zero actuals using a `1e-9 MW` guard.
- Normalised MAE is MAE divided by mean actual generation.
- P50/P90/P95 values use linear interpolation.
- Risk buffer is the requested percentile of positive errors only.
- Wind levels are empirical low/medium/high thirds of actual generation.

### Snapshot quality

The committed snapshot contains:

- 1,488 actual rows.
- 18,080 forecast revisions.
- 31 delivery days × 48 actual half-hours.
- Zero duplicate actual keys.
- Zero duplicate forecast `(startTime, publishTime)` keys.
- Zero missing actual half-hours.
- Zero negative actual or forecast values.

For the full data notes and API discovery details, see [docs/DATA_NOTES.md](docs/DATA_NOTES.md).

## Refreshing the snapshot

The downloader uses dataset-specific BMRS query parameters and seven-day chunks by default:

```powershell
python scripts\fetch_data.py
```

Useful options:

```powershell
python scripts\fetch_data.py `
  --start 2026-01-02 `
  --end 2026-02-02 `
  --forecast-publish-start 2025-12-01 `
  --chunk-days 7 `
  --output-dir data
```

The script validates every response and writes output only after the complete download passes validation. It exits non-zero instead of creating a partial or wrong-window snapshot.

## Testing

Run the complete Python test suite:

```powershell
python -m unittest discover -s tests -v
```

The tests cover:

- BMRS response canonicalisation and validation.
- Duplicate and missing half-hour detection.
- D-1 cut-off conversion.
- Exact-at-cut-off forecast selection.
- No-look-ahead leakage.
- Hourly-to-half-hour alignment.
- Missing forecast handling.
- MAE, RMSE, bias, MAPE, normalised MAE, percentiles, and risk buffers.
- API health, metadata, decision, summary, validation, and risk-buffer endpoints.

The current suite passes **17 tests**.

## Deployment

### Vercel

The intended deployment is Vercel:

1. Push this project to a public GitHub repository with an anonymous random-string name, as required by the challenge.
2. Import the repository into Vercel with the project root as the root directory.
3. Use the committed [vercel.json](vercel.json).
4. Confirm the build succeeds.
5. Test the deployed frontend and API in an incognito browser window.

The serverless API entry point is [api/index.py](api/index.py). The frontend is built as a static Vite application and `/api/*` is rewritten to the FastAPI function.

**Live app:** `TODO: add deployed Vercel URL`

Verify at least:

```text
https://<deployment>/ 
https://<deployment>/api/health
https://<deployment>/api/docs
```

### Docker fallback

The Docker stack in [docker-compose.yml](docker-compose.yml) is the local/deployment fallback if Vercel’s Python bundle constraints prevent the serverless deployment.

## AI tools disclosure

AI tools were used for this project. The AI assistant using Copilot SDK in VS Code helped with:

- Inspecting the BMRS API contract and building the Phase 0 data notes.
- Implementing the chunked ingestion and validation script.
- Implementing and testing the pure forecast-selection and metrics logic.
- Wiring the FastAPI endpoints.
- Building the React/Vite dashboard and Recharts visualisations.
- Creating Docker and Vercel deployment configuration.
- Drafting and updating this documentation.

The implementation was reviewed through focused tests, a full snapshot run, API smoke tests, and a production frontend build. The developer remains responsible for validating the final deployment, repository access, and submission materials.

## Limitations and future work

- The cut-off is configurable but currently defaults to 09:00 Europe/London; the production exchange rule should be confirmed with the client.
- Forecast availability and historical accuracy are measured from the committed snapshot, not live API data.
- The risk buffer is statistical and does not model prices, imbalance charges, correlated half-hours, storage, transmission constraints, or portfolio diversification.
- The current wind-level grouping uses empirical thirds and should be replaced with a client-approved operational definition if needed.
- The frontend bundle can be code-split further if the dashboard grows.
- Authentication, role-based access, audit logging, and scheduled snapshot refresh are not included in this prototype.
- A production deployment should add monitoring, structured logs, retry policies, and data freshness alerts.

## Submission checklist

- [ ] Public GitHub repository with an anonymous random-string name.
- [ ] README complete.
- [ ] Vercel URL deployed and tested.
- [ ] `/api/health` and `/api/docs` tested in incognito mode.
- [ ] Unlisted YouTube video recorded, maximum 10 minutes.
- [ ] Submission form completed: <https://forms.cloud.microsoft/r/FTX6K2bbnU>.
- [ ] Every URL opened in incognito mode before submission.

