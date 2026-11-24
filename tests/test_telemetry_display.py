import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from supabase_backend import hourly_telemetry, normalize


class TelemetryDisplayTests(unittest.TestCase):
    def test_standby_placeholders_are_missing_and_other_readings_stay_visible(self):
        sample = normalize({
            "created_at": "2026-11-18T09:16:05Z",
            "finger_detected": False,
            "biometrics": {"heart_rate": 0, "spo2": 0, "respiration_rate": 0},
            "system": {"battery_percent": 0, "battery_voltage": 0},
            "assessment": {"pain_index": 9.8, "vas_score": 1, "pain_status": "STANDBY"},
            "motion_emg": {"emg_tone": 10, "muscle_spasm_jerk": 0},
        })

        for name in ("heart_rate", "spo2", "respiration_rate", "battery_percent", "battery_voltage"):
            self.assertIsNone(sample[name], name)
        self.assertEqual(sample["pain_index"], 9.8)
        self.assertEqual(sample["pain_status"], "STANDBY")
        self.assertEqual(sample["emg_tone"], 10)
        self.assertEqual(sample["muscle_spasm_jerk"], 0)

    def test_reported_zero_charge_is_preserved_with_measured_voltage(self):
        sample = normalize({
            "created_at": "2026-11-18T09:16:05Z",
            "battery_percent": 0, "battery_voltage": 3.1,
        })

        self.assertEqual(sample["battery_percent"], 0)
        self.assertEqual(sample["battery_voltage"], 3.1)

    def test_hourly_counts_reflect_available_values_for_each_signal(self):
        rows = [
            normalize({"created_at": "2026-11-18T09:00:00Z", "heart_rate": 80, "emg_tone": 10}),
            normalize({"created_at": "2026-11-18T09:01:00Z", "heart_rate": 0, "emg_tone": 20}),
        ]
        with patch("supabase_backend.telemetry", return_value=rows):
            summaries, truncated = hourly_telemetry("device", "token", datetime(2026, 11, 18, tzinfo=timezone.utc))

        self.assertFalse(truncated)
        self.assertEqual(summaries[0]["count_heart_rate"], 1)
        self.assertEqual(summaries[0]["avg_heart_rate"], 80)
        self.assertEqual(summaries[0]["count_emg_rms"], 2)
        self.assertEqual(summaries[0]["avg_emg_rms"], 15)
