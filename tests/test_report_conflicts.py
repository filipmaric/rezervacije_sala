import datetime as dt
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def init_conflict_db(db_path):
    target_day = dt.date(2026, 3, 10)
    conn = sqlite3.connect(db_path)
    with conn:
        with open(REPO_ROOT / "schema.sql", encoding="utf-8") as f:
            conn.executescript(f.read())

        conn.execute(
            """
            INSERT INTO rooms (name, capacity, type, building_name, code, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Room 406", 50, "lecture", "A", "406", 1),
        )
        conn.execute(
            """
            INSERT INTO rooms (name, capacity, type, building_name, code, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Room 407", 50, "lecture", "A", "407", 2),
        )
        conn.execute(
            """
            INSERT INTO semesters (name, start_date, end_date)
            VALUES (?, ?, ?)
            """,
            ("Spring 2026", "2026-03-01", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            """,
            (target_day.isoformat(), 1, target_day.weekday()),
        )
        conn.execute(
            "INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "alice", target_day.isoformat(), 9, 11, "morning"),
        )
        conn.execute(
            "INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "bob", target_day.isoformat(), 10, 12, "overlap"),
        )
        conn.execute(
            "INSERT INTO teachers (name, username) VALUES (?, ?)",
            ("Prof", "prof"),
        )
        conn.execute(
            "INSERT INTO courses (name, code) VALUES (?, ?)",
            ("NumericalMethods", "ALG1"),
        )
        conn.execute(
            """
            INSERT INTO course_sessions (course_id, teacher_id, semester_id, type)
            VALUES (?, ?, ?, ?)
            """,
            (1, 1, 1, "lecture"),
        )
        conn.execute(
            """
            INSERT INTO weekly_sessions (session_id, room_id, day_of_week, start_slot, end_slot)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, 2, target_day.weekday(), 9, 11),
        )
        conn.execute(
            "INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description) VALUES (?, ?, ?, ?, ?, ?)",
            (2, "charlie", target_day.isoformat(), 10, 12, "weekly overlap"),
        )
    conn.close()


def run_script(db_path, now):
    return subprocess.run(
        [
            PYTHON,
            "scripts/report_conflicts.py",
            str(db_path),
            "--now",
            now,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def test_report_conflicts_finds_future_overlaps(tmp_path):
    db_path = tmp_path / "conflicts.db"
    init_conflict_db(db_path)

    result = run_script(db_path, "2026-03-10T08:00:00")

    assert result.returncode == 1
    assert "Conflicts found after 2026-03-10T08:00" in result.stdout
    assert "reservation #1" in result.stdout
    assert "reservation #2" in result.stdout
    assert "weekly session #1" in result.stdout
    assert "weekly overlap" in result.stdout


def test_report_conflicts_clean_db_exits_zero(tmp_path):
    db_path = tmp_path / "clean.db"
    target_day = dt.date(2026, 3, 10)

    conn = sqlite3.connect(db_path)
    with conn:
        with open(REPO_ROOT / "schema.sql", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute(
            """
            INSERT INTO rooms (name, capacity, type, building_name, code, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Room 406", 50, "lecture", "A", "406", 1),
        )
        conn.execute(
            """
            INSERT INTO semesters (name, start_date, end_date)
            VALUES (?, ?, ?)
            """,
            ("Spring 2026", "2026-03-01", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            """,
            (target_day.isoformat(), 1, target_day.weekday()),
        )
    conn.close()

    result = run_script(db_path, "2026-03-10T08:00:00")

    assert result.returncode == 0
    assert "No conflicts found after 2026-03-10T08:00" in result.stdout
