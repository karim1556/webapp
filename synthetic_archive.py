"""Normalize the supplied research demo CSVs and serve their private archive."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from demo_telemetry import generate_fields
from supabase_backend import IST, RECORDING_FIELDS, _request, timestamp

DEMOGRAPHICS = {
    "P001": {"sex": "Female", "birth_year": 1992},
    "P002": {"sex": "Male", "birth_year": 1986},
    "P003": {"sex": "Male", "birth_year": 2002},
}
PATIENT_IDS = tuple(DEMOGRAPHICS)
PROFILE_SELECT = "patient_id,profile,daily_summary,events,first_recorded_at,last_recorded_at,sample_count"
MEASUREMENTS = {
    "heart_rate": "heart_rate_bpm", "ibi_ms": "ibi_ms", "temperature_c": "socket_temp_c",
    "humidity_sweat": "socket_humidity_pct", "accel_peak_g": "accel_peak_g",
    "gyro_peak_dps": "gyro_peak_dps", "tilt_deg": "tilt_deg", "steps": "steps",
    "battery_voltage": "battery_v",
}


def archive_instant(local_time):
    return datetime.fromisoformat(local_time).replace(tzinfo=IST).astimezone(timezone.utc)


def load_bundle(folder):
    """Load original columns and verify the patient IDs and minute timestamps."""
    folder = Path(folder)

    def read(name):
        with (folder / name).open(newline="", encoding="utf-8-sig") as file:
            return list(csv.DictReader(file))

    profiles = {row["patient_id"]: row for row in read("patient_profiles.csv")}
    if set(profiles) != set(PATIENT_IDS):
        raise ValueError("Expected patient profiles P001–P003.")
    daily = read("daily_summary.csv")
    events = read("events_log.csv")
    imu = read("raw_imu_50hz_windows.csv")

    result = {}
    for patient_id in PATIENT_IDS:
        samples = []
        seen = set()
        for row in read(f"{patient_id}_sensor_data.csv"):
            recorded = archive_instant(row["timestamp"])
            if row["patient_id"] != patient_id or recorded.timestamp() != int(row["unix_time"]) or recorded in seen:
                raise ValueError(f"Wrong patient, timestamp, or duplicate in {patient_id}_sensor_data.csv.")
            seen.add(recorded)
            samples.append({
                "patient_id": patient_id, "recorded_at": recorded.isoformat(),
                "readings": {**row, "neurosync_telemetry": generate_fields(row)},
            })
        samples.sort(key=lambda row: row["recorded_at"])
        if not samples:
            raise ValueError(f"No readings for {patient_id}.")
        result[patient_id] = {
            "patient_id": patient_id, "profile": {**profiles[patient_id], **DEMOGRAPHICS[patient_id]},
            "daily_summary": [row for row in daily if row["patient_id"] == patient_id],
            "events": [row for row in events if row["patient_id"] == patient_id],
            "imu_windows": [row for row in imu if row["patient_id"] == patient_id],
            "first_recorded_at": samples[0]["recorded_at"], "last_recorded_at": samples[-1]["recorded_at"],
            "sample_count": len(samples), "samples": samples,
        }
    return result


def profile(patient_id, token):
    rows = _request("GET", "/rest/v1/neurosync_demo_patients", token=token, params={
        "patient_id": f"eq.{patient_id}", "select": PROFILE_SELECT, "limit": 1,
    })
    return rows[0] if rows else None


def expert_profiles(owner_id=None):
    filters = {"ready": "eq.true", "order": "patient_id.asc",
               "select": PROFILE_SELECT if owner_id else "patient_id,owner_id,profile,sample_count"}
    if owner_id:
        filters.update(owner_id=f"eq.{owner_id}", limit=1)
    return _request("GET", "/rest/v1/neurosync_demo_patients", expert_archive=True, params=filters)


def normalize_sample(row):
    source = row["readings"]
    values = {target: float(source[key]) if source.get(key) else None for target, key in MEASUREMENTS.items()}
    values.update({
        "id": row["recorded_at"], "ts": timestamp(row["recorded_at"]).isoformat(),
        "device_id": row["patient_id"], "state_label": "Archived", "finger_detected": None,
        "button_pressed": source["sos_button"] == "1", "quality": source.get("data_quality_flag"),
        "activity": source.get("gt_activity"), "scenario": source.get("gt_event"),
    })
    values.update(source.get("neurosync_telemetry") or generate_fields(source))
    if values["button_pressed"]:
        values["button_event"] = {
            "id": values["id"], "occurred_at": values["ts"],
            "measurements": {key: values[key] for key, _, _ in RECORDING_FIELDS},
        }
    return values


def samples(patient_id, token, *, limit, since=None, until=None, offset=0, sos_only=False, expert_archive=False):
    filters = [
        ("patient_id", f"eq.{patient_id}"), ("order", "recorded_at.desc"),
        ("select", "patient_id,recorded_at,readings"), ("limit", limit), ("offset", offset),
    ]
    if since is not None:
        filters.append(("recorded_at", "gte." + since.isoformat()))
    if until is not None:
        filters.append(("recorded_at", "lt." + until.isoformat()))
    if sos_only:
        filters.append(("readings->>sos_button", "eq.1"))
    return [normalize_sample(row) for row in _request(
        "GET", "/rest/v1/neurosync_demo_samples", token=token, params=filters, expert_archive=expert_archive,
    )]


def day_bounds(day):
    start = archive_instant(day.isoformat())
    return start, start + timedelta(days=1)
