"""Server-side access to Supabase Auth and wearable telemetry."""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import current_app

IST = ZoneInfo("Asia/Kolkata")
GROUPS = {
    "biometrics": ("heart_rate", "spo2", "spo2_status", "hrv_rmssd", "hrv_sdnn",
                   "respiration_rate", "respiration_method", "perfusion_index",
                   "blood_flow_status", "pulse_raw_adc"),
    "motion_emg": ("muscle_spasm_jerk", "posture_score", "posture_status", "emg_tone"),
    "environment": ("temperature_c", "humidity_sweat"),
    "assessment": ("pain_index", "vas_score", "pain_status", "assessment_label"),
    "system": ("battery_voltage", "battery_percent"),
}
PPG_MEASUREMENTS = ("heart_rate", "spo2", "hrv_rmssd", "hrv_sdnn", "respiration_rate", "perfusion_index")
TEXT_FIELDS = {
    "spo2_status", "respiration_method", "blood_flow_status", "posture_status",
    "pain_status", "assessment_label",
}


class SupabaseError(Exception):
    def __init__(self, status, code=""):
        super().__init__(f"Supabase request failed ({status})")
        self.status = status
        self.code = code


RECORDING_FIELDS = [
    ("heart_rate", "Heart rate", "bpm"),
    ("hrv_rmssd", "HRV RMSSD", "ms"),
    ("hrv_sdnn", "HRV SDNN", "ms"),
    ("spo2", "Oxygen saturation", "%"),
    ("perfusion_index", "Perfusion index", "%"),
    ("respiration_rate", "Respiration", "breaths/min"),
    ("temperature_c", "Temperature", "°C"),
    ("humidity_sweat", "Humidity / sweat proxy", "%"),
    ("muscle_spasm_jerk", "Muscle motion / spasms", "m/s²"),
    ("pain_index", "Pain Score", "/ 100"),
]


