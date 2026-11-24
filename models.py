"""
models.py — AE490 Flask-Login User model
"""
from flask_login import UserMixin


class User(UserMixin):
    """Wraps a SQLite row dict for Flask-Login."""

    def __init__(self, row: dict, assigned_patients=None, demo_patient_id=None):
        self.assigned_patients = assigned_patients
        self.is_doctor = assigned_patients is not None
        self.id               = row["id"]
        self.username         = f"Patient {demo_patient_id}" if demo_patient_id else row["username"]
        self.email            = row["email"]
        self.demo_patient_id  = demo_patient_id
        self.device_id        = demo_patient_id or row.get("device_id")
        self.created_at       = row.get("created_at")

    def get_id(self):
        return str(self.id)


def doctor_access(identity):
    """Return an administrator-managed mapping, or None for a patient account."""
    import re
    from uuid import UUID

    metadata = (identity or {}).get("app_metadata") or {}
    if metadata.get("neurosync_doctor") is not True:
        return None
    assignments = metadata.get("neurosync_patients")
    if not isinstance(assignments, list):
        return {}
    result = {}
    for assignment in assignments[:100]:
        if not isinstance(assignment, dict):
            continue
        try:
            subject = str(UUID(str(assignment.get("user_id"))))
        except ValueError:
            continue
        device = assignment.get("device_id")
        if isinstance(device, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
            result[subject] = device
    return result


def demo_patient(identity):
    metadata = (identity or {}).get("app_metadata") or {}
    patient_id = metadata.get("neurosync_demo_patient")
    return patient_id if patient_id in ("P001", "P002", "P003") else None
