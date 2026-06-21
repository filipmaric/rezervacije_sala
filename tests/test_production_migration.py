import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def table_columns(conn, table_name):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def foreign_key_targets(conn, table_name):
    return {
        row[2]
        for row in conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    }


def create_legacy_production_db(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys = OFF;

            CREATE TABLE semesters (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL
            );

            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE
            );

            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT
            );

            CREATE TABLE course_sessions (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                semester_id INTEGER NOT NULL,
                type TEXT NOT NULL DEFAULT 'lecture',
                FOREIGN KEY(course_id) REFERENCES courses(id),
                FOREIGN KEY(teacher_id) REFERENCES teachers(id),
                FOREIGN KEY(semester_id) REFERENCES semesters(id)
            );

            CREATE TABLE rooms (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                capacity INTEGER DEFAULT 0,
                type TEXT,
                location TEXT NOT NULL,
                code TEXT UNIQUE,
                priority INTEGER DEFAULT 100
            );

            CREATE TABLE reservations (
                id INTEGER PRIMARY KEY,
                room_id INTEGER NOT NULL,
                username INTEGER,
                date TEXT NOT NULL,
                start_slot INTEGER NOT NULL CHECK(start_slot >= 0),
                end_slot INTEGER NOT NULL CHECK(end_slot > start_slot),
                description TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE RESTRICT
            );

            CREATE TABLE weekly_sessions (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                room_id INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
                start_slot INTEGER NOT NULL,
                end_slot INTEGER NOT NULL,
                FOREIGN KEY(session_id) REFERENCES course_sessions(id),
                FOREIGN KEY(room_id) REFERENCES rooms(id)
            );

            CREATE TABLE attendance_records (
                id INTEGER PRIMARY KEY,
                event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
                event_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                username TEXT NOT NULL,
                spot_check_flagged INTEGER NOT NULL DEFAULT 0,
                spot_check_teacher_username TEXT,
                spot_check_flagged_at TEXT,
                UNIQUE(event_kind, event_id, event_date, username)
            );

            CREATE TABLE attendance_session_geofence_settings (
                event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
                event_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                PRIMARY KEY(event_kind, event_id, event_date)
            );

            CREATE TABLE attendance_session_failures (
                session_token TEXT PRIMARY KEY,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            INSERT INTO semesters (id, name, start_date, end_date)
            VALUES (1, 'Current 2026', '2026-01-01', '2026-12-31');

            INSERT INTO teachers (id, name, username)
            VALUES (1, 'Prof', 'alice');

            INSERT INTO courses (id, name, code)
            VALUES (1, 'NumericalMethods', 'NUM');

            INSERT INTO course_sessions (id, course_id, teacher_id, semester_id, type)
            VALUES (1, 1, 1, 1, 'lecture');

            INSERT INTO rooms (id, name, capacity, type, location, code, priority)
            VALUES (1, 'R1', 50, 'lecture', 'A', NULL, 1);

            INSERT INTO reservations (id, room_id, username, date, start_slot, end_slot, description)
            VALUES (1, 1, 'user', '2026-03-09', 10, 12, 'test');

            INSERT INTO weekly_sessions (id, session_id, room_id, day_of_week, start_slot, end_slot)
            VALUES (1, 1, 1, 1, 10, 12);

            INSERT INTO attendance_records (
                id,
                event_kind,
                event_id,
                event_date,
                username,
                spot_check_flagged,
                spot_check_teacher_username,
                spot_check_flagged_at
            )
            VALUES (1, 'weekly', 1, '2026-03-09', 'student1', 1, 'alice', '2026-03-09T10:00:00');

            INSERT INTO attendance_session_geofence_settings (
                event_kind, event_id, event_date, enabled
            )
            VALUES ('weekly', 1, '2026-03-09', 1);

            INSERT INTO attendance_session_failures (
                session_token, failed_attempts, blocked, updated_at
            )
            VALUES ('token', 1, 0, '2026-03-09T10:00:00');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_production_db_upgrades_legacy_schema(tmp_path):
    db_path = tmp_path / "production.db"
    create_legacy_production_db(db_path)

    before = sqlite3.connect(db_path)
    try:
        before_rooms = before.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        before_attendance = before.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
        before_weekly = before.execute("SELECT COUNT(*) FROM weekly_sessions").fetchone()[0]
    finally:
        before.close()

    result = subprocess.run(
        [PYTHON, "scripts/migrate_production_db.py", str(db_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Migration completed" in result.stdout

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rooms_columns = table_columns(conn, "rooms")
        attendance_columns = table_columns(conn, "attendance_records")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "building_name" in rooms_columns
        assert "location" not in rooms_columns
        assert "client_latitude" in attendance_columns
        assert "client_longitude" in attendance_columns
        assert "geofence_checked" in attendance_columns
        assert "spot_check_flagged" not in attendance_columns
        assert "spot_check_teacher_username" not in attendance_columns
        assert "spot_check_flagged_at" not in attendance_columns
        assert "building_locations" in tables
        assert "attendance_attempt_geofence_settings" in tables
        assert "attendance_attempt_failures" in tables
        assert "attendance_spot_check_flags" in tables
        assert "attendance_session_geofence_settings" not in tables
        assert "attendance_session_failures" not in tables
        building_names = {
            row[0]
            for row in conn.execute(
                "SELECT building_name FROM building_locations"
            ).fetchall()
        }
        assert building_names == {"Студентски трг", "Светог Николе", "Јагићева"}
        assert foreign_key_targets(conn, "reservations") == {"rooms"}
        assert foreign_key_targets(conn, "weekly_sessions") == {"course_sessions", "rooms"}
        assert before_rooms == conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        assert before_attendance == conn.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
        assert before_weekly == conn.execute("SELECT COUNT(*) FROM weekly_sessions").fetchone()[0]
    finally:
        conn.close()
