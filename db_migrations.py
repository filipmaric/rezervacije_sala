"""Database migration helpers for legacy SQLite databases."""

from __future__ import annotations

import sqlite3

import db as mydb


ROOMS_SCHEMA = """
CREATE TABLE rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    capacity INTEGER DEFAULT 0,
    type TEXT,
    building_name TEXT NOT NULL,
    code TEXT UNIQUE,
    priority INTEGER DEFAULT (100)
)
"""


ATTENDANCE_RECORDS_SCHEMA = """
CREATE TABLE attendance_records (
    id INTEGER PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
    event_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    username TEXT NOT NULL,
    registration_source TEXT NOT NULL DEFAULT 'web' CHECK(registration_source IN ('web', 'android')),
    client_ip TEXT,
    client_latitude REAL,
    client_longitude REAL,
    geofence_checked INTEGER NOT NULL DEFAULT 0 CHECK(geofence_checked IN (0, 1)),
    failed_attempts_before_success INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_kind, event_id, event_date, username)
)
"""


ATTENDANCE_ATTEMPT_GEOFENCE_SETTINGS_SCHEMA = """
CREATE TABLE attendance_attempt_geofence_settings (
    event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
    event_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    PRIMARY KEY(event_kind, event_id, event_date)
)
"""


ATTENDANCE_ATTEMPT_FAILURES_SCHEMA = """
CREATE TABLE attendance_attempt_failures (
    attempt_token TEXT PRIMARY KEY,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

MATF_BUILDING_LOCATIONS = [
    {"building_name": "Студентски трг", "latitude": 44.8200177330261, "longitude": 20.45871822883615, "radius_m": 100},
    {"building_name": "Светог Николе", "latitude": 44.803735279889494, "longitude": 20.495233662987637, "radius_m": 100},
    {"building_name": "Јагићева", "latitude": 44.80004520753084, "longitude": 20.48487938340236, "radius_m": 100},
]

RESERVATIONS_SCHEMA = """
CREATE TABLE reservations (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL,
    username INTEGER,
    date TEXT NOT NULL,
    start_slot INTEGER NOT NULL CHECK(start_slot >= 0),
    end_slot INTEGER NOT NULL CHECK(end_slot > start_slot),
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT
)
"""

WEEKLY_SESSIONS_SCHEMA = """
CREATE TABLE weekly_sessions (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
    start_slot INTEGER NOT NULL,
    end_slot INTEGER NOT NULL,
    FOREIGN KEY(session_id) REFERENCES course_sessions(id),
    FOREIGN KEY(room_id) REFERENCES rooms(id)
)
"""


def table_columns(conn, table_name):
    """Return the set of column names for a table."""
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def table_exists(conn, table_name):
    """Return True when a table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def foreign_key_targets(conn, table_name):
    """Return the referenced table names for one table's foreign keys."""
    return {
        row[2]
        for row in conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    }


def _with_foreign_keys_disabled(conn, func):
    """Run a schema rewrite with foreign key enforcement temporarily disabled."""
    previous = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        return func()
    finally:
        conn.execute(f"PRAGMA foreign_keys = {int(previous)}")


def _ensure_base_schema(conn):
    """Create support tables and current columns before rebuilding legacy tables."""
    mydb.init_db(conn)


