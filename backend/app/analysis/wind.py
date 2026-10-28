"""Decision-time forecast selection and forecast accuracy metrics."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


UTC = timezone.utc
DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_CUTOFF = "09:00"
NEAR_ZERO_ACTUAL_MW = 1e-9


@dataclass(frozen=True)
class DecisionRecord:
    target_time: datetime
    forecast: float | None
    publish_time: datetime | None
    actual: float | None
    error: float | None
    horizon_hours: float | None
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "startTime": self.target_time.isoformat().replace("+00:00", "Z"),
            "forecast": self.forecast,
            "publishTime": (
                self.publish_time.isoformat().replace("+00:00", "Z")
                if self.publish_time
                else None
            ),
            "actual": self.actual,
            "error": self.error,
            "horizonHours": self.horizon_hours,
            "status": self.status,
        }


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value!r}")
    return parsed.astimezone(UTC)


def parse_cutoff(cutoff: str) -> time:
    try:
        hour_text, minute_text = cutoff.split(":", maxsplit=1)
        parsed = time(int(hour_text), int(minute_text))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"cutoff must be HH:MM, got {cutoff!r}") from exc
    return parsed


def decision_cutoff(
    delivery_date: date,
    cutoff: str = DEFAULT_CUTOFF,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    """Return the D-1 cut-off as an aware UTC datetime."""
    local_zone = ZoneInfo(timezone_name)
    cutoff_local = datetime.combine(
        delivery_date - timedelta(days=1),
        parse_cutoff(cutoff),
        tzinfo=local_zone,
    )
    return cutoff_local.astimezone(UTC)


def _row_generation(row: Mapping[str, Any]) -> float:
    value = row.get("generation")
    if value is None:
        raise ValueError(f"Row has no generation: {row!r}")
    try:
        generation = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Generation must be numeric: {value!r}") from exc
    if not math.isfinite(generation):
        raise ValueError(f"Generation must be finite: {value!r}")
    return generation


def select_decision_forecasts(
    forecasts: Iterable[Mapping[str, Any]],
    actuals: Iterable[Mapping[str, Any]],
    delivery_date: date,
    cutoff: str = DEFAULT_CUTOFF,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> list[DecisionRecord]:
    """Select the latest pre-cut-off forecast for every actual target period."""
    cutoff_utc = decision_cutoff(delivery_date, cutoff, timezone_name)
    local_zone = ZoneInfo(timezone_name)
    actual_by_target = {
        parse_timestamp(row["startTime"]): _row_generation(row)
        for row in actuals
        if parse_timestamp(row["startTime"]).astimezone(local_zone).date() == delivery_date
    }
    forecasts_by_target: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for row in forecasts:
        target_time = parse_timestamp(row["startTime"])
        publish_time = parse_timestamp(row["publishTime"])
        has_matching_actual = (
            target_time in actual_by_target
            or target_time + timedelta(minutes=30) in actual_by_target
        )
        if has_matching_actual and publish_time <= cutoff_utc:
            forecasts_by_target[target_time].append(
                (publish_time, _row_generation(row))
            )

    records: list[DecisionRecord] = []
    for target_time in sorted(actual_by_target):
        # WINDFOR is hourly in this snapshot. Repeat the preceding hourly
        # forecast for the second half-hour rather than interpolating it.
        forecast_target = target_time.replace(minute=0) if target_time.minute == 30 else target_time
        candidates = forecasts_by_target.get(forecast_target, [])
        if not candidates:
            records.append(
                DecisionRecord(
                    target_time=target_time,
                    forecast=None,
                    publish_time=None,
                    actual=actual_by_target[target_time],
                    error=None,
                    horizon_hours=None,
                    status="missing_forecast",
                )
            )
            continue
        publish_time, forecast = max(candidates, key=lambda item: item[0])
        actual = actual_by_target[target_time]
        records.append(
            DecisionRecord(
                target_time=target_time,
                forecast=forecast,
                publish_time=publish_time,
                actual=actual,
                error=forecast - actual,
                horizon_hours=(target_time - publish_time).total_seconds() / 3600,
                status="ok",
            )
        )
    return records


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentile <= 1:
        raise ValueError("percentile must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def compute_metrics(
    records: Iterable[DecisionRecord],
    near_zero_actual_mw: float = NEAR_ZERO_ACTUAL_MW,
) -> dict[str, float | int | None]:
    records = list(records)
    usable = [
        record
        for record in records
        if record.status == "ok" and record.error is not None and record.actual is not None
    ]
    errors = [record.error for record in usable if record.error is not None]
    absolute_errors = [abs(error) for error in errors]
    positive_errors = [error for error in errors if error > 0]
    actuals = [record.actual for record in usable if record.actual is not None]
    mape_values = [
        abs(record.error) / abs(record.actual) * 100
        for record in usable
        if record.error is not None
        and record.actual is not None
        and abs(record.actual) > near_zero_actual_mw
    ]
    mean_actual = mean(actuals) if actuals else None
    mae = mean(absolute_errors) if absolute_errors else None
    return {
        "count": len(usable),
        "missing_forecast_count": sum(record.status == "missing_forecast" for record in records),
        "bias_mw": mean(errors) if errors else None,
        "mae_mw": mae,
        "rmse_mw": math.sqrt(mean(error * error for error in errors)) if errors else None,
        "mape_percent": mean(mape_values) if mape_values else None,
        "normalised_mae": mae / mean_actual if mae is not None and mean_actual else None,
        "absolute_error_p50_mw": _percentile(absolute_errors, 0.50),
        "absolute_error_p90_mw": _percentile(absolute_errors, 0.90),
        "absolute_error_p95_mw": _percentile(absolute_errors, 0.95),
        "overforecast_p50_mw": _percentile(positive_errors, 0.50),
        "overforecast_p90_mw": _percentile(positive_errors, 0.90),
        "overforecast_p95_mw": _percentile(positive_errors, 0.95),
    }


def risk_buffer(
    records: Iterable[DecisionRecord],
    confidence: float,
) -> float | None:
    """Return the confidence percentile of positive forecast errors in MW."""
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    positive_errors = [
        record.error
        for record in records
        if record.status == "ok" and record.error is not None and record.error > 0
    ]
    return _percentile(positive_errors, confidence)


def _bucket_horizon(hours: float) -> str:
    if hours < 6:
        return "<6h"
    if hours < 12:
        return "6-12h"
    if hours < 24:
        return "12-24h"
    if hours < 36:
        return "24-36h"
    return "36h+"


def _group_metrics(
    records: Iterable[DecisionRecord],
    key_function: Any,
) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[DecisionRecord]] = defaultdict(list)
    for record in records:
        if record.status == "ok":
            groups[str(key_function(record))].append(record)
    return {key: compute_metrics(group) for key, group in sorted(groups.items())}


def by_hour(records: Iterable[DecisionRecord]) -> dict[str, dict[str, float | int | None]]:
    return _group_metrics(records, lambda record: f"{record.target_time.hour:02d}:00")


def by_horizon(records: Iterable[DecisionRecord]) -> dict[str, dict[str, float | int | None]]:
    return _group_metrics(records, lambda record: _bucket_horizon(record.horizon_hours or 0))


def by_day(records: Iterable[DecisionRecord]) -> dict[str, dict[str, float | int | None]]:
    return _group_metrics(
        records,
        lambda record: record.target_time.date().isoformat(),
    )


def wind_level_thresholds(records: Iterable[DecisionRecord]) -> tuple[float, float]:
    actuals = [
        record.actual
        for record in records
        if record.status == "ok" and record.actual is not None
    ]
    low = _percentile(actuals, 1 / 3)
    high = _percentile(actuals, 2 / 3)
    if low is None or high is None:
        raise ValueError("At least one usable record is required for wind levels")
    return low, high


def by_wind_level(
    records: Iterable[DecisionRecord],
) -> dict[str, dict[str, float | int | None]]:
    materialised = list(records)
    low, high = wind_level_thresholds(materialised)

    def level(record: DecisionRecord) -> str:
        if record.actual is None:
            return "unknown"
        if record.actual <= low:
            return "low"
        if record.actual <= high:
            return "medium"
        return "high"

    return _group_metrics(materialised, level)
