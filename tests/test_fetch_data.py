import unittest

from scripts.fetch_data import (
    canonical_actuals,
    canonical_forecasts,
    missing_half_hours,
    validate_rows,
)


class FetchDataTests(unittest.TestCase):
    def test_actuals_filter_and_canonicalise_timestamps(self) -> None:
        rows = canonical_actuals(
            [
                {
                    "fuelType": "WIND",
                    "startTime": "2026-01-02T00:00:00+00:00",
                    "generation": "10.5",
                },
                {
                    "fuelType": "GAS",
                    "startTime": "2026-01-02T00:30:00Z",
                    "generation": 99,
                },
            ]
        )
        self.assertEqual(rows, [{
            "startTime": "2026-01-02T00:00:00Z",
            "generation": 10.5,
            "fuelType": "WIND",
        }])

    def test_missing_half_hour_is_reported(self) -> None:
        rows = [
            {"startTime": "2026-01-02T00:00:00Z", "generation": 1, "fuelType": "WIND"},
            {"startTime": "2026-01-02T01:00:00Z", "generation": 1, "fuelType": "WIND"},
        ]
        self.assertEqual(missing_half_hours(rows), ["2026-01-02T00:30:00Z"])

    def test_forecast_revision_key_is_start_and_publish_time(self) -> None:
        rows = canonical_forecasts(
            [
                {
                    "startTime": "2026-01-02T00:00:00Z",
                    "publishTime": "2026-01-01T08:00:00Z",
                    "generation": 10,
                },
                {
                    "startTime": "2026-01-02T00:00:00Z",
                    "publishTime": "2026-01-01T09:00:00Z",
                    "generation": 12,
                },
            ]
        )
        self.assertEqual(validate_rows(
            [
                {
                    "startTime": "2026-01-02T00:00:00Z",
                    "generation": 11,
                    "fuelType": "WIND",
                }
            ],
            rows,
        )["forecast_rows"], 2)


if __name__ == "__main__":
    unittest.main()
