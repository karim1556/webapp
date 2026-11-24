"""
routes/dashboard.py — AE490 Dashboard + API routes
"""

from flask import (
    Blueprint, render_template, jsonify,
    request, redirect, url_for, flash, current_app, abort, Response
)
from flask_login import login_required, current_user
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import csv
import io
import json
import re

import database as db
import config
import synthetic_archive as archive
from ml.patient_forest import score_readings
from routes.auth import session_record
from supabase_backend import (IST, RECORDING_FIELDS, SupabaseError, button_events_in_samples, hardware_events,
                              hourly_telemetry, telemetry, timestamp)

dash_bp = Blueprint("dashboard", __name__)
LIVE_SAMPLE_LIMIT = 200


@dash_bp.before_request
def restrict_doctor_workspace():
    if current_user.is_authenticated and current_user.is_doctor:
        if request.endpoint == "dashboard.dashboard_page":
            return redirect(url_for("dashboard.doctor_page"))
        if request.endpoint not in {"dashboard.doctor_page", "dashboard.doctor_patient",
                                    "dashboard.expert_history_data", "dashboard.expert_history_export"}:
            abort(403)


@dash_bp.get("/doctor")
@dash_bp.get("/expert")
@login_required
def doctor_page():
    if not current_user.is_doctor:
        abort(403)
    assignments = current_user.assigned_patients
    archive_patients = archive.expert_profiles(assignments)
    archive_subjects = {device: subject for subject, device in assignments.items() if device in archive.PATIENT_IDS}
    for patient in archive_patients:
        patient["subject"] = archive_subjects[patient["patient_id"]]
    live_subjects = [subject for subject, device in assignments.items() if device not in archive.PATIENT_IDS]
    return render_template("doctor.html", patients=db.get_assigned_users(live_subjects), archive_patients=archive_patients)


@dash_bp.get("/doctor/patients/<uuid:subject>")
@dash_bp.get("/expert/patients/<uuid:subject>")
@login_required
def doctor_patient(subject):
    if not current_user.is_doctor:
        abort(403)
    subject = str(subject)
    device_id = current_user.assigned_patients.get(subject)
    if device_id is None:
        abort(404)
    if device_id in archive.PATIENT_IDS:
        return render_template(
            "history.html", recording_fields=RECORDING_FIELDS, format_ist=_format_ist,
            archive_profile=_expert_archive_profile(subject), expert_subject=subject,
            history_api=url_for("dashboard.expert_history_data", subject=subject),
            history_export_url=url_for("dashboard.expert_history_export", subject=subject),
        )
    patients = db.get_assigned_users([subject])
    if not patients:
        abort(404)
    patient = patients[0]
    reports = db.get_pain_reports(patient["id"], days=30)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        token = session_record()["access_token"]
        recent = score_readings(telemetry(device_id, token, limit=1, since=since))
        events = hardware_events(device_id, token, since, score_samples=score_readings)
        telemetry_available = True
    except SupabaseError:
        recent, events, telemetry_available = [], [], False
    for report in reports:
        report["display_time"] = _db_time_as_iso(report["occurred_at_utc"])
    return render_template("doctor.html", patient=patient, device_id=device_id,
                           reports=reports, reading=recent[0] if recent else None,
                           events=events, telemetry_available=telemetry_available,
                           recording_fields=RECORDING_FIELDS, format_ist=_format_ist)


def _format_ist(iso_time):
    return timestamp(iso_time).astimezone(IST).strftime("%d %b %Y, %H:%M:%S IST")


# ─── Main pages ───────────────────────────────────────────────────────────────

@dash_bp.route("/dashboard")
@login_required
def dashboard_page():
    return render_template(
        "dashboard.html",
        poll_seconds=config.TELEMETRY_POLL_SECONDS,
        sample_limit=LIVE_SAMPLE_LIMIT,
        has_live_source=not current_user.demo_patient_id,
    )


@dash_bp.route("/history")
@login_required
def history_page():
    user_info = db.get_user_by_id(current_user.id)
    return render_template("history.html", user=user_info, recording_fields=RECORDING_FIELDS,
                           archive_profile=_archive_profile() if current_user.demo_patient_id else None,
                           format_ist=_format_ist, history_api=url_for("dashboard.api_history"),
                           history_export_url=url_for("dashboard.history_export"))


