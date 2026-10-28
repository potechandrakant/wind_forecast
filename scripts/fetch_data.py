"""Download and validate the January 2026 wind data snapshot.

The script deliberately uses only the Python standard library so it can be
run before the application dependencies are installed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/{dataset}/stream"
DEFAULT_START = date(2026, 1, 2)
DEFAULT_END = date(2026, 2, 2)
DEFAULT_FORECAST_PUBLISH_START = date(2025, 12, 1)
UTC = timezone.utc


@dataclass(frozen=True)
class FetchConfig:
    base_url: str = DEFAULT_BASE_URL
    start: date = DEFAULT_START
    end: date = DEFAULT_END
    forecast_publish_start: date = DEFAULT_FORECAST_PUBLISH_START
    chunk_days: int = 7
    timeout_seconds: int = 30


def parse_iso_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone: {value!r}")
    return parsed.astimezone(UTC)


def parse_generation(value: Any, field_name: str = "generation") -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite: {value!r}")
    return parsed


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    """Accept the documented list shape and common wrapper shapes."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", payload.get("results", payload.get("items")))
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("API response did not contain a list of row objects")
    return rows


def request_chunk(
    *,
    dataset: str,
    start: date,
    end: date | datetime,
    config: FetchConfig,
    extra_params: dict[str, str] | None = None,
    expected_start: date | None = None,
    expected_end: date | None = None,
) -> list[dict[str, Any]]:
    if dataset == "FUELHH":
        query = {
            "settlementDateFrom": start.isoformat(),
            "settlementDateTo": (end - timedelta(days=1)).isoformat(),
        }
    elif dataset == "WINDFOR":
        query = {
            "publishDateTimeFrom": format_query_time(start),
            "publishDateTimeTo": format_query_time(end),
        }
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")
    query["format"] = "json"
    if extra_params:
        query.update(extra_params)
    url = config.base_url.format(dataset=dataset)
    request = Request(f"{url}?{urlencode(query)}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Could not fetch {dataset} for {start} through {end}: {exc}"
        ) from exc
    rows = extract_rows(payload)
    if expected_start is not None and expected_end is not None:
        expected_start_time = datetime.combine(expected_start, datetime.min.time(), tzinfo=UTC)
        expected_end_time = datetime.combine(expected_end, datetime.min.time(), tzinfo=UTC)
        outside_window = []
        for row in rows:
            timestamp = parse_iso_timestamp(row.get("startTime"), "startTime")
            if timestamp < expected_start_time or timestamp >= expected_end_time:
                outside_window.append(row.get("startTime"))
        if outside_window:
            raise RuntimeError(
                f"{dataset} returned rows outside requested window "
                f"[{expected_start_time.isoformat()}, {expected_end_time.isoformat()}): "
                f"{outside_window[:5]}"
            )
    return rows


