import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from models import is_expert
from supabase_backend import button_events_in_samples, hardware_events, normalize


def packet(row_id, voltage, **values):
    return normalize({
        "id": row_id, "created_at": f"2026-11-21T12:00:{row_id:02}Z",
        "system": {"battery_voltage": voltage, "battery_percent": 0},
        **values,
    })


class HardwareEventTests(unittest.TestCase):
    def test_binary_battery_values_are_button_states(self):
        self.assertIs(packet(1, 0, haptic_active=True)["button_pressed"], False)
        self.assertIs(packet(2, 1)["button_pressed"], True)
        self.assertIsNone(packet(2, 1)["battery_voltage"])
        self.assertIsNone(packet(3, 3.85)["button_pressed"])
        self.assertEqual(packet(3, 3.85)["battery_voltage"], 3.85)

    def test_held_and_missing_packets_do_not_repeat_a_stamp(self):
        rows = [
            packet(7, 1), packet(6, 0), packet(5, 1),
            packet(4, None), packet(3, 1), packet(2, 1, pain_index=10), packet(1, 0),
        ]
        chronological = button_events_in_samples(rows, "C6525", "token")
        events = [row["button_event"] for row in chronological if "button_event" in row]
        self.assertEqual([event["id"] for event in events], [2, 7])
        self.assertEqual(events[0]["occurred_at"], "2026-11-21T12:00:02+00:00")
        self.assertEqual(events[0]["measurements"]["pain_index"], 10)

    def test_window_beginning_during_a_press_does_not_create_a_new_stamp(self):
        rows = [packet(5, 1), packet(4, 1)]
        with patch("supabase_backend.telemetry", return_value=[packet(3, 1)]) as telemetry:
            chronological = button_events_in_samples(rows, "C6525", "token")
        self.assertNotIn("button_event", chronological[0])
        self.assertNotIn("button_event", chronological[1])
        self.assertEqual(telemetry.call_args.kwargs["before"], (rows[1]["ts"], 4))

    def test_diary_uses_the_first_pressed_packet_snapshot(self):
        rows = [packet(3, 1, pain_index=15), packet(2, 1, pain_index=10), packet(1, 0)]
        with patch("supabase_backend.telemetry", return_value=rows):
            events = hardware_events("C6525", "token", datetime(2026, 11, 21, tzinfo=timezone.utc))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["measurements"]["pain_index"], 10)

    def test_editable_profile_cannot_enable_clinician_role(self):
        self.assertFalse(is_expert({"user_metadata": {"neurosync_doctor": True}}))
        self.assertFalse(is_expert({"app_metadata": {"neurosync_doctor": "true"}}))
        self.assertTrue(is_expert({"app_metadata": {"neurosync_doctor": True}}))


if __name__ == "__main__":
    unittest.main()
