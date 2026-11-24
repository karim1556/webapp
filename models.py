"""
models.py — AE490 Flask-Login User model
"""
from flask_login import UserMixin


class User(UserMixin):
    """Wraps an app profile and trusted Auth metadata for Flask-Login."""

    def __init__(self, row: dict, default_device):
        identity = {"app_metadata": row.get("app_metadata")}
        self.is_doctor = is_expert(identity)
        demo_patient_id = demo_patient(identity)
        self.id               = row["id"]
        self.username         = f"Patient {demo_patient_id}" if demo_patient_id else row["username"]
        self.email            = row["email"]
        self.demo_patient_id  = demo_patient_id
        self.device_override  = device_override(row.get("device_id"), demo_patient_id)
        self.device_id        = self.device_override or default_device
        self.created_at       = row.get("created_at")

    def get_id(self):
        return str(self.id)


def is_expert(identity):
    metadata = (identity or {}).get("app_metadata") or {}
    return metadata.get("neurosync_doctor") is True


def device_override(value, archive_id=None):
    # Archived accounts previously stored the history identifier as a device ID.
    return value if value != archive_id else None


def demo_patient(identity):
    metadata = (identity or {}).get("app_metadata") or {}
    patient_id = metadata.get("neurosync_demo_patient")
    return patient_id if patient_id in ("P001", "P002", "P003") else None
