"""Server-side Postgres persistence for Neurosync AI."""

from contextlib import closing
import json
import time

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import config


def get_db():
    if not config.SUPABASE_DB_URL:
        raise psycopg.OperationalError("Supabase Postgres connection is not configured.")
    options = conninfo_to_dict(config.SUPABASE_DB_URL)
    options.setdefault("sslmode", "require")
    options.setdefault("connect_timeout", 5)
    conn = psycopg.connect(**options, row_factory=dict_row)
    try:
        conn.execute("SET search_path TO neurosync_app, pg_catalog")
        conn.execute("SET TIME ZONE 'UTC'")
    except Exception:
        conn.close()
        raise
    return conn


def link_supabase_user(identity):
    subject = identity["id"]
    email = identity["email"].strip().lower()
    metadata = identity.get("user_metadata") or {}
    name = str(metadata.get("username") or email.split("@")[0]).strip()[:60] or "Patient"
    with closing(get_db()) as conn, conn:
        conn.execute("SELECT pg_advisory_xact_lock(1392656445)")
        row = conn.execute("SELECT id FROM users WHERE supabase_id=%s", (subject,)).fetchone()
        if row:
            return row["id"]
        existing = conn.execute("SELECT id, supabase_id FROM users WHERE lower(email)=%s", (email,)).fetchone()
        if existing:
            if existing["supabase_id"] or not identity.get("email_confirmed_at"):
                raise ValueError("This email is already linked to another account.")
            conn.execute("UPDATE users SET supabase_id=%s WHERE id=%s", (subject, existing["id"]))
            return existing["id"]
        username = name
        suffix = 1
        while conn.execute("SELECT 1 FROM users WHERE username=%s", (username,)).fetchone():
            suffix += 1
            username = f"{name} {suffix}"
        cursor = conn.execute(
            "INSERT INTO users (username, email, supabase_id) VALUES (%s, %s, %s) RETURNING id",
            (username, email, subject),
        )
        return cursor.fetchone()["id"]


