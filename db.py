"""SQLite database helpers for the classroom reservation app."""

import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from flask import g

import config


def _app_module():
    """Return the loaded app module when available."""
    return sys.modules.get("app") or sys.modules.get("__main__")


def _database_path():
    """Resolve the active database path from the app module or config defaults."""
    core = _app_module()
    if core is not None and hasattr(core, "DATABASE"):
        return core.DATABASE
    return config.DATABASE


def _schema_path():
    """Resolve the active schema path from the app module or config defaults."""
    core = _app_module()
    if core is not None and hasattr(core, "SCHEMA_FILE"):
        return core.SCHEMA_FILE
    return config.SCHEMA_FILE


def _database_has_schema(conn):
    """Check whether the main application schema is already present."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
    ).fetchone()
    return bool(row)


def _ensure_attendance_schema(conn):
    """Create the attendance tables if they are missing."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY,
            event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
            event_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            username TEXT NOT NULL,
            registration_source TEXT NOT NULL DEFAULT 'web' CHECK(registration_source IN ('web', 'android')),
            client_ip TEXT,
            client_latitude REAL,
            client_longitude REAL,
            geofence_checked INTEGER NOT NULL DEFAULT 0 CHECK(geofence_checked IN (0,1)),
            failed_attempts_before_success INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(event_kind, event_id, event_date, username)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_records_event
            ON attendance_records(event_kind, event_id, event_date);
        CREATE INDEX IF NOT EXISTS idx_attendance_records_username_event
            ON attendance_records(username, event_kind, event_id, event_date);

        CREATE TABLE IF NOT EXISTS attendance_attempt_geofence_settings (
            event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
            event_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            PRIMARY KEY(event_kind, event_id, event_date)
        );

        CREATE TABLE IF NOT EXISTS attendance_attempt_failures (
            attempt_token TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS attendance_spot_check_flags (
            attendance_record_id INTEGER PRIMARY KEY,
            teacher_username TEXT NOT NULL,
            flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(attendance_record_id) REFERENCES attendance_records(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_weekly_sessions_day_room_start
            ON weekly_sessions(day_of_week, room_id, start_slot);
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(attendance_records)").fetchall()
    }
    if "registration_source" not in columns:
        conn.execute(
            "ALTER TABLE attendance_records ADD COLUMN registration_source TEXT NOT NULL DEFAULT 'web'"
        )
    if "client_ip" not in columns:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN client_ip TEXT")
    if "client_latitude" not in columns:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN client_latitude REAL")
    if "client_longitude" not in columns:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN client_longitude REAL")
    if "geofence_checked" not in columns:
        conn.execute(
            "ALTER TABLE attendance_records ADD COLUMN geofence_checked INTEGER NOT NULL DEFAULT 0"
        )
    if "failed_attempts_before_success" not in columns:
        conn.execute(
            "ALTER TABLE attendance_records ADD COLUMN failed_attempts_before_success INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_attempt_geofence_settings (
            event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
            event_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            PRIMARY KEY(event_kind, event_id, event_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_spot_check_flags (
            attendance_record_id INTEGER PRIMARY KEY,
            teacher_username TEXT NOT NULL,
            flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(attendance_record_id) REFERENCES attendance_records(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attendance_records_username_event
            ON attendance_records(username, event_kind, event_id, event_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_weekly_sessions_day_room_start
            ON weekly_sessions(day_of_week, room_id, start_slot)
        """
    )
    conn.commit()


def _ensure_mobile_auth_schema(conn):
    """Create the Android auth tables if they are missing."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mobile_auth_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radius_username TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mobile_auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            device_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_reason TEXT,
            FOREIGN KEY(user_id) REFERENCES mobile_auth_users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_mobile_auth_sessions_user_id
            ON mobile_auth_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_mobile_auth_sessions_token_hash
            ON mobile_auth_sessions(token_hash);

        CREATE TABLE IF NOT EXISTS mobile_auth_device_login_policies (
            device_id TEXT PRIMARY KEY,
            last_username TEXT NOT NULL,
            last_login_date TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_mobile_auth_device_login_policies_login_date
            ON mobile_auth_device_login_policies(last_login_date);
        """
    )
    conn.commit()


def _ensure_student_directory_schema(conn):
    """Create the student directory table if it is missing."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            username TEXT PRIMARY KEY,
            student_index TEXT NOT NULL,
            surname TEXT NOT NULL,
            given_name TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_students_student_index
            ON students(student_index);
        """
    )
    conn.commit()


def _ensure_building_locations_schema(conn):
    """Create the building locations table."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS building_locations (
            building_name TEXT PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m REAL NOT NULL CHECK(radius_m > 0)
        );
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(building_locations)").fetchall()
    }
    if "building_name" not in columns and "name" in columns:
        conn.execute("ALTER TABLE building_locations ADD COLUMN building_name TEXT")
        conn.execute(
            """
            UPDATE building_locations
            SET building_name = name
            WHERE building_name IS NULL
            """
        )
    conn.commit()


def _ensure_room_building_name_schema(conn):
    """Create the room building-name column if an older database still uses location."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(rooms)").fetchall()
    }
    if "building_name" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN building_name TEXT")
        if "location" in columns:
            conn.execute(
                """
                UPDATE rooms
                SET building_name = location
                WHERE building_name IS NULL
                """
            )
    conn.commit()


def _ensure_extra_schemas(conn):
    """Create the add-on tables used by attendance and Android auth."""
    _ensure_attendance_schema(conn)
    _ensure_mobile_auth_schema(conn)
    _ensure_student_directory_schema(conn)
    _ensure_building_locations_schema(conn)
    _ensure_room_building_name_schema(conn)


def ensure_student_directory_schema(conn):
    """Public helper used by import scripts to ensure the student directory exists."""
    _ensure_student_directory_schema(conn)


def building_locations_for_room_building_name(building_name):
    """Return building geofences for one room location label."""
    normalized = str(building_name or "").strip()
    if not normalized:
        return []
    return [
        dict(row)
        for row in query_db(
            """
            SELECT building_name,
                   building_name AS name,
                   latitude,
                   longitude,
                   radius_m
            FROM building_locations
            WHERE building_name = ?
            ORDER BY building_name
            """,
            (normalized,),
        )
    ]


def building_locations_for_room_location(room_location):
    """Backward-compatible wrapper for room building-name lookups."""
    return building_locations_for_room_building_name(room_location)


def building_locations_all():
    """Return all configured building geofences."""
    return [
        dict(row)
        for row in query_db(
            """
            SELECT building_name,
                   building_name AS name,
                   latitude,
                   longitude,
                   radius_m
            FROM building_locations
            ORDER BY building_name
            """
        )
    ]


def _utcnow():
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _isoformat(dt):
    """Render a UTC timestamp in ISO 8601 format."""
    return dt.astimezone(timezone.utc).isoformat()


def hash_token(token):
    """Hash an opaque bearer token before storing it in SQLite."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_app(app):
    """Register database teardown hooks on the Flask application."""
    app.teardown_appcontext(close_connection)


def init_db(conn=None):
    """Initialize the database from schema.sql and add attendance tables."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(_database_path(), detect_types=sqlite3.PARSE_DECLTYPES)
        close_conn = True

    try:
        if _database_has_schema(conn):
            _ensure_extra_schemas(conn)
            return False

        schema_path = _schema_path()
        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path, encoding="utf-8") as f:
            conn.executescript(f.read())
        _ensure_extra_schemas(conn)
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def get_db():
    """Return the current SQLite connection, creating one for this request if needed."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(_database_path(), detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        # enable WAL mode for concurrent access (Gunicorn)
        db.execute("PRAGMA journal_mode=WAL;")
        # ensure foreign keys are enforced
        db.execute("PRAGMA foreign_keys = ON;")
        init_db(db)
        _ensure_extra_schemas(db)
    return db


def close_connection(exception):
    """Close the request-scoped SQLite connection at teardown."""
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    """Run a SELECT query and return either one row or all rows."""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=(), commit=True):
    """Run a write query and optionally commit it immediately."""
    conn = get_db()
    cur = conn.execute(query, args)
    if commit:
        conn.commit()
    return cur.lastrowid


def mobile_auth_get_or_create_user(radius_username):
    """Return the Android-auth user row for a RADIUS username."""
    now = _isoformat(_utcnow())
    conn = get_db()
    row = conn.execute(
        "SELECT id, radius_username FROM mobile_auth_users WHERE radius_username = ?",
        (radius_username,),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO mobile_auth_users (radius_username, created_at) VALUES (?, ?)",
            (radius_username, now),
        )
        conn.commit()
        user_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT id, radius_username FROM mobile_auth_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return row


def mobile_auth_get_user_by_id(user_id):
    """Return a mobile-auth user row by id."""
    return query_db(
        "SELECT id, radius_username FROM mobile_auth_users WHERE id = ?",
        (user_id,),
        one=True,
    )


def mobile_auth_get_device_login_policy(device_id):
    """Return the latest username recorded for a device."""
    return query_db(
        """
        SELECT device_id, last_username, last_login_date, updated_at
        FROM mobile_auth_device_login_policies
        WHERE device_id = ?
        """,
        (device_id,),
        one=True,
    )


def mobile_auth_assert_device_login_allowed(device_id, username):
    """Reject a different username on the same device within one UTC day."""
    policy = mobile_auth_get_device_login_policy(device_id)
    if policy is None:
        return

    current_day = _utcnow().date().isoformat()
    if policy["last_login_date"] == current_day and policy["last_username"] != username.strip():
        raise RuntimeError(
            f'This phone is already used by "{policy["last_username"]}" today. Try again tomorrow.'
        )


def mobile_auth_record_device_login(device_id, username):
    """Persist the username that last logged in from a device."""
    now = _isoformat(_utcnow())
    current_day = _utcnow().date().isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO mobile_auth_device_login_policies (
            device_id, last_username, last_login_date, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            last_username = excluded.last_username,
            last_login_date = excluded.last_login_date,
            updated_at = excluded.updated_at
        """,
        (device_id, username.strip(), current_day, now),
    )
    conn.commit()
    return mobile_auth_get_device_login_policy(device_id)


def mobile_auth_revoke_active_sessions(user_id, reason, exclude_session_id=None):
    """Revoke all active Android sessions for a user."""
    now = _isoformat(_utcnow())
    sql = """
        UPDATE mobile_auth_sessions
        SET revoked_at = ?, revoked_reason = ?
        WHERE user_id = ?
          AND revoked_at IS NULL
          AND expires_at > ?
    """
    params = [now, reason, user_id, now]
    if exclude_session_id is not None:
        sql += " AND id != ?"
        params.append(exclude_session_id)
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    return int(cur.rowcount)


def mobile_auth_create_session(user_id, device_id, device_name, token_hash, session_days):
    """Create a new Android session and return the stored row."""
    now = _utcnow()
    created_at = _isoformat(now)
    expires_at = _isoformat(now + timedelta(days=session_days))
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO mobile_auth_sessions (
            user_id, device_id, device_name, token_hash,
            created_at, last_seen_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, device_id, device_name, token_hash, created_at, created_at, expires_at),
    )
    conn.commit()
    return mobile_auth_get_session_by_id(int(cur.lastrowid))


def mobile_auth_touch_session(session_id):
    """Refresh the last-seen timestamp for an Android session."""
    conn = get_db()
    conn.execute(
        "UPDATE mobile_auth_sessions SET last_seen_at = ? WHERE id = ?",
        (_isoformat(_utcnow()), session_id),
    )
    conn.commit()
    return mobile_auth_get_session_by_id(session_id)


def mobile_auth_revoke_session(session_id, reason):
    """Mark an Android session as revoked."""
    conn = get_db()
    conn.execute(
        "UPDATE mobile_auth_sessions SET revoked_at = ?, revoked_reason = ? WHERE id = ?",
        (_isoformat(_utcnow()), reason, session_id),
    )
    conn.commit()


def mobile_auth_get_session_by_token(token):
    """Find an Android session by its raw bearer token."""
    token_hash = hash_token(token)
    return query_db(
        "SELECT * FROM mobile_auth_sessions WHERE token_hash = ?",
        (token_hash,),
        one=True,
    )


def mobile_auth_get_session_by_id(session_id):
    """Find an Android session by id."""
    return query_db(
        "SELECT * FROM mobile_auth_sessions WHERE id = ?",
        (session_id,),
        one=True,
    )


def student_identity_for_username(username):
    """Return the stored student identity for one username or a fallback label."""
    row = query_db(
        """
        SELECT username, student_index, surname, given_name
        FROM students
        WHERE username = ?
        """,
        (username,),
        one=True,
    )
    if not row:
        return {
            "username": username,
            "student_index": None,
            "student_name": None,
            "student_label": "Непознато",
        }

    given_name = (row["given_name"] or "").strip()
    surname = (row["surname"] or "").strip()
    full_name = " ".join(part for part in (given_name, surname) if part).strip()
    student_index = (row["student_index"] or "").strip()
    student_label = "Непознато"
    if full_name and student_index:
        student_label = f"{full_name} ({student_index})"
    elif full_name:
        student_label = full_name
    elif student_index:
        student_label = student_index

    return {
        "username": row["username"],
        "student_index": student_index or None,
        "student_name": full_name or None,
        "student_label": student_label,
    }