@dash_bp.route("/diary")
@login_required
def diary_page():
    user_info = db.get_user_by_id(current_user.id)
    return render_template("diary.html", user=user_info, recording_fields=RECORDING_FIELDS)


@dash_bp.route("/validation")
@login_required
def validation_page():
    user_info = db.get_user_by_id(current_user.id)
    return render_template("validation.html", user=user_info)


@dash_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        if current_user.demo_patient_id:
            abort(403)
        if "device_id" not in request.form:
            abort(400)
        device_id = request.form.get("device_id", "").strip()
        if device_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
            flash("Use up to 64 letters, numbers, hyphens or underscores.", "error")
        else:
            db.update_device_id(current_user.id, device_id or None)
            flash("Device selection updated.", "success")
        return redirect(url_for("dashboard.settings_page"))
    return render_template(
        "settings.html", user=db.get_user_by_id(current_user.id),
        poll_seconds=config.TELEMETRY_POLL_SECONDS,
    )


def _archive_profile():
    info = archive.profile(current_user.demo_patient_id, session_record()["access_token"])
    return _profile_dates(info)


def _expert_archive_profile(subject):
    if not current_user.is_doctor:
        abort(403)
    device = current_user.assigned_patients.get(str(subject))
    if device not in archive.PATIENT_IDS:
        abort(404)
    rows = archive.expert_profiles({str(subject): device}, details=True)
    return _profile_dates(rows[0] if rows else None)


def _profile_dates(info):
    if info is None:
        abort(404)
    info["first_day"] = timestamp(info["first_recorded_at"]).astimezone(IST).date().isoformat()
    info["last_day"] = timestamp(info["last_recorded_at"]).astimezone(IST).date().isoformat()
    return info


def _telemetry(**filters):
    token = session_record()["access_token"]
    if current_user.demo_patient_id:
        return archive.samples(current_user.demo_patient_id, token, **filters)
    return telemetry(current_user.device_id, token, **filters)


def _history_rows(limit):
    if current_user.demo_patient_id:
        return _archive_history_rows(_archive_profile(), session_record()["access_token"])
    hours = max(1, min(request.args.get("hours", 168, type=int), 2160))
    now = datetime.now(timezone.utc)
    samples = _telemetry(limit=limit, since=now - timedelta(hours=hours), until=now)
    return score_readings(button_events_in_samples(samples, current_user.device_id, session_record()["access_token"]))


def _archive_history_rows(info, token=None, *, expert_archive=False):
    requested_day = request.args.get("day", info["last_day"])
    try:
        selected = date.fromisoformat(requested_day)
    except ValueError:
        abort(400)
    if not date.fromisoformat(info["first_day"]) <= selected <= date.fromisoformat(info["last_day"]):
        abort(400)
    start, end = archive.day_bounds(selected)
    filters = {"limit": 750, "since": start, "until": end, "expert_archive": expert_archive}
    rows = archive.samples(info["patient_id"], token, **filters)
    if len(rows) == 750:
        rows.extend(archive.samples(info["patient_id"], token, offset=750, **filters))
    return score_readings(rows[::-1])


@dash_bp.errorhandler(SupabaseError)
def telemetry_error(error):
    current_app.logger.warning("Telemetry upstream returned status %s (%s)", error.status, error.code)
    if error.status == 403:
        return jsonify(status="forbidden", message="This account does not have access to the selected device."), 403
    return jsonify(status="unavailable", message="Supabase telemetry is temporarily unavailable."), 503


@dash_bp.route("/api/live")
@login_required
def api_live():
    device_id = current_user.device_id
    if current_user.demo_patient_id:
        return jsonify(status="no_live_source", device_id=device_id, fresh=False, samples=[])

    limit = max(1, min(request.args.get("limit", 1, type=int), LIVE_SAMPLE_LIMIT))
    samples = _telemetry(limit=limit)
    if not samples:
        return jsonify(status="no_data", device_id=device_id, fresh=False, samples=[])

    latest = samples[0]
    latest_received_at = timestamp(latest["ts"])
    age = max(0, (datetime.now(timezone.utc) - latest_received_at).total_seconds())
    freshness = config.LIVE_STALE_AFTER_SECONDS
    if int((freshness - age) * 1000) <= 0:
        return jsonify(status="offline", device_id=device_id, fresh=False, samples=[])

    # A newly connected device starts a new live series after a gap.
    active_samples = [latest]
    last_received_at = latest_received_at
    for sample in samples[1:]:
        recorded_at = timestamp(sample["ts"])
        if (last_received_at - recorded_at).total_seconds() >= freshness:
            break
        active_samples.append(sample)
        last_received_at = recorded_at

    score_readings(active_samples)
    age = max(0, (datetime.now(timezone.utc) - latest_received_at).total_seconds())
    fresh_for_ms = int((freshness - age) * 1000)
    if fresh_for_ms <= 0:
        return jsonify(status="offline", device_id=device_id, fresh=False, samples=[])
    return jsonify(status="ok", device_id=device_id, reading=latest, fresh=True,
                   age_seconds=int(age), fresh_for_ms=fresh_for_ms, samples=active_samples[::-1])


