import math
import unittest
from datetime import date, datetime, timezone

from backend.app.analysis.wind import (
    DecisionRecord,
    compute_metrics,
    decision_cutoff,
    risk_buffer,
    select_decision_forecasts,
)


UTC = timezone.utc


def actual(start_time: str, generation: float) -> dict[str, object]:
    return {"startTime": start_time, "generation": generation, "fuelType": "WIND"}


def forecast(start_time: str, publish_time: str, generation: float) -> dict[str, object]:
    return {
        "startTime": start_time,
        "publishTime": publish_time,
        "generation": generation,
    }


class WindAnalysisTests(unittest.TestCase):
    def test_decision_cutoff_converts_d1_london_time_to_utc(self) -> None:
        self.assertEqual(
            decision_cutoff(date(2026, 1, 2)),
            datetime(2026, 1, 1, 9, tzinfo=UTC),
        )

    def test_selects_latest_forecast_at_or_before_cutoff(self) -> None:
        target = "2026-01-02T00:00:00Z"
        records = select_decision_forecasts(
            [
                forecast(target, "2026-01-01T08:00:00Z", 100),
                forecast(target, "2026-01-01T09:00:00Z", 110),
                forecast(target, "2026-01-01T09:01:00Z", 999),
            ],
            [actual(target, 105)],
            date(2026, 1, 2),
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].forecast, 110)
        self.assertEqual(records[0].publish_time, datetime(2026, 1, 1, 9, tzinfo=UTC))
        self.assertEqual(records[0].error, 5)

    def test_forecast_after_cutoff_cannot_leak_into_decision(self) -> None:
        target = "2026-01-02T00:30:00Z"
        records = select_decision_forecasts(
            [forecast(target, "2026-01-01T09:01:00Z", 999)],
            [actual(target, 1000)],
            date(2026, 1, 2),
        )
        self.assertEqual(records[0].status, "missing_forecast")
        self.assertIsNone(records[0].forecast)

    def test_missing_forecast_is_explicit(self) -> None:
        records = select_decision_forecasts(
            [],
            [actual("2026-01-02T00:00:00Z", 100)],
            date(2026, 1, 2),
        )
        self.assertEqual(records[0].status, "missing_forecast")
        self.assertIsNone(records[0].error)

    def test_hourly_forecast_is_repeated_for_second_half_hour(self) -> None:
        target = "2026-01-02T00:30:00Z"
        records = select_decision_forecasts(
            [forecast("2026-01-02T00:00:00Z", "2026-01-01T08:00:00Z", 100)],
            [actual(target, 90)],
            date(2026, 1, 2),
        )
        self.assertEqual(records[0].status, "ok")
        self.assertEqual(records[0].forecast, 100)
        self.assertEqual(records[0].error, 10)

    def test_metrics_and_percentiles(self) -> None:
        records = [
            DecisionRecord(
                datetime(2026, 1, 2, 0, tzinfo=UTC),
                10,
                datetime(2026, 1, 1, 0, tzinfo=UTC),
                9,
                1,
                24,
                "ok",
            ),
            DecisionRecord(
                datetime(2026, 1, 2, 0, 30, tzinfo=UTC),
                6,
                datetime(2026, 1, 1, 0, tzinfo=UTC),
                8,
                -2,
                23.5,
                "ok",
            ),
            DecisionRecord(
                datetime(2026, 1, 2, 1, tzinfo=UTC),
                10,
                datetime(2026, 1, 1, 0, tzinfo=UTC),
                7,
                3,
                23,
                "ok",
            ),
        ]
        metrics = compute_metrics(records)
        self.assertAlmostEqual(metrics["bias_mw"], 2 / 3)
        self.assertEqual(metrics["mae_mw"], 2)
        self.assertAlmostEqual(metrics["rmse_mw"], math.sqrt(14 / 3))
        self.assertEqual(metrics["absolute_error_p50_mw"], 2)
        self.assertEqual(metrics["overforecast_p50_mw"], 2)
        self.assertAlmostEqual(metrics["normalised_mae"], 2 / 8)

    def test_mape_ignores_near_zero_actuals(self) -> None:
        records = [
            DecisionRecord(
                datetime(2026, 1, 2, tzinfo=UTC), 5, datetime(2026, 1, 1, tzinfo=UTC),
                0, 5, 24, "ok",
            ),
            DecisionRecord(
                datetime(2026, 1, 2, 0, 30, tzinfo=UTC), 12,
                datetime(2026, 1, 1, tzinfo=UTC), 10, 2, 23.5, "ok",
            ),
        ]
        self.assertEqual(compute_metrics(records)["mape_percent"], 20)

    def test_risk_buffer_is_percentile_of_positive_errors(self) -> None:
        records = [
            DecisionRecord(datetime(2026, 1, 2, tzinfo=UTC), 11, None, 10, 1, None, "ok"),
            DecisionRecord(datetime(2026, 1, 2, 0, 30, tzinfo=UTC), 8, None, 10, -2, None, "ok"),
            DecisionRecord(datetime(2026, 1, 2, 1, tzinfo=UTC), 13, None, 10, 3, None, "ok"),
        ]
        self.assertEqual(risk_buffer(records, 0.5), 2)
        self.assertAlmostEqual(risk_buffer(records, 0.95), 2.9)


if __name__ == "__main__":
    unittest.main()
