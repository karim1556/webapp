"""
database.py — AE490 Phantom Limb — SQLite Schema and CRUD helpers
"""

from models import demo_patient, doctor_access

import sqlite3
import json
import time
from contextlib import closing
from config import DB_PATH


# ─── Schema ───────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT    UNIQUE NOT NULL,
    email             TEXT    UNIQUE NOT NULL,
    supabase_id       TEXT    UNIQUE,
    device_id         TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sensor_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    accel_x         REAL, accel_y REAL, accel_z REAL,
    gyro_x          REAL, gyro_y  REAL, gyro_z  REAL,
    posture_score   REAL,
    emg_rms         INTEGER,
    heart_rate      REAL,
    hrv_ms          REAL,
    breath_rate     REAL,
    breath_depth    REAL,
    leads_on        INTEGER DEFAULT 1,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS pain_predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    sensor_log_id   INTEGER,
    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pain_flag       INTEGER,
    pain_probability REAL,
    pain_level      REAL,
    FOREIGN KEY(user_id)       REFERENCES users(id),
    FOREIGN KEY(sensor_log_id) REFERENCES sensor_logs(id)
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    entry_date        DATE    DEFAULT (date('now')),
    self_pain_rating  INTEGER CHECK(self_pain_rating BETWEEN 0 AND 10),
    notes             TEXT,
    ml_pain_avg       REAL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Patient-entered pain observations. Times are stored as UTC text and the
-- IANA timezone is retained so local daypart views remain interpretable.
CREATE TABLE IF NOT EXISTS pain_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    report_type       TEXT NOT NULL CHECK(report_type IN ('check_in', 'episode')),
    occurred_at_utc   TEXT NOT NULL,
    started_at_utc    TEXT,
    ended_at_utc      TEXT,
    timezone          TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    pain_rating       INTEGER NOT NULL CHECK(pain_rating BETWEEN 0 AND 10),
    pain_type         TEXT,
    qualities_json    TEXT,
    activity          TEXT,
    relief            TEXT,
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    CHECK(ended_at_utc IS NULL OR started_at_utc IS NOT NULL)
);

-- One-tap SOS records do not invent a pain rating or require the full diary form.
CREATE TABLE IF NOT EXISTS pain_sos_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    occurred_at_utc   TEXT NOT NULL,
    timezone          TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS pain_sos_sensor_samples (
    sos_event_id   INTEGER NOT NULL,
    sensor_log_id  INTEGER NOT NULL,
    sample_order   INTEGER NOT NULL,
    PRIMARY KEY(sos_event_id, sensor_log_id),
    UNIQUE(sos_event_id, sample_order),
    FOREIGN KEY(sos_event_id) REFERENCES pain_sos_events(id) ON DELETE CASCADE,
    FOREIGN KEY(sensor_log_id) REFERENCES sensor_logs(id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id),
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    REAL NOT NULL,
    valid_until   REAL NOT NULL,
    assigned_patients TEXT,
    demo_patient_id TEXT
);

