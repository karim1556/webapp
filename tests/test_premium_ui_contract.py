from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/style.css").read_text()
BASE = (ROOT / "templates/base.html").read_text()
DASHBOARD = (ROOT / "templates/dashboard.html").read_text()
HISTORY = (ROOT / "templates/history.html").read_text()
SIGNAL_PANELS = (ROOT / "templates/signal_panels.html").read_text()


class PremiumUiContractTests(unittest.TestCase):
    def test_light_instrument_tokens_are_defined(self):
        expected_tokens = {
            "--paper: #f7f8f6",
            "--surface: #ffffff",
            "--ink: #172a2d",
            "--teal: #0f827c",
            "--red: #e46f56",
        }
        for token in expected_tokens:
            self.assertIn(token, CSS)

    def test_shell_uses_light_theme_and_versioned_stylesheet(self):
        self.assertIn('content="#F7F8F6"', BASE)
        self.assertIn("v='premium-light-1'", BASE)

    def test_dashboard_shows_core_readings_without_selectors(self):
        self.assertNotIn("<select", DASHBOARD)
        self.assertIn('id="readingContract"', DASHBOARD)
        for field in (
            "heart_rate", "spo2", "hrv_rmssd", "respiration_rate", "perfusion_index",
            "temperature_c", "muscle_spasm_jerk",
        ):
            self.assertIn(f"'{field}'", DASHBOARD)

    def test_dashboard_hero_shows_only_the_random_forest_score(self):
        self.assertIn('id="painIndexGauge"', DASHBOARD)
        self.assertIn('data-field="pain_index"', DASHBOARD)
        self.assertIn('<h2 id="painIndexTitle">Pain Score</h2>', DASHBOARD)
        self.assertNotIn('data-field="vas_score"', DASHBOARD)
        self.assertNotIn('data-field="pain_probability"', DASHBOARD)

    def test_live_and_history_share_the_separate_chart_panels(self):
        self.assertIn('{% include "signal_panels.html" %}', DASHBOARD)
        self.assertIn('{% include "signal_panels.html" %}', HISTORY)
        self.assertEqual(HISTORY.count("<select"), 1)
        for panel in ("optical", "cardiac", "respiration", "oxygen", "perfusion", "environment", "motion", "pain"):
            self.assertIn(f"('{panel}',", SIGNAL_PANELS)


if __name__ == "__main__":
    unittest.main()
