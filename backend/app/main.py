"""FastAPI application for the UK wind forecast portal."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .analysis.csv_loader import load_snapshot
from .analysis.wind import (
    DEFAULT_CUTOFF,
    DEFAULT_TIMEZONE,
    DecisionRecord,
    by_day,
    by_hour,
    by_horizon,
    by_wind_level,
    compute_metrics,
    decision_cutoff,
    parse_timestamp,
    risk_buffer,
    select_decision_forecasts,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"


class MetaResponse(BaseModel):
    available_dates: list[date]
    default_cutoff: str
    timezone: str
    actual_rows: int
    forecast_rows: int
    forecast_publish_start: datetime
    forecast_publish_end: datetime


class DecisionResponse(BaseModel):
    delivery_date: date
    cutoff: str
    cutoff_utc: datetime
    records: list[dict[str, Any]]


class AccuracyResponse(BaseModel):
    start: date
    end: date
    metrics: dict[str, float | int | None]


class GroupedAccuracyResponse(BaseModel):
    start: date
    end: date
    groups: dict[str, dict[str, float | int | None]]


class HistogramResponse(BaseModel):
    start: date
    end: date
    bins: list[dict[str, float | int]]


class RiskBufferResponse(BaseModel):
    start: date
    end: date
    confidence: float
    buffer_mw: float | None
    by_hour: dict[str, float | None]


def _load_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        return load_snapshot(DATA_DIR)
    except OSError as exc:
        raise RuntimeError(f"Unable to load committed data snapshot from {DATA_DIR}") from exc


ACTUALS, FORECASTS = _load_data()
AVAILABLE_DATES = sorted(
    {
        parse_timestamp(row["startTime"]).date()
        for row in ACTUALS
    }
)


def _parse_date_range(start: date | None, end: date | None) -> tuple[date, date]:
    resolved_start = start or AVAILABLE_DATES[0]
    resolved_end = end or AVAILABLE_DATES[-1]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if resolved_start < AVAILABLE_DATES[0] or resolved_end > AVAILABLE_DATES[-1]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Date range must be between {AVAILABLE_DATES[0].isoformat()} "
                f"and {AVAILABLE_DATES[-1].isoformat()}"
            ),
        )
    return resolved_start, resolved_end


def _records_for_date(delivery_date: date, cutoff: str) -> list[DecisionRecord]:
    return select_decision_forecasts(
        FORECASTS,
        ACTUALS,
        delivery_date,
        cutoff=cutoff,
        timezone_name=DEFAULT_TIMEZONE,
    )


@lru_cache(maxsize=64)
def _records_for_range(start: date, end: date, cutoff: str = DEFAULT_CUTOFF) -> tuple[DecisionRecord, ...]:
    records: list[DecisionRecord] = []
    current = start
    while current <= end:
        records.extend(_records_for_date(current, cutoff))
        current += timedelta(days=1)
    return tuple(records)


def _errors(records: list[DecisionRecord]) -> list[float]:
    return [
        record.error
        for record in records
        if record.status == "ok" and record.error is not None
    ]


def _histogram(records: list[DecisionRecord], bin_count: int = 20) -> list[dict[str, float | int]]:
    errors = _errors(records)
    if not errors:
        return []
    minimum, maximum = min(errors), max(errors)
    if minimum == maximum:
        return [{"lower_mw": minimum, "upper_mw": maximum, "count": len(errors)}]
    width = (maximum - minimum) / bin_count
    counts = [0] * bin_count
    for error in errors:
        index = min(int((error - minimum) / width), bin_count - 1)
        counts[index] += 1
    return [
        {
            "lower_mw": minimum + index * width,
            "upper_mw": minimum + (index + 1) * width,
            "count": counts[index],
        }
        for index in range(bin_count)
    ]


app = FastAPI(
    title="UK Wind Forecast Portal API",
    version="1.0.0",
    description="Decision-time wind forecast selection and accuracy metrics for January 2026.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "actual_rows": len(ACTUALS), "forecast_rows": len(FORECASTS)}


@app.get("/api/meta", response_model=MetaResponse)
def meta() -> MetaResponse:
    publish_times = [
        datetime.fromisoformat(row["publishTime"].replace("Z", "+00:00"))
        for row in FORECASTS
    ]
    return MetaResponse(
        available_dates=AVAILABLE_DATES,
        default_cutoff=DEFAULT_CUTOFF,
        timezone=DEFAULT_TIMEZONE,
        actual_rows=len(ACTUALS),
        forecast_rows=len(FORECASTS),
        forecast_publish_start=min(publish_times),
        forecast_publish_end=max(publish_times),
    )


@app.get("/api/decision", response_model=DecisionResponse)
def decision(
    delivery_date: date = Query(...),
    cutoff: str = Query(DEFAULT_CUTOFF, pattern=r"^\d{2}:\d{2}$"),
) -> DecisionResponse:
    if delivery_date not in AVAILABLE_DATES:
        raise HTTPException(status_code=404, detail="delivery_date is not available")
    try:
        cutoff_utc = decision_cutoff(delivery_date, cutoff, DEFAULT_TIMEZONE)
        records = _records_for_date(delivery_date, cutoff)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DecisionResponse(
        delivery_date=delivery_date,
        cutoff=cutoff,
        cutoff_utc=cutoff_utc,
        records=[record.as_dict() for record in records],
    )


def _accuracy_range(
    start: date | None,
    end: date | None,
) -> tuple[date, date, list[DecisionRecord]]:
    resolved_start, resolved_end = _parse_date_range(start, end)
    return (
        resolved_start,
        resolved_end,
        list(_records_for_range(resolved_start, resolved_end)),
    )


@app.get("/api/accuracy/summary", response_model=AccuracyResponse)
def accuracy_summary(
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> AccuracyResponse:
    resolved_start, resolved_end, records = _accuracy_range(start, end)
    return AccuracyResponse(
        start=resolved_start,
        end=resolved_end,
        metrics=compute_metrics(records),
    )


def grouped_accuracy(
    start: date | None,
    end: date | None,
    grouper: Any,
) -> GroupedAccuracyResponse:
    resolved_start, resolved_end, records = _accuracy_range(start, end)
    return GroupedAccuracyResponse(
        start=resolved_start,
        end=resolved_end,
        groups=grouper(records),
    )


@app.get("/api/accuracy/by-hour", response_model=GroupedAccuracyResponse)
def accuracy_by_hour(start: date | None = Query(None), end: date | None = Query(None)) -> GroupedAccuracyResponse:
    return grouped_accuracy(start, end, by_hour)


@app.get("/api/accuracy/by-horizon", response_model=GroupedAccuracyResponse)
def accuracy_by_horizon(start: date | None = Query(None), end: date | None = Query(None)) -> GroupedAccuracyResponse:
    return grouped_accuracy(start, end, by_horizon)


@app.get("/api/accuracy/by-wind-level", response_model=GroupedAccuracyResponse)
def accuracy_by_wind_level(start: date | None = Query(None), end: date | None = Query(None)) -> GroupedAccuracyResponse:
    return grouped_accuracy(start, end, by_wind_level)


@app.get("/api/accuracy/by-day", response_model=GroupedAccuracyResponse)
def accuracy_by_delivery_day(start: date | None = Query(None), end: date | None = Query(None)) -> GroupedAccuracyResponse:
    return grouped_accuracy(start, end, by_day)


@app.get("/api/accuracy/error-distribution", response_model=HistogramResponse)
def error_distribution(
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> HistogramResponse:
    resolved_start, resolved_end, records = _accuracy_range(start, end)
    return HistogramResponse(
        start=resolved_start,
        end=resolved_end,
        bins=_histogram(records),
    )


@app.get("/api/risk-buffer", response_model=RiskBufferResponse)
def risk_buffer_endpoint(
    start: date | None = Query(None),
    end: date | None = Query(None),
    confidence: float = Query(0.95, ge=0, le=1),
) -> RiskBufferResponse:
    resolved_start, resolved_end, records = _accuracy_range(start, end)
    records_by_hour: dict[str, list[DecisionRecord]] = {}
    for record in records:
        records_by_hour.setdefault(f"{record.target_time.hour:02d}:00", []).append(record)
    hourly_buffers: dict[str, float | None] = {}
    for hour, hour_records in sorted(records_by_hour.items()):
        hourly_buffers[hour] = risk_buffer(hour_records, confidence)
    return RiskBufferResponse(
        start=resolved_start,
        end=resolved_end,
        confidence=confidence,
        buffer_mw=risk_buffer(records, confidence),
        by_hour=hourly_buffers,
    )
