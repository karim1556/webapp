from contextlib import ExitStack
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import create_app


class LiveFreshnessTests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.client = create_app().test_client()
        session = {"user_id": 1, "access_token": "test-token"}
        contexts.enter_context(patch("app.session_record", return_value=session))
        contexts.enter_context(patch("routes.dashboard.session_record", return_value=session))
        self.user = contexts.enter_context(patch("database.get_user_by_id", return_value={
            "id": 1, "username": "Patient", "email": "patient@example.test", "device_id": None,
        }))

    def test_offline_source_returns_no_last_known_measurements(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=2)
        with patch("routes.dashboard.telemetry", return_value=[
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
        with patch("routes.dashboard.telemetry", return_value=samples):
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
        with patch("routes.dashboard.telemetry", return_value=samples), patch("ml.patient_forest.load_forest") as load:
            model = load.return_value[0]
            model.predict.return_value = [32.34]
            load.return_value = (model, ("heart_rate",))
            data = self.client.get("/api/live?limit=200").get_json()
        self.assertEqual(data["reading"]["pain_index"], 32.3)
        self.assertNotIn("vas_score", data["reading"])
        self.assertNotIn("pain_status", data["reading"])
        self.assertEqual([row["pain_index"] for row in data["samples"]], [None, 32.3])
        self.assertEqual(model.predict.call_args.args[0].to_dict("records"), [{"heart_rate": 90}])

    def test_archive_account_polls_default_live_device_without_archive_fallback(self):
        self.user.return_value.update(device_id="P001", app_metadata={"neurosync_demo_patient": "P001"})
        with patch("routes.dashboard.telemetry", return_value=[]) as telemetry, patch("routes.dashboard._archive_profile") as profile:
            data = self.client.get("/api/live").get_json()
            html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(data["status"], "no_data")
        self.assertEqual(data["samples"], [])
        self.assertNotIn("reading", data)
        self.assertIn("Open Signal History", html)
        self.assertIn('data-live-source="true"', html)
        self.assertIn('data-field="finger_detected">—', html)
        self.assertIn('data-field="heart_rate">—', html)
        self.assertIn('id="painIndexGauge"', html)
        self.assertIn('No live readings', html)
        telemetry.assert_called_once_with("C6525", "test-token", limit=1)
        profile.assert_not_called()

    def test_device_override_and_clearing_it(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('placeholder="C6525"', html)
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        with patch("database.update_device_id") as save:
            for entered, expected in (("CUSTOM-1", "CUSTOM-1"), ("", None)):
                response = self.client.post("/settings", data={"device_id": entered, "csrf_token": token})
                self.assertEqual(response.status_code, 302)
                save.assert_called_with(1, expected)
        self.user.return_value["device_id"] = "CUSTOM-1"
        with patch("routes.dashboard.telemetry", return_value=[]) as telemetry:
            self.assertEqual(self.client.get("/api/live").json["device_id"], "CUSTOM-1")
            telemetry.assert_called_once_with("CUSTOM-1", "test-token", limit=1)

    def test_expert_with_role_only_can_open_roster_and_regular_patient_history(self):
        subject = "2f2b31b1-dd0f-416d-a403-bf8a295309e2"
        patient = {"id": None, "supabase_id": subject, "username": "New Account", "device_id": None}
        url = f"/expert/patients/{subject}"
        self.assertEqual(self.client.get(url + "/recordings").status_code, 403)
        self.user.return_value["app_metadata"] = {"neurosync_doctor": True}
        with patch("database.get_live_patients", return_value=[patient]), \
             patch("routes.dashboard.archive.expert_profiles", return_value=[]), \
             patch("routes.dashboard.telemetry", return_value=[]):
            self.assertIn("New Account", self.client.get("/expert").get_data(as_text=True))
            self.assertIn("C6525", self.client.get(url + "/history").get_data(as_text=True))
            self.assertEqual(self.client.get(url + "/recordings").json["device_id"], "C6525")
