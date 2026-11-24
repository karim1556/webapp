import unittest

from demo_telemetry import firmware_score, generate_fields
from supabase_backend import RECORDING_FIELDS


class DemoTelemetryTests(unittest.TestCase):
    def test_same_fields_and_quiet_and_elevated_episodes_for_all_patients(self):
        for patient in ("P001", "P002", "P003"):
            base = {
                "patient_id": patient, "day": "1", "unix_time": "1793471400",
                "timestamp": "2026-11-01 00:00:00",
                "heart_rate_bpm": "68", "socket_temp_c": "32.8", "socket_humidity_pct": "54",
                "accel_peak_g": "1.12", "accel_mag_mean_g": "1.01", "gyro_mean_dps": "4",
            }
            quiet = generate_fields(base)
            self.assertEqual(quiet["pain_status"], "NORMAL")
            self.assertEqual(quiet, generate_fields(base))
            self.assertTrue(all(key in quiet and quiet[key] is not None for key, _, _ in RECORDING_FIELDS))
            elevated = [
                generate_fields({**base, "timestamp": f"2026-11-01 18:{minute:02}:00",
                                 "unix_time": str(1793471400 + 18 * 3600 + minute * 60)})
                for minute in range(60)
            ]
            self.assertTrue(any(row["pain_index"] >= 45 for row in elevated), patient)

    def test_scoring_clamps_the_weighted_sum_after_the_triad_bonus(self):
        fields = {
            "heart_rate": 110, "hrv_rmssd": 8, "perfusion_index": .40, "respiration_rate": 28,
            "humidity_sweat": 80, "temperature_c": 38, "posture_score": 65, "spo2": 90,
        }
        self.assertEqual(firmware_score(fields, spasm=True), 1.0)


if __name__ == "__main__":
    unittest.main()
