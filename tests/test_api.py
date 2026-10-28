import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertGreater(response.json()["actual_rows"], 0)

    def test_meta_contains_available_dates_and_cutoff(self) -> None:
        response = self.client.get("/api/meta")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["default_cutoff"], "09:00")
        self.assertEqual(len(body["available_dates"]), 31)

    def test_decision_returns_48_half_hours(self) -> None:
        response = self.client.get(
            "/api/decision",
            params={"delivery_date": "2026-01-02", "cutoff": "09:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["records"]), 48)
        self.assertTrue(all(row["status"] == "ok" for row in response.json()["records"]))

    def test_summary_has_metrics(self) -> None:
        response = self.client.get(
            "/api/accuracy/summary",
            params={"start": "2026-01-02", "end": "2026-01-03"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metrics"]["count"], 96)

    def test_risk_buffer_validates_confidence(self) -> None:
        response = self.client.get("/api/risk-buffer", params={"confidence": 1.5})
        self.assertEqual(response.status_code, 422)

    def test_invalid_date_range_is_clear(self) -> None:
        response = self.client.get(
            "/api/accuracy/summary",
            params={"start": "2026-01-10", "end": "2026-01-02"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("on or before", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