-- Indexes for fast per-user queries
CREATE INDEX IF NOT EXISTS idx_sensor_user  ON sensor_logs(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_pain_user    ON pain_predictions(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_diary_user   ON diary_entries(user_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_pain_reports_user_time ON pain_reports(user_id, occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_pain_sos_user_time ON pain_sos_events(user_id, occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_pain_sos_samples_event_order ON pain_sos_sensor_samples(sos_event_id, sample_order);
"""

# ─── Connection helper ────────────────────────────────────────────────────────

def get_db():
    """Return a new SQLite connection (caller must close)."""
    conn = sqlite3.connect(DB_PATH, timeout=12, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables. Safe to call on every app start."""
    with closing(get_db()) as conn:
        conn.executescript(SCHEMA_SQL)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            legacy_device_column = "firebase_user_id"
            if "device_id" not in columns:
                if legacy_device_column in columns:
                    conn.execute(f"ALTER TABLE users RENAME COLUMN {legacy_device_column} TO device_id")
                else:
                    conn.execute("ALTER TABLE users ADD COLUMN device_id TEXT")
            if "supabase_id" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN supabase_id TEXT")
                conn.execute("CREATE UNIQUE INDEX idx_users_supabase ON users(supabase_id)")
            if "password_hash" in columns:
                conn.execute("ALTER TABLE users DROP COLUMN password_hash")
                conn.execute("DROP TABLE IF EXISTS password_reset_tokens")
            auth_columns = {row["name"] for row in conn.execute("PRAGMA table_info(auth_sessions)")}
            if "assigned_patients" not in auth_columns:
                conn.execute("ALTER TABLE auth_sessions ADD COLUMN assigned_patients TEXT")
            if "demo_patient_id" not in auth_columns:
                conn.execute("ALTER TABLE auth_sessions ADD COLUMN demo_patient_id TEXT")
            sos_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pain_sos_events)")}
            if "telemetry_json" not in sos_columns:
                conn.execute("ALTER TABLE pain_sos_events ADD COLUMN telemetry_json TEXT")


def link_supabase_user(identity, default_device):
    subject = identity["id"]
    email = identity["email"].strip().lower()
    metadata = identity.get("user_metadata") or {}
    name = str(metadata.get("username") or email.split("@")[0]).strip()[:60] or "Patient"
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM users WHERE supabase_id=?", (subject,)).fetchone()
        if row:
            return row["id"]
        existing = conn.execute("SELECT id, supabase_id FROM users WHERE lower(email)=?", (email,)).fetchone()
        if existing:
            if existing["supabase_id"] or not identity.get("email_confirmed_at"):
                raise ValueError("This email is already linked to another account.")
            conn.execute("UPDATE users SET supabase_id=? WHERE id=?", (subject, existing["id"]))
            return existing["id"]
        username = name
        suffix = 1
        while conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            suffix += 1
            username = f"{name} {suffix}"
        cursor = conn.execute(
            "INSERT INTO users (username, email, supabase_id, device_id) VALUES (?, ?, ?, ?)",
            (username, email, subject, default_device),
        )
        return cursor.lastrowid


def get_user_by_id(user_id):
    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_device_id(user_id, device_id):
    with closing(get_db()) as conn, conn:
        conn.execute("UPDATE users SET device_id=? WHERE id=?", (device_id, user_id))


def get_assigned_users(subjects):
    if not subjects:
        return []
    where = ", ".join("?" for _ in subjects)
    with closing(get_db()) as conn:
        return [dict(row) for row in conn.execute(
            f"SELECT id, username, supabase_id FROM users WHERE supabase_id IN ({where}) ORDER BY username",
            tuple(subjects),
        )]


def _assigned_patients(payload):
    access = doctor_access(payload.get("user"))
    return json.dumps(access) if access is not None else None


def save_auth_session(session_id, user_id, payload, valid_until):
    with closing(get_db()) as conn, conn:
        conn.execute("DELETE FROM auth_sessions WHERE valid_until <= ?", (time.time(),))
        conn.execute(
            """INSERT INTO auth_sessions
               (id, user_id, access_token, refresh_token, expires_at, valid_until, assigned_patients, demo_patient_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, payload["access_token"], payload["refresh_token"],
             time.time() + payload.get("expires_in", 3600), valid_until, _assigned_patients(payload), demo_patient(payload.get("user"))),
        )


def get_auth_session(session_id):
    if not session_id:
        return None
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE id=? AND valid_until > ?", (session_id, time.time())
        ).fetchone()
        return dict(row) if row else None


def refresh_auth_session(session_id, refresh_token, refresh):
    # Serialize token rotation across workers. Other requests reuse the newly
    # committed token instead of exchanging the same refresh token twice.
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM auth_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None or row["valid_until"] <= time.time():
            return None
        if row["refresh_token"] != refresh_token:
            return dict(row)
        payload = refresh(refresh_token)
        conn.execute(
            """UPDATE auth_sessions SET access_token=?, refresh_token=?, expires_at=?,
               assigned_patients=?, demo_patient_id=? WHERE id=?""",
            (payload["access_token"], payload["refresh_token"],
             time.time() + payload.get("expires_in", 3600), _assigned_patients(payload), demo_patient(payload.get("user")), session_id),
        )
        return dict(conn.execute("SELECT * FROM auth_sessions WHERE id=?", (session_id,)).fetchone())


def delete_auth_session(session_id):
    if session_id:
        with closing(get_db()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE id=?", (session_id,))


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
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def get_latest_sensor_log(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sensor_logs WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_sensor_logs(user_id, limit=200):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sensor_logs WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_sensor_logs(user_id, limit=10, within_seconds=30, at_or_before=None):
    """Get this user's newest samples within a short SOS capture window."""
    limit = max(1, min(int(limit), 50))
    within_seconds = max(1, min(int(within_seconds), 300))
    where = "user_id=? AND datetime(ts) >= datetime('now', ?)"
    params = [user_id, f"-{within_seconds} seconds"]
    if at_or_before:
        where += " AND datetime(ts) <= datetime(?)"
        params.append(at_or_before)
    params.append(limit)
    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM sensor_logs WHERE {where} ORDER BY id DESC LIMIT ?",
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
           VALUES (?,?,?,?,?)""",
        (user_id, sensor_log_id, pain_flag, pain_prob, pain_level),
    )
    conn.commit()
    conn.close()


def get_latest_pain_prediction(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pain_predictions WHERE user_id=? ORDER BY id DESC LIMIT 1",
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
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        sensor_log_id = cur.lastrowid
        prediction_id = conn.execute(
            """INSERT INTO pain_predictions
               (user_id, sensor_log_id, pain_flag, pain_probability, pain_level)
               VALUES (?,?,?,?,?)""",
            (
                user_id,
                sensor_log_id,
                int(prediction["pain_flag"]),
                float(prediction["pain_probability"]),
                float(prediction["pain_level"]),
            ),
        ).lastrowid
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
                  (strftime('%s','now') - strftime('%s', sl.ts)) AS age_seconds
           FROM sensor_logs sl
           LEFT JOIN pain_predictions pp ON pp.sensor_log_id = sl.id
           WHERE sl.user_id=?
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
           WHERE pp.user_id=?
           ORDER BY pp.id DESC
           LIMIT ?""",
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
           WHERE user_id=?
             AND ts >= date('now', ?)
           GROUP BY day
           ORDER BY day""",
        (user_id, f"-{days} days"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Diary Entries ────────────────────────────────────────────────────────────

def insert_diary_entry(user_id, self_rating, notes, ml_pain_avg=None):
    conn = get_db()
    conn.execute(
        """INSERT INTO diary_entries
           (user_id, self_pain_rating, notes, ml_pain_avg)
           VALUES (?,?,?,?)""",
        (user_id, self_rating, notes, ml_pain_avg),
    )
    conn.commit()
    conn.close()


def get_diary_entries(user_id, limit=60):
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM diary_entries WHERE user_id=?
           ORDER BY entry_date DESC LIMIT ?""",
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
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        conn.commit()
        return cur.lastrowid
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
           WHERE user_id=? AND datetime(occurred_at_utc) >= datetime('now', ?)
           ORDER BY occurred_at_utc DESC, id DESC LIMIT ?""",
        (user_id, f"-{int(days)} days", int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_pain_sos_event(user_id, occurred_at_utc, timezone_name, sensor_log_ids=None, telemetry=None):
    """Save an SOS event and link the nearby sensor samples that already arrived."""
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pain_sos_events (user_id, occurred_at_utc, timezone, telemetry_json)
               VALUES (?, ?, ?, ?)""",
            (user_id, occurred_at_utc, timezone_name, json.dumps(telemetry) if telemetry is not None else None),
        )
        event_id = cur.lastrowid
        for sample_order, sensor_log_id in enumerate(sensor_log_ids or []):
            conn.execute(
                """INSERT OR IGNORE INTO pain_sos_sensor_samples
                   (sos_event_id, sensor_log_id, sample_order)
                   SELECT ?, id, ? FROM sensor_logs WHERE id=? AND user_id=?""",
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
               WHERE user_id=? AND datetime(occurred_at_utc) >= datetime('now', ?)
               ORDER BY occurred_at_utc DESC, id DESC LIMIT ?
           ) e
           LEFT JOIN pain_sos_sensor_samples s ON s.sos_event_id=e.id
           LEFT JOIN sensor_logs sl ON sl.id=s.sensor_log_id AND sl.user_id=e.user_id
           ORDER BY e.occurred_at_utc DESC, e.id DESC, s.sample_order ASC
           """,
        (user_id, f"-{int(days)} days", int(limit)),
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
           WHERE user_id=? AND self_pain_rating IS NOT NULL
             AND datetime(created_at) >= datetime('now', ?)
           ORDER BY created_at DESC, id DESC LIMIT ?""",
        (user_id, f"-{int(days)} days", int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_hourly_sensor_trends(user_id, days=14):
    """Aggregate wearable values by local hour; timestamps are server receipt time.

    The dashboard timezone is Asia/Kolkata. Sensor firmware timestamps are
    millis since boot, so the existing SQLite UTC receipt timestamp is used.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT strftime('%Y-%m-%d %H:00:00', ts, '+330 minutes') AS hour_start_local,
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
           WHERE user_id=? AND ts >= datetime('now', ?)
           GROUP BY hour_start_local
           ORDER BY hour_start_local""",
        (user_id, f"-{int(days)} days"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


