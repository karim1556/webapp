"""
config.py — AE490 Flask Web Application Configuration
"""

import os
import sys
import secrets

# ─── Flask ────────────────────────────────────────────────────────────────────
DEBUG = False

# ─── SQLite database (local) ──────────────────────────────────────────────────
def _user_data_dir() -> str:
    """Return a writable, platform-native directory for application data."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(root, "PhantomLimb")


BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_STORAGE = _user_data_dir() if getattr(sys, "frozen", False) else BASE_DIR
if getattr(sys, "frozen", False):
    os.makedirs(APP_STORAGE, exist_ok=True)

# Local settings are optional. Deployment environment variables take precedence.
_settings_file = os.path.join(APP_STORAGE if getattr(sys, "frozen", False) else BASE_DIR, ".env")
try:
    with open(_settings_file, encoding="utf-8") as settings:
        for line in settings:
            name, separator, value = line.strip().partition("=")
            if separator and name in {
                "SECRET_KEY", "SUPABASE_URL", "SUPABASE_PUBLIC_KEY", "SUPABASE_ADMIN_KEY", "SUPABASE_DB_URL",
                "PHANTOMLIMB_ORIGIN", "PHANTOMLIMB_DEFAULT_DEVICE",
            }:
                os.environ.setdefault(name, value.strip())
except FileNotFoundError:
    pass

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://utocjwqcwfqaovvlhsgx.supabase.co").rstrip("/")
SUPABASE_PUBLIC_KEY = os.environ.get("SUPABASE_PUBLIC_KEY", "")
SUPABASE_ADMIN_KEY = os.environ.get("SUPABASE_ADMIN_KEY", "")
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL", "")
PUBLIC_ORIGIN = (os.environ.get("PHANTOMLIMB_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
DEFAULT_DEVICE = os.environ.get("PHANTOMLIMB_DEFAULT_DEVICE", "C6525")
TELEMETRY_POLL_SECONDS = 5
LIVE_STALE_AFTER_SECONDS = 30

# ─── ML model paths ───────────────────────────────────────────────────────────
ML_DIR              = os.path.join(BASE_DIR, "ml")
PATIENT_FOREST_PATH = os.path.join(ML_DIR, "models", "patient_pain_forest.joblib")
CLASSIFIER_PATH     = os.path.join(ML_DIR, "models", "pain_classifier.pkl")
REGRESSOR_PATH      = os.path.join(ML_DIR, "models", "pain_regressor.pkl")
SCALER_PATH         = os.path.join(ML_DIR, "models", "scaler.pkl")

# ─── Pain probability threshold for alert ─────────────────────────────────────
PAIN_THRESHOLD = 0.50