def format_query_time(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    return f"{value.isoformat()}T00:00Z"


def iter_chunks(start: date, end: date, chunk_days: int) -> Iterable[tuple[date, date]]:
    if end <= start:
        raise ValueError("end must be after start")
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    current = start
    while current < end:
        next_start = min(current + timedelta(days=chunk_days), end)
        yield current, next_start
        current = next_start


def canonical_actuals(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for row in rows:
        if row.get("fuelType") != "WIND":
            continue
        start_time = parse_iso_timestamp(row.get("startTime"), "startTime")
        generation = parse_generation(row.get("generation"))
        canonical.append(
            {
                "startTime": start_time.isoformat().replace("+00:00", "Z"),
                "generation": generation,
                "fuelType": "WIND",
            }
        )
    return canonical


def canonical_forecasts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for row in rows:
        start_time = parse_iso_timestamp(row.get("startTime"), "startTime")
        publish_time = parse_iso_timestamp(row.get("publishTime"), "publishTime")
        generation = parse_generation(row.get("generation"))
        canonical.append(
            {
                "startTime": start_time.isoformat().replace("+00:00", "Z"),
                "publishTime": publish_time.isoformat().replace("+00:00", "Z"),
                "generation": generation,
            }
        )
    return canonical


def duplicate_keys(rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> list[tuple[Any, ...]]:
    seen: set[tuple[Any, ...]] = set()
    duplicates: list[tuple[Any, ...]] = []
    for row in rows:
        key = tuple(row[field] for field in fields)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates


def missing_half_hours(rows: Iterable[dict[str, Any]]) -> list[str]:
    timestamps = sorted(
        parse_iso_timestamp(row["startTime"], "startTime") for row in rows
    )
    if not timestamps:
        return []
    expected = set()
    current = timestamps[0]
    while current <= timestamps[-1]:
        expected.add(current)
        current += timedelta(minutes=30)
    return [value.isoformat().replace("+00:00", "Z") for value in sorted(expected - set(timestamps))]


def validate_rows(
    actuals: list[dict[str, Any]], forecasts: list[dict[str, Any]]
) -> dict[str, Any]:
    actual_duplicates = duplicate_keys(actuals, ("startTime",))
    forecast_duplicates = duplicate_keys(forecasts, ("startTime", "publishTime"))
    actual_missing = missing_half_hours(actuals)
    negative_actuals = [row["startTime"] for row in actuals if row["generation"] < 0]
    negative_forecasts = [row["startTime"] for row in forecasts if row["generation"] < 0]
    report = {
        "actual_rows": len(actuals),
        "forecast_rows": len(forecasts),
        "actual_duplicate_keys": len(actual_duplicates),
        "forecast_duplicate_keys": len(forecast_duplicates),
        "missing_actual_half_hours": len(actual_missing),
        "negative_actual_rows": len(negative_actuals),
        "negative_forecast_rows": len(negative_forecasts),
        "actual_duplicate_examples": actual_duplicates[:5],
        "forecast_duplicate_examples": forecast_duplicates[:5],
        "missing_actual_examples": actual_missing[:5],
        "negative_actual_examples": negative_actuals[:5],
        "negative_forecast_examples": negative_forecasts[:5],
    }
    if any(
        report[key]
        for key in (
            "actual_duplicate_keys",
            "forecast_duplicate_keys",
            "missing_actual_half_hours",
            "negative_actual_rows",
            "negative_forecast_rows",
        )
    ):
        raise ValueError("Data-quality validation failed: " + json.dumps(report))
    return report


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_snapshot(config: FetchConfig, output_dir: Path) -> dict[str, Any]:
    actual_rows: list[dict[str, Any]] = []
    forecast_rows: list[dict[str, Any]] = []
    for chunk_start, chunk_end in iter_chunks(config.start, config.end, config.chunk_days):
        actual_rows.extend(
            request_chunk(
                dataset="FUELHH",
                start=chunk_start,
                end=chunk_end,
                config=config,
                extra_params={"fuelType": "WIND"},
                expected_start=config.start,
                expected_end=config.end,
            )
        )
    for chunk_start, chunk_end in iter_chunks(
        config.forecast_publish_start, config.end, config.chunk_days
    ):
        forecast_rows.extend(
            request_chunk(dataset="WINDFOR", start=chunk_start, end=chunk_end, config=config)
        )

    actuals = canonical_actuals(actual_rows)
    forecasts = [
        row
        for row in canonical_forecasts(forecast_rows)
        if config.start.isoformat() <= row["startTime"][:10] < config.end.isoformat()
    ]
    report = validate_rows(actuals, forecasts)
    write_csv(output_dir / "actuals_wind.csv", actuals, ["startTime", "generation", "fuelType"])
    write_csv(output_dir / "forecasts_wind.csv", forecasts, ["startTime", "publishTime", "generation"])
    report["scope_start"] = config.start.isoformat()
    report["scope_end_exclusive"] = config.end.isoformat()
    report["forecast_publish_start"] = config.forecast_publish_start.isoformat()
    (output_dir / "quality_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument(
        "--forecast-publish-start",
        type=date.fromisoformat,
        default=DEFAULT_FORECAST_PUBLISH_START,
    )
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = FetchConfig(
        base_url=args.base_url,
        start=args.start,
        end=args.end,
        forecast_publish_start=args.forecast_publish_start,
        chunk_days=args.chunk_days,
        timeout_seconds=args.timeout,
    )
    try:
        report = fetch_snapshot(config, args.output_dir)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