def _request(method, path, *, token=None, params=None, body=None, expert_archive=False):
    base = current_app.config["SUPABASE_URL"]
    key = current_app.config["SUPABASE_PUBLIC_KEY"]
    if expert_archive:
        if method != "GET" or path not in {"/rest/v1/neurosync_demo_patients", "/rest/v1/neurosync_demo_samples"}:
            raise ValueError("Expert archive access is read-only.")
        key = current_app.config["SUPABASE_ADMIN_KEY"]
        token = None if key.startswith("sb_secret_") else key
    if not base or not key:
        raise SupabaseError(503, "not_configured")
    headers = {"apikey": key, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(
            method, base + path, headers=headers, params=params, json=body,
            timeout=(3, 6), allow_redirects=False,
        )
    except requests.RequestException as error:
        raise SupabaseError(503) from error
    if not response.ok or response.is_redirect:
        try:
            error = response.json()
            code = error.get("error_code") or error.get("code") or ""
        except (ValueError, AttributeError):
            code = ""
        raise SupabaseError(response.status_code, code)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as error:
        raise SupabaseError(502) from error


def auth(path, *, method="POST", token=None, params=None, body=None):
    return _request(method, "/auth/v1/" + path, token=token, params=params, body=body)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize(row):
    reading = {key: row.get(key) for key in (
        "id", "device_id", "state", "state_label", "finger_detected",
        "haptic_active", "vibration_intensity",
    )}
    reading["ts"] = timestamp(row["created_at"]).isoformat()
    for group, keys in GROUPS.items():
        nested = row.get(group)
        if not isinstance(nested, dict):
            nested = {}
        for key in keys:
            value = row.get(key)
            if value is None:
                value = nested.get(key)
            reading[key] = (value if isinstance(value, str) else None) if key in TEXT_FIELDS else _number(value)
    # Firmware emits zero placeholders for missing optical measurements.
    # A contact flag of false also invalidates carried-over optical values.
    for key in PPG_MEASUREMENTS:
        if reading["finger_detected"] is False or reading[key] == 0:
            reading[key] = None
    # The firmware uses the battery-voltage slot to upload the physical SOS flag.
    battery_value = reading["battery_voltage"]
    reading["button_pressed"] = bool(battery_value) if battery_value in (0, 1) else None
    if reading["button_pressed"] is not None:
        reading["battery_voltage"] = None
        if reading["battery_percent"] == 0:
            reading["battery_percent"] = None
    return reading


def telemetry(device_id, token, *, limit, since=None, until=None, offset=0, before=None, button_states_only=False):
    if not device_id:
        return []
    params = [
        ("device_id", f"eq.{device_id}"), ("order", "created_at.desc,id.desc"),
        ("limit", limit), ("offset", offset),
    ]
    if button_states_only:
        params.append(("battery_voltage", "in.(0,1)"))
    if before is not None:
        stamp, row_id = before
        params.append(("or", f"(created_at.lt.{stamp},and(created_at.eq.{stamp},id.lt.{row_id}))"))
    if since:
        params.append(("created_at", "gte." + since.isoformat()))
    if until:
        params.append(("created_at", "lte." + until.isoformat()))
    rows = _request("GET", "/rest/v1/phantom_limb_telemetry", token=token, params=params)
    return [normalize(row) for row in rows]


def button_events_in_samples(rows, device_id, token):
    """Annotate the first pressed packet in each run of 1s, preserving raw button state."""
    ordered = rows[::-1]
    first_flag = next((row for row in ordered if row["button_pressed"] is not None), None)
    pressed = False
    if first_flag and first_flag["button_pressed"]:
        preceding = telemetry(device_id, token, limit=1, button_states_only=True,
                              before=(first_flag["ts"], first_flag["id"]))
        pressed = bool(preceding and preceding[0]["button_pressed"])

    for row in ordered:
        state = row["button_pressed"]
        if state is None:
            continue
        if state and not pressed:
            row["button_event"] = {
                "id": row["id"],
                "occurred_at": row["ts"],
                "measurements": {field: row[field] for field, _, _ in RECORDING_FIELDS},
            }
        pressed = state
    return ordered


def hardware_events(device_id, token, since, *, score_samples=None):
    rows = []
    before = None
    until = datetime.now(timezone.utc)
    page_size = 500
    while True:
        page = telemetry(device_id, token, limit=page_size, since=since, until=until,
                         before=before, button_states_only=True)
        rows.extend(page)
        if len(page) < page_size:
            break
        last = page[-1]
        before = (last["ts"], last["id"])
    ordered = button_events_in_samples(rows, device_id, token)
    event_rows = [row for row in reversed(ordered) if "button_event" in row]
    if score_samples is not None:
        score_samples(event_rows)
    return [row["button_event"] for row in event_rows]


def hourly_telemetry(device_id, token, since):
    """Group a bounded recent window in IST for diary charts."""
    buckets = {}
    until = datetime.now(timezone.utc)
    for offset in range(0, 2000, 500):
        page = telemetry(device_id, token, limit=500, since=since, until=until, offset=offset)
        for sample in page:
            hour = timestamp(sample["ts"]).astimezone(IST).strftime("%Y-%m-%d %H:00:00")
            buckets.setdefault(hour, []).append(sample)
        if len(page) < 500:
            break
    metrics = {
        "heart_rate": "heart_rate", "breath_rate": "respiration_rate",
        "emg_rms": "emg_tone", "posture_score": "posture_score",
    }
    result = []
    for hour, samples in sorted(buckets.items()):
        summary = {"hour_start_local": hour, "reading_count": len(samples)}
        for name, source in metrics.items():
            values = [sample[source] for sample in samples if sample[source] is not None]
            summary["count_" + name] = len(values)
            summary["avg_" + name] = sum(values) / len(values) if values else None
            summary["peak_" + name] = max(values) if values else None
        result.append(summary)
    return result, len(page) == 500
