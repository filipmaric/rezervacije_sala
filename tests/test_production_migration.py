import shutil
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


def test_migrate_production_db_upgrades_legacy_schema(tmp_path):
    source = REPO_ROOT / "production.db"
    db_path = tmp_path / "production.db"
    shutil.copy2(source, db_path)

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