def get_user_by_id(user_id):
    with closing(get_db()) as conn:
        row = conn.execute(
            """SELECT u.*, a.raw_app_meta_data AS app_metadata
               FROM users AS u JOIN auth.users AS a ON a.id::text=u.supabase_id
               WHERE u.id=%s""", (user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_device_id(user_id, device_id):
    with closing(get_db()) as conn, conn:
        conn.execute("UPDATE users SET device_id=%s WHERE id=%s", (device_id, user_id))


def get_live_patients(subject=None):
    """List regular Auth accounts, including people who have never signed in."""
    with closing(get_db()) as conn:
        return conn.execute(
            """SELECT a.id::text AS supabase_id, u.id, u.device_id,
                      COALESCE(u.username, NULLIF(a.raw_user_meta_data->>'username', ''),
                               NULLIF(split_part(a.email, '@', 1), ''), 'Patient') AS username
               FROM auth.users AS a
               LEFT JOIN users AS u ON u.supabase_id=a.id::text
               WHERE a.raw_app_meta_data->'neurosync_doctor' IS DISTINCT FROM 'true'::jsonb
                 AND COALESCE(a.raw_app_meta_data->>'neurosync_demo_patient', '') NOT IN ('P001', 'P002', 'P003')
                 AND (%s::uuid IS NULL OR a.id=%s::uuid)
               ORDER BY username, a.id""",
            (subject, subject),
        ).fetchall()


def save_auth_session(session_id, user_id, payload, valid_until):
    with closing(get_db()) as conn, conn:
        conn.execute("DELETE FROM auth_sessions WHERE valid_until <= %s", (time.time(),))
        conn.execute(
            """INSERT INTO auth_sessions
               (id, user_id, access_token, refresh_token, expires_at, valid_until)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (session_id, user_id, payload["access_token"], payload["refresh_token"],
             time.time() + payload.get("expires_in", 3600), valid_until),
        )


def get_auth_session(session_id):
    if not session_id:
        return None
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE id=%s AND valid_until > %s", (session_id, time.time())
        ).fetchone()
        return dict(row) if row else None


def refresh_auth_session(session_id, refresh_token, refresh):
    # Serialize token rotation across workers. Other requests reuse the newly
    # committed token instead of exchanging the same refresh token twice.
    with closing(get_db()) as conn, conn:
        row = conn.execute("SELECT * FROM auth_sessions WHERE id=%s FOR UPDATE", (session_id,)).fetchone()
        if row is None or row["valid_until"] <= time.time():
            return None
        if row["refresh_token"] != refresh_token:
            return dict(row)
        payload = refresh(refresh_token)
        conn.execute(
            """UPDATE auth_sessions SET access_token=%s, refresh_token=%s, expires_at=%s WHERE id=%s""",
            (payload["access_token"], payload["refresh_token"],
             time.time() + payload.get("expires_in", 3600), session_id),
        )
        return dict(conn.execute("SELECT * FROM auth_sessions WHERE id=%s", (session_id,)).fetchone())


def delete_auth_session(session_id):
    if session_id:
        with closing(get_db()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE id=%s", (session_id,))


# ─── Sensor Logs ─────────────────────────────────────────────────────────────

def insert_sensor_log(user_id, d: dict) -> int:
    """Insert one sensor snapshot and return the new row id."""
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO sensor_logs
           (user_id, accel_x, accel_y, accel_z,
            gyro_x, gyro_y, gyro_z, posture_score,
            emg_rms, heart_rate, hrv_ms,
            breath_rate, breath_depth, leads_on)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            user_id,
            d.get("accel_x"), d.get("accel_y"), d.get("accel_z"),
            d.get("gyro_x"),  d.get("gyro_y"),  d.get("gyro_z"),
            d.get("posture_score"),
            d.get("emg_rms"),
            d.get("heart_rate_bpm"),
            d.get("hrv"),
            d.get("breath_rate"),
            d.get("breath_depth"),
            int(d.get("leads_on", 1)),
        ),
    )
    log_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return log_id


def get_latest_sensor_log(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sensor_logs WHERE user_id=%s ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_sensor_logs(user_id, limit=200):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sensor_logs WHERE user_id=%s ORDER BY id DESC LIMIT %s",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_sensor_logs(user_id, limit=10, within_seconds=30, at_or_before=None):
    """Get this user's newest samples within a short SOS capture window."""
    limit = max(1, min(int(limit), 50))
    within_seconds = max(1, min(int(within_seconds), 300))
    where = "user_id=%s AND ts >= CURRENT_TIMESTAMP - make_interval(secs => %s)"
    params = [user_id, within_seconds]
    if at_or_before:
        where += " AND ts <= %s"
        params.append(at_or_before)
    params.append(limit)
    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM sensor_logs WHERE {where} ORDER BY id DESC LIMIT %s",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Pain Predictions ────────────────────────────────────────────────────────

def insert_pain_prediction(user_id, sensor_log_id, pain_flag, pain_prob, pain_level):
    conn = get_db()
    conn.execute(
        """INSERT INTO pain_predictions
           (user_id, sensor_log_id, pain_flag, pain_probability, pain_level)
           VALUES (%s,%s,%s,%s,%s)""",
        (user_id, sensor_log_id, pain_flag, pain_prob, pain_level),
    )
    conn.commit()
    conn.close()


def get_latest_pain_prediction(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pain_predictions WHERE user_id=%s ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_reading_with_prediction(user_id, sensor: dict, prediction: dict):
    """Persist a sensor sample and its prediction in one transaction.

    Keeping the two records in a single commit prevents the API from ever
    observing a half-written reading while a reading is being stored
    a sample.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO sensor_logs
               (user_id, accel_x, accel_y, accel_z,
                gyro_x, gyro_y, gyro_z, posture_score,
                emg_rms, heart_rate, hrv_ms,
                breath_rate, breath_depth, leads_on)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                user_id,
                sensor.get("accel_x"), sensor.get("accel_y"), sensor.get("accel_z"),
                sensor.get("gyro_x"), sensor.get("gyro_y"), sensor.get("gyro_z"),
                sensor.get("posture_score"), sensor.get("emg_rms"),
                sensor.get("heart_rate_bpm"), sensor.get("hrv"),
                sensor.get("breath_rate"), sensor.get("breath_depth"),
                int(bool(sensor.get("leads_on", 1))),
            ),
        )
        sensor_log_id = cur.fetchone()["id"]
        prediction_id = conn.execute(
            """INSERT INTO pain_predictions
               (user_id, sensor_log_id, pain_flag, pain_probability, pain_level)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (
                user_id,
                sensor_log_id,
                int(prediction["pain_flag"]),
                float(prediction["pain_probability"]),
                float(prediction["pain_level"]),
            ),
        ).fetchone()["id"]
        conn.commit()
        return sensor_log_id, prediction_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_latest_reading(user_id):
    """Return one coherent sensor + prediction pair for the live API."""
    conn = get_db()
    row = conn.execute(
        """SELECT sl.*, pp.id AS prediction_id, pp.ts AS prediction_ts,
                  pp.pain_flag, pp.pain_probability, pp.pain_level,
                  EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - sl.ts)::integer AS age_seconds
           FROM sensor_logs sl
           LEFT JOIN pain_predictions pp ON pp.sensor_log_id = sl.id
           WHERE sl.user_id=%s
           ORDER BY sl.id DESC
           LIMIT 1""",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pain_history(user_id, limit=500):
    """Returns pain predictions joined with sensor timestamp."""
    conn = get_db()
    rows = conn.execute(
        """SELECT COALESCE(sl.ts, pp.ts) AS ts,
                  pp.pain_flag, pp.pain_probability, pp.pain_level,
                  sl.posture_score, sl.emg_rms, sl.heart_rate, sl.hrv_ms,
                  sl.breath_rate, sl.breath_depth
           FROM pain_predictions pp
           LEFT JOIN sensor_logs sl ON pp.sensor_log_id = sl.id
           WHERE pp.user_id=%s
           ORDER BY pp.id DESC
           LIMIT %s""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_pain_averages(user_id, days=30):
    """Average legacy prototype score per day for the past N days."""
    conn = get_db()
    rows = conn.execute(
        """SELECT date(ts) as day,
                  AVG(pain_level)       as avg_pain_level,
                  AVG(pain_probability) as avg_pain_prob,
                  COUNT(*)              as reading_count
           FROM pain_predictions
           WHERE user_id=%s
             AND ts >= CURRENT_DATE - %s
           GROUP BY day
           ORDER BY day""",
        (user_id, int(days)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Diary Entries ────────────────────────────────────────────────────────────

def insert_diary_entry(user_id, self_rating, notes, ml_pain_avg=None):
    conn = get_db()
    conn.execute(
        """INSERT INTO diary_entries
           (user_id, self_pain_rating, notes, ml_pain_avg)
           VALUES (%s,%s,%s,%s)""",
        (user_id, self_rating, notes, ml_pain_avg),
    )
    conn.commit()
    conn.close()


def get_diary_entries(user_id, limit=60):
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM diary_entries WHERE user_id=%s
           ORDER BY entry_date DESC LIMIT %s""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Patient-reported pain diary ─────────────────────────────────────────────

def insert_pain_report(user_id, report):
    """Save a patient check-in or episode; all report times are normalized UTC."""
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pain_reports
               (user_id, report_type, occurred_at_utc, started_at_utc,
                ended_at_utc, timezone, pain_rating, pain_type,
                qualities_json, activity, relief, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                user_id,
                report["report_type"],
                report["occurred_at_utc"],
                report.get("started_at_utc"),
                report.get("ended_at_utc"),
                report["timezone"],
                report["pain_rating"],
                report.get("pain_type"),
                report.get("qualities_json"),
                report.get("activity"),
                report.get("relief"),
                report.get("notes"),
            ),
        )
        report_id = cur.fetchone()["id"]
        conn.commit()
        return report_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pain_reports(user_id, days=14, limit=1000):
    """Return patient-entered reports in the requested UTC lookback window."""
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM pain_reports
           WHERE user_id=%s AND occurred_at_utc >= CURRENT_TIMESTAMP - make_interval(days => %s)
           ORDER BY occurred_at_utc DESC, id DESC LIMIT %s""",
        (user_id, int(days), int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_pain_sos_event(user_id, occurred_at_utc, timezone_name, sensor_log_ids=None, telemetry=None):
    """Save an SOS event and link the nearby sensor samples that already arrived."""
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pain_sos_events (user_id, occurred_at_utc, timezone, telemetry_json)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (user_id, occurred_at_utc, timezone_name, json.dumps(telemetry) if telemetry is not None else None),
        )
        event_id = cur.fetchone()["id"]
        for sample_order, sensor_log_id in enumerate(sensor_log_ids or []):
            conn.execute(
                """INSERT INTO pain_sos_sensor_samples
                   (sos_event_id, sensor_log_id, sample_order)
                   SELECT %s, id, %s FROM sensor_logs WHERE id=%s AND user_id=%s ON CONFLICT DO NOTHING""",
                (event_id, sample_order, sensor_log_id, user_id),
            )
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pain_sos_events(user_id, days=14, limit=1000):
    """Return SOS taps with their linked sensor readings, oldest to newest."""
    conn = get_db()
    rows = conn.execute(
        """SELECT e.id AS event_id, e.user_id, e.occurred_at_utc, e.timezone, e.telemetry_json,
                  s.sample_order, sl.id AS sample_id, sl.ts AS sample_ts,
                  sl.heart_rate, sl.hrv_ms, sl.breath_rate, sl.breath_depth,
                  sl.emg_rms, sl.posture_score, sl.leads_on,
                  sl.accel_x, sl.accel_y, sl.accel_z,
                  sl.gyro_x, sl.gyro_y, sl.gyro_z
           FROM (
               SELECT * FROM pain_sos_events
               WHERE user_id=%s AND occurred_at_utc >= CURRENT_TIMESTAMP - make_interval(days => %s)
               ORDER BY occurred_at_utc DESC, id DESC LIMIT %s
           ) e
           LEFT JOIN pain_sos_sensor_samples s ON s.sos_event_id=e.id
           LEFT JOIN sensor_logs sl ON sl.id=s.sensor_log_id AND sl.user_id=e.user_id
           ORDER BY e.occurred_at_utc DESC, e.id DESC, s.sample_order ASC
           """,
        (user_id, int(days), int(limit)),
    ).fetchall()
    conn.close()

    events = {}
    for row in rows:
        event_id = row["event_id"]
        if event_id not in events:
            events[event_id] = {
                "id": event_id,
                "user_id": row["user_id"],
                "occurred_at_utc": row["occurred_at_utc"],
                "timezone": row["timezone"],
                "sensor_readings": json.loads(row["telemetry_json"]) if row["telemetry_json"] else [],
            }
        if row["sample_id"] is not None:
            events[event_id]["sensor_readings"].append({
                "sample_number": int(row["sample_order"]) + 1,
                "occurred_at_utc": row["sample_ts"],
                "heart_rate": row["heart_rate"],
                "hrv_ms": row["hrv_ms"],
                "breath_rate": row["breath_rate"],
                "breath_depth": row["breath_depth"],
                "emg_rms": row["emg_rms"],
                "posture_score": row["posture_score"],
                "leads_on": row["leads_on"],
                "accel_x": row["accel_x"],
                "accel_y": row["accel_y"],
                "accel_z": row["accel_z"],
                "gyro_x": row["gyro_x"],
                "gyro_y": row["gyro_y"],
                "gyro_z": row["gyro_z"],
            })
    return list(events.values())


def get_legacy_diary_reports(user_id, days=14, limit=500):
    """Return older daily self-reports for continuity in the new diary view."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, self_pain_rating, notes, created_at
           FROM diary_entries
           WHERE user_id=%s AND self_pain_rating IS NOT NULL
             AND created_at >= CURRENT_TIMESTAMP - make_interval(days => %s)
           ORDER BY created_at DESC, id DESC LIMIT %s""",
        (user_id, int(days), int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_hourly_sensor_trends(user_id, days=14):
    """Aggregate wearable values by local hour; timestamps are server receipt time.

    The dashboard timezone is Asia/Kolkata. Sensor firmware timestamps are
    millis since boot, so the UTC receipt timestamp is used.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT to_char(ts AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:00:00') AS hour_start_local,
                  AVG(heart_rate) AS avg_heart_rate,
                  MAX(heart_rate) AS peak_heart_rate,
                  AVG(breath_rate) AS avg_breath_rate,
                  MAX(breath_rate) AS peak_breath_rate,
                  AVG(emg_rms) AS avg_emg_rms,
                  MAX(emg_rms) AS peak_emg_rms,
                  AVG(posture_score) AS avg_posture_score,
                  MAX(posture_score) AS peak_posture_score,
                  COUNT(*) AS reading_count
           FROM sensor_logs
           WHERE user_id=%s AND ts >= CURRENT_TIMESTAMP - make_interval(days => %s)
           GROUP BY hour_start_local
           ORDER BY hour_start_local""",
        (user_id, int(days)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