@dash_bp.route("/api/history")
@dash_bp.route("/api/sensor-history")
@login_required
def api_history():
    limit = max(1, min(request.args.get("limit", 500, type=int), 500))
    return jsonify(status="ok", data=_history_rows(limit), device_id=current_user.device_id)


@dash_bp.get("/history.csv")
@login_required
def history_export():
    return _history_csv(_history_rows(500))


@dash_bp.get("/expert/patients/<uuid:subject>/recordings")
@login_required
def expert_history_data(subject):
    info = _expert_archive_profile(subject)
    return jsonify(status="ok", device_id=info["patient_id"], data=_archive_history_rows(info, expert_archive=True))


@dash_bp.get("/expert/patients/<uuid:subject>/recordings.csv")
@login_required
def expert_history_export(subject):
    return _history_csv(_archive_history_rows(_expert_archive_profile(subject), expert_archive=True))


def _history_csv(rows):
    fields = RECORDING_FIELDS
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["received_at_utc", *[field for field, _, _ in fields], "sos_button", "pain_stamp", "pain_stamp_at_utc"])
    for row in rows:
        button_event = row.get("button_event")
        pressed = row["button_pressed"]
        writer.writerow([row["ts"], *[row[field] for field, _, _ in fields],
                         int(pressed) if pressed is not None else "",
                         "Hardware button" if button_event else "",
                         button_event["occurred_at"] if button_event else ""])
    return Response(output.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=neurosync-recordings.csv",
    })


@dash_bp.route("/api/daily-averages")
@login_required
def api_daily_averages():
    days = max(1, min(request.args.get("days", 30, type=int), 90))
    rows = score_readings(_telemetry(limit=500, since=datetime.now(timezone.utc) - timedelta(days=days)))
    daily = {}
    for row in rows:
        if row["pain_index"] is not None:
            date = timestamp(row["ts"]).astimezone(IST).date().isoformat()
            daily.setdefault(date, []).append(row["pain_index"] / 10)
    return jsonify(status="ok", data=[
        {"day": day, "avg_pain_level": sum(scores) / len(scores), "readings": len(scores)}
        for day, scores in sorted(daily.items())
    ])


# ─── REST API — Diary ────────────────────────────────────────────────────────

@dash_bp.route("/api/diary", methods=["GET"])
@login_required
def api_diary_get():
    limit  = max(1, min(request.args.get("limit", 60, type=int) or 60, 365))
    rows   = db.get_diary_entries(current_user.id, limit=limit)
    return jsonify({"status": "ok", "data": rows})


@dash_bp.route("/api/diary", methods=["POST"])
@login_required
def api_diary_post():
    body = request.get_json(force=True) or {}
    self_rating = body.get("self_pain_rating")
    notes       = body.get("notes", "").strip()

    try:
        self_rating = int(self_rating)
    except (TypeError, ValueError):
        self_rating = None

    if self_rating is None or not 0 <= self_rating <= 10:
        return jsonify({"status": "error", "msg": "Invalid pain rating (0–10)"}), 400

    # Historical diary rows retain their original reference score.
    ml_avg = None

    db.insert_diary_entry(
        user_id       = current_user.id,
        self_rating   = self_rating,
        notes         = notes,
        ml_pain_avg   = ml_avg,
    )
    return jsonify({"status": "ok"})


# ─── REST API — Patient-reported pain diary and trends ────────────────────────

def _as_utc_db_time(value, zone):
    """Convert an ISO client time (or local wall time) to SQLite UTC text."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _db_time_as_iso(value):
    """Serialize SQLite's UTC timestamp (stored without offset) explicitly."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dash_bp.route("/api/pain-trends", methods=["GET"])