def migrate_spot_check_flags(conn):
    """Copy legacy spot-check flags from attendance_records into the new table."""
    columns = table_columns(conn, "attendance_records")
    legacy_columns = {
        "spot_check_flagged",
        "spot_check_teacher_username",
        "spot_check_flagged_at",
    }
    if not legacy_columns.issubset(columns):
        return 0

    if not table_exists(conn, "attendance_spot_check_flags"):
        conn.execute(
            """
            CREATE TABLE attendance_spot_check_flags (
                attendance_record_id INTEGER PRIMARY KEY,
                teacher_username TEXT NOT NULL,
                flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(attendance_record_id) REFERENCES attendance_records(id) ON DELETE CASCADE
            )
            """
        )

    before = conn.execute(
        "SELECT COUNT(*) FROM attendance_spot_check_flags"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO attendance_spot_check_flags (
            attendance_record_id, teacher_username, flagged_at
        )
        SELECT
            id,
            spot_check_teacher_username,
            COALESCE(spot_check_flagged_at, created_at, datetime('now'))
        FROM attendance_records
        WHERE spot_check_flagged = 1
        """
    )
    after = conn.execute(
        "SELECT COUNT(*) FROM attendance_spot_check_flags"
    ).fetchone()[0]
    return after - before


def migrate_attendance_attempt_geofence_settings(conn):
    """Rename legacy attendance-session geofence settings to the new table name."""
    old_name = "attendance_session_geofence_settings"
    new_name = "attendance_attempt_geofence_settings"
    if not table_exists(conn, old_name):
        return False

    if not table_exists(conn, new_name):
        conn.execute(ATTENDANCE_ATTEMPT_GEOFENCE_SETTINGS_SCHEMA)

    conn.execute(
        f"""
        INSERT OR REPLACE INTO {new_name} (
            event_kind, event_id, event_date, enabled
        )
        SELECT event_kind, event_id, event_date, enabled
        FROM {old_name}
        """
    )
    conn.execute(f"DROP TABLE {old_name}")
    return True


def migrate_attendance_attempt_failures(conn):
    """Rename legacy attendance-session failure rows to the new table name."""
    old_name = "attendance_session_failures"
    new_name = "attendance_attempt_failures"
    if not table_exists(conn, old_name):
        return False

    if not table_exists(conn, new_name):
        conn.execute(ATTENDANCE_ATTEMPT_FAILURES_SCHEMA)

    conn.execute(
        f"""
        INSERT OR REPLACE INTO {new_name} (
            attempt_token, failed_attempts, blocked, updated_at
        )
        SELECT session_token, failed_attempts, blocked, updated_at
        FROM {old_name}
        """
    )
    conn.execute(f"DROP TABLE {old_name}")
    return True


def rebuild_rooms_table(conn):
    """Drop the legacy rooms.location column and keep the current building_name column."""
    columns = table_columns(conn, "rooms")
    if "location" not in columns and "building_name" in columns:
        return False

    def rewrite():
        conn.execute("ALTER TABLE rooms RENAME TO rooms_legacy")
        conn.execute(ROOMS_SCHEMA)
        conn.execute(
            """
            INSERT INTO rooms (id, name, capacity, type, building_name, code, priority)
            SELECT
                id,
                name,
                capacity,
                type,
                COALESCE(building_name, location),
                code,
                priority
            FROM rooms_legacy
            """
        )
        conn.execute("DROP TABLE rooms_legacy")

    _with_foreign_keys_disabled(conn, rewrite)
    return True


def rebuild_reservations_table(conn):
    """Recreate reservations so their foreign key points back to rooms."""
    targets = foreign_key_targets(conn, "reservations")
    if targets == {"rooms"}:
        return False

    def rewrite():
        conn.execute("ALTER TABLE reservations RENAME TO reservations_legacy")
        conn.execute(
            """
            CREATE TABLE reservations (
                id INTEGER PRIMARY KEY,
                room_id INTEGER NOT NULL,
                username INTEGER,
                date TEXT NOT NULL,
                start_slot INTEGER NOT NULL CHECK(start_slot >= 0),
                end_slot INTEGER NOT NULL CHECK(end_slot > start_slot),
                description TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE RESTRICT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO reservations (
                id, room_id, username, date, start_slot, end_slot, description, created_at
            )
            SELECT id, room_id, username, date, start_slot, end_slot, description, created_at
            FROM reservations_legacy
            """
        )
        conn.execute("DROP TABLE reservations_legacy")

    _with_foreign_keys_disabled(conn, rewrite)
    return True


def rebuild_weekly_sessions_table(conn):
    """Recreate weekly_sessions so their foreign key points back to rooms."""
    targets = foreign_key_targets(conn, "weekly_sessions")
    if targets == {"rooms", "course_sessions"} or targets == {"course_sessions", "rooms"}:
        return False

    def rewrite():
        conn.execute("ALTER TABLE weekly_sessions RENAME TO weekly_sessions_legacy")
        conn.execute(
            """
            CREATE TABLE weekly_sessions (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                room_id INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
                start_slot INTEGER NOT NULL,
                end_slot INTEGER NOT NULL,
                FOREIGN KEY(session_id) REFERENCES course_sessions(id),
                FOREIGN KEY(room_id) REFERENCES rooms(id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_sessions (
                id, session_id, room_id, day_of_week, start_slot, end_slot
            )
            SELECT id, session_id, room_id, day_of_week, start_slot, end_slot
            FROM weekly_sessions_legacy
            """
        )
        conn.execute("DROP TABLE weekly_sessions_legacy")

    _with_foreign_keys_disabled(conn, rewrite)
    return True


def seed_matf_building_locations(conn):
    """Insert the standard MATF building geofences into building_locations."""
    if not table_exists(conn, "building_locations"):
        return False

    before = conn.execute("SELECT COUNT(*) FROM building_locations").fetchone()[0]
    conn.executemany(
        """
        INSERT OR REPLACE INTO building_locations (
            building_name, latitude, longitude, radius_m
        ) VALUES (:building_name, :latitude, :longitude, :radius_m)
        """,
        MATF_BUILDING_LOCATIONS,
    )
    after = conn.execute("SELECT COUNT(*) FROM building_locations").fetchone()[0]
    return after != before


def rebuild_attendance_records_table(conn):
    """Drop the legacy spot-check columns from attendance_records."""
    columns = table_columns(conn, "attendance_records")
    legacy_columns = {
        "spot_check_flagged",
        "spot_check_teacher_username",
        "spot_check_flagged_at",
    }
    current_columns = {
        "id",
        "event_kind",
        "event_id",
        "event_date",
        "username",
        "registration_source",
        "client_ip",
        "client_latitude",
        "client_longitude",
        "geofence_checked",
        "failed_attempts_before_success",
        "created_at",
    }
    if legacy_columns.isdisjoint(columns) and current_columns.issubset(columns):
        return False

    conn.execute("ALTER TABLE attendance_records RENAME TO attendance_records_legacy")
    conn.execute(ATTENDANCE_RECORDS_SCHEMA)

    legacy_columns = table_columns(conn, "attendance_records_legacy")
    def expr(column, fallback):
        return column if column in legacy_columns else fallback

    conn.execute(
        f"""
        INSERT INTO attendance_records (
            id,
            event_kind,
            event_id,
            event_date,
            username,
            registration_source,
            client_ip,
            client_latitude,
            client_longitude,
            geofence_checked,
            failed_attempts_before_success,
            created_at
        )
        SELECT
            id,
            event_kind,
            event_id,
            event_date,
            username,
            {expr("registration_source", "'web'")},
            {expr("client_ip", "NULL")},
            {expr("client_latitude", "NULL")},
            {expr("client_longitude", "NULL")},
            {expr("geofence_checked", "0")},
            {expr("failed_attempts_before_success", "0")},
            {expr("created_at", "datetime('now')")}
        FROM attendance_records_legacy
        """
    )

    migrate_spot_check_flags(conn)
    conn.execute("DROP TABLE attendance_records_legacy")
    return True


def migrate_database(conn):
    """Bring a legacy database up to the current schema."""
    geofence_settings_changed = False
    failures_changed = False
    _ensure_base_schema(conn)
    rooms_changed = rebuild_rooms_table(conn)
    if rooms_changed:
        conn.commit()

    _ensure_base_schema(conn)
    reservations_changed = rebuild_reservations_table(conn)
    if reservations_changed:
        conn.commit()

    _ensure_base_schema(conn)
    weekly_sessions_changed = rebuild_weekly_sessions_table(conn)
    if weekly_sessions_changed:
        conn.commit()

    _ensure_base_schema(conn)
    building_locations_changed = seed_matf_building_locations(conn)
    if building_locations_changed:
        conn.commit()

    _ensure_base_schema(conn)
    attendance_changed = rebuild_attendance_records_table(conn)
    if attendance_changed:
        conn.commit()

    _ensure_base_schema(conn)
    geofence_settings_changed = migrate_attendance_attempt_geofence_settings(conn)
    if geofence_settings_changed:
        conn.commit()

    _ensure_base_schema(conn)
    failures_changed = migrate_attendance_attempt_failures(conn)
    if failures_changed:
        conn.commit()

    _ensure_base_schema(conn)
    return {
        "rooms_changed": rooms_changed,
        "reservations_changed": reservations_changed,
        "weekly_sessions_changed": weekly_sessions_changed,
        "building_locations_changed": building_locations_changed,
        "attendance_changed": attendance_changed,
        "geofence_settings_changed": geofence_settings_changed,
        "failures_changed": failures_changed,
    }
