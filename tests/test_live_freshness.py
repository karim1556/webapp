from contextlib import ExitStack
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import create_app


class LiveFreshnessTests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        directory = contexts.enter_context(tempfile.TemporaryDirectory())
        contexts.enter_context(patch("database.DB_PATH", str(Path(directory) / "test.db")))
        self.client = create_app().test_client()
        self.auth = contexts.enter_context(patch("app.session_record", return_value={
            "user_id": 1, "assigned_patients": None, "demo_patient_id": None,
        }))
        contexts.enter_context(patch("database.get_user_by_id", return_value={
            "id": 1, "username": "Patient", "email": "patient@example.test", "device_id": "C6525",
        }))

    def test_offline_source_returns_no_last_known_measurements(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=2)
        with patch("routes.dashboard._telemetry", return_value=[
            {"id": 1, "ts": old.isoformat(), "finger_detected": True, "heart_rate": 103},
        ]):
            data = self.client.get("/api/live").get_json()
        self.assertEqual(data["status"], "offline")
        self.assertEqual(data["samples"], [])
        self.assertNotIn("reading", data)

    def test_fresh_reconnection_does_not_send_prior_session_samples(self):
        now = datetime.now(timezone.utc)
        samples = [
            {"id": key, "ts": (now - timedelta(seconds=age)).isoformat()}
            for key, age in [(3, 1), (2, 4), (1, 90)]
        ]
        with patch("routes.dashboard._telemetry", return_value=samples):
            data = self.client.get("/api/live?limit=200").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertGreater(data["fresh_for_ms"], 0)
        self.assertEqual([row["id"] for row in data["samples"]], [2, 3])

    def test_live_pain_score_comes_only_from_the_forest(self):
        now = datetime.now(timezone.utc)
        samples = [
            {"id": 3, "ts": now.isoformat(), "finger_detected": True, "heart_rate": 90,
             "pain_index": 88, "vas_score": 8.8, "pain_status": "SEVERE"},
            {"id": 2, "ts": now.isoformat(), "finger_detected": False, "heart_rate": 90,
             "pain_index": 85},
        ]
        with patch("routes.dashboard._telemetry", return_value=samples), patch("ml.patient_forest.load_forest") as load:
            model = load.return_value[0]
            model.predict.return_value = [32.34]
            load.return_value = (model, ("heart_rate",))
            data = self.client.get("/api/live?limit=200").get_json()
        self.assertEqual(data["reading"]["pain_index"], 32.3)
        self.assertNotIn("vas_score", data["reading"])
        self.assertNotIn("pain_status", data["reading"])
        self.assertEqual([row["pain_index"] for row in data["samples"]], [None, 32.3])
        self.assertEqual(model.predict.call_args.args[0].to_dict("records"), [{"heart_rate": 90}])

    def test_archive_shows_placeholders_without_reading_measurements(self):
        self.auth.return_value["demo_patient_id"] = "P001"
        with patch("routes.dashboard._telemetry") as telemetry, patch("routes.dashboard._archive_profile") as profile:
            data = self.client.get("/api/live").get_json()
            html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(data["status"], "no_live_source")
        self.assertEqual(data["samples"], [])
        self.assertNotIn("reading", data)
        self.assertIn("Open Signal History", html)
        self.assertIn('data-live-source="false"', html)
        self.assertIn('data-field="finger_detected">—', html)
        self.assertIn('data-field="heart_rate">—', html)
        self.assertIn('id="painIndexGauge"', html)
        self.assertIn('No live readings', html)
        telemetry.assert_not_called()
        profile.assert_not_called()