@login_required
def api_pain_trends():
    """Return patient reports and hourly sensor summaries for diary graphs."""
    days = max(1, min(request.args.get("days", 14, type=int) or 14, 90))
    reports = []
    for row in db.get_pain_reports(current_user.id, days=days):
        item = dict(row)
        item["occurred_at_utc"] = _db_time_as_iso(item.get("occurred_at_utc"))
        item["started_at_utc"] = _db_time_as_iso(item.get("started_at_utc"))
        item["ended_at_utc"] = _db_time_as_iso(item.get("ended_at_utc"))
        try:
            item["qualities"] = json.loads(item.pop("qualities_json") or "[]")
        except (TypeError, ValueError):
            item["qualities"] = []
        item["legacy"] = False
        reports.append(item)

    # SOS taps are deliberately separate from rated reports: they record the
    # event time without guessing a pain intensity for the patient.
    for row in db.get_pain_sos_events(current_user.id, days=days):
        sensor_readings = [{
            "sample_number": sample.get("sample_number"),
            "occurred_at_utc": _db_time_as_iso(sample.get("occurred_at_utc")),
            "heart_rate": sample.get("heart_rate"),
            "hrv_ms": sample.get("hrv_ms"),
            "spo2": sample.get("spo2"),
            "source": sample.get("source"),
            "breath_rate": sample.get("breath_rate"),
            "breath_depth": sample.get("breath_depth"),
            "emg_rms": sample.get("emg_rms"),
            "posture_score": sample.get("posture_score"),
            "leads_on": sample.get("leads_on"),
            "accel_x": sample.get("accel_x"),
            "accel_y": sample.get("accel_y"),
            "accel_z": sample.get("accel_z"),
            "gyro_x": sample.get("gyro_x"),
            "gyro_y": sample.get("gyro_y"),
            "gyro_z": sample.get("gyro_z"),
        } for sample in row.get("sensor_readings", [])]
        reports.append({
            "id": f"sos-{row['id']}",
            "report_type": "sos",
            "occurred_at_utc": _db_time_as_iso(row.get("occurred_at_utc")),
            "started_at_utc": None,
            "ended_at_utc": None,
            "timezone": row.get("timezone") or "Asia/Kolkata",
            "pain_rating": None,
            "pain_type": None,
            "qualities": [],
            "activity": None,
            "relief": None,
            "notes": None,
            "sensor_readings": sensor_readings,
            "sensor_sample_count": len(sensor_readings),
            "sensor_sample_target": 10,
            "sensor_window_seconds": 30,
            "legacy": False,
        })

    # Older daily self-ratings remain visible as check-ins. Their saved time is
    # shown because no time-of-day field was captured in the old diary form.
    for row in db.get_legacy_diary_reports(current_user.id, days=days):
        reports.append({
            "id": f"legacy-{row['id']}",
            "report_type": "check_in",
            "occurred_at_utc": _db_time_as_iso(row.get("created_at")),
            "started_at_utc": None,
            "ended_at_utc": None,
            "timezone": "Asia/Kolkata",
            "pain_rating": row.get("self_pain_rating"),
            "pain_type": None,
            "qualities": [],
            "activity": None,
            "relief": None,
            "notes": row.get("notes"),
            "legacy": True,
        })
    reports.sort(key=lambda item: item.get("occurred_at_utc") or "", reverse=True)

    sensor_status = "ok"
    try:
        if current_user.demo_patient_id:
            sensor_rows, sensor_truncated = [], False
            sensor_status = "archive"
        else:
            sensor_rows, sensor_truncated = hourly_telemetry(
                current_user.device_id, session_record()["access_token"],
                datetime.now(timezone.utc) - timedelta(days=days),
            )
    except SupabaseError:
        sensor_rows, sensor_truncated, sensor_status = [], False, "unavailable"

    event_status = "ok"
    try:
        token = session_record()["access_token"]
        if current_user.demo_patient_id:
            end = timestamp(_archive_profile()["last_recorded_at"]) + timedelta(seconds=1)
            events = [row["button_event"] for row in score_readings(_telemetry(
                limit=500, since=end - timedelta(days=days), until=end, sos_only=True,
            ))]
        else:
            events = hardware_events(current_user.device_id, token, datetime.now(timezone.utc) - timedelta(days=days),
                                     score_samples=score_readings)
    except SupabaseError:
        events, event_status = [], "unavailable"

    response = jsonify({
        "status": "ok",
        "days": days,
        "timezone": "Asia/Kolkata",
        "reports": reports,
        "sensor_hours": sensor_rows,
        "sensor_truncated": sensor_truncated,
        "sensor_status": sensor_status,
        "hardware_events": events,
        "hardware_events_status": event_status,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@dash_bp.route("/api/pain-reports", methods=["POST"])
@login_required
def api_pain_report_post():
    """Save a patient-entered check-in or episode report."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"status": "error", "msg": "Send a JSON report."}), 400

    report_type = body.get("report_type")
    if report_type not in ("check_in", "episode"):
        return jsonify({"status": "error", "msg": "Choose a check-in or pain episode."}), 400

    raw_rating = body.get("pain_rating")
    if isinstance(raw_rating, bool):
        return jsonify({"status": "error", "msg": "Pain rating must be a whole number from 0 to 10."}), 400
    try:
        pain_rating = int(raw_rating)
    except (TypeError, ValueError):
        pain_rating = None
    if pain_rating is None or str(raw_rating).strip() != str(pain_rating) or not 0 <= pain_rating <= 10:
        return jsonify({"status": "error", "msg": "Pain rating must be a whole number from 0 to 10."}), 400

    timezone_name = body.get("timezone") or "Asia/Kolkata"
    if not isinstance(timezone_name, str) or len(timezone_name) > 64:
        return jsonify({"status": "error", "msg": "Invalid timezone."}), 400
    try:
        user_zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return jsonify({"status": "error", "msg": "Choose a valid timezone."}), 400

    now = datetime.now(timezone.utc)
    if report_type == "check_in":
        raw_occurred = body.get("occurred_at")
        occurred = _as_utc_db_time(raw_occurred, user_zone)
        if raw_occurred and occurred is None:
            return jsonify({"status": "error", "msg": "Choose a valid check-in time."}), 400
        if occurred is None:
            occurred = now.strftime("%Y-%m-%d %H:%M:%S")
        started = ended = None
    else:
        started = _as_utc_db_time(body.get("started_at"), user_zone)
        ended = _as_utc_db_time(body.get("ended_at"), user_zone) if body.get("ended_at") else None
        if started is None:
            return jsonify({"status": "error", "msg": "Add when the episode started."}), 400
        if body.get("ended_at") and ended is None:
            return jsonify({"status": "error", "msg": "Choose a valid episode end time."}), 400
        if ended and ended < started:
            return jsonify({"status": "error", "msg": "Episode end must be after its start."}), 400
        occurred = started

    occurred_dt = datetime.strptime(occurred, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if occurred_dt > now.replace(microsecond=0) + timedelta(minutes=5):
        return jsonify({"status": "error", "msg": "Report time cannot be in the future."}), 400
    if ended:
        ended_dt = datetime.strptime(ended, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if ended_dt > now.replace(microsecond=0) + timedelta(minutes=5):
            return jsonify({"status": "error", "msg": "Episode end cannot be in the future."}), 400

    allowed_types = {"phantom", "residual_limb", "both", "other", "not_sure"}
    pain_type = body.get("pain_type") or None
    if not isinstance(pain_type, str) or pain_type not in allowed_types:
        pain_type = None
    allowed_qualities = {"burning", "electric_shooting", "cramping", "tingling", "aching", "pressure", "other"}
    qualities = body.get("qualities") if isinstance(body.get("qualities"), list) else []
    qualities = [item for item in qualities if isinstance(item, str) and item in allowed_qualities][:7]

    def _optional_text(key, max_length=500):
        value = body.get(key, "")
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:max_length] or None

    report_id = db.insert_pain_report(current_user.id, {
        "report_type": report_type,
        "occurred_at_utc": occurred,
        "started_at_utc": started,
        "ended_at_utc": ended,
        "timezone": timezone_name,
        "pain_rating": pain_rating,
        "pain_type": pain_type,
        "qualities_json": json.dumps(qualities),
        "activity": _optional_text("activity"),
        "relief": _optional_text("relief"),
        "notes": _optional_text("notes", 1000),
    })
    return jsonify({"status": "ok", "id": report_id}), 201


