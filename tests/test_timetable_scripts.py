import datetime as dt
import csv
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DAY_TOKENS = {
    0: "pon",
    1: "uto",
    2: "sre",
    3: "cet",
    4: "pet",
    5: "sub",
    6: "ned",
}


def init_timetable_db(db_path, target_day=None):
    if target_day is None:
        target_day = dt.date(2026, 3, 10)

    conn = sqlite3.connect(db_path)
    with conn:
        with open(REPO_ROOT / "schema.sql", encoding="utf-8") as f:
            conn.executescript(f.read())

        conn.execute(
            """
            INSERT INTO rooms (name, capacity, type, location, code, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Room 406", 50, "lecture", "A", "406", 1),
        )
        conn.execute(
            """
            INSERT INTO rooms (name, capacity, type, location, code, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Room 407", 50, "lecture", "A", "407", 2),
        )
        conn.execute(
            """
            INSERT INTO semesters (name, start_date, end_date)
            VALUES (?, ?, ?)
            """,
            ("Winter 2026", "2026-01-01", "2026-12-31"),
        )
        conn.execute(
            """
            INSERT INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            """,
            (target_day.isoformat(), 1, target_day.weekday()),
        )
    conn.close()


def write_metadata(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerows(rows)


def run_script(args, input_text=None):
    return subprocess.run(
        [PYTHON, *args],
        cwd=REPO_ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )


def fetch_one(db_path, query):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchone()
    finally:
        conn.close()


def fetch_all(db_path, query):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def test_import_timetable_replaces_existing_schedule(tmp_path):
    db_path = tmp_path / "timetable.db"
    teachers = tmp_path / "teachers.csv"
    subjects = tmp_path / "subjects.csv"

    init_timetable_db(db_path)
    write_metadata(teachers, [("profuser", "Prof Full Name")])
    write_metadata(
        subjects,
        [
            ("MAT1.p", "Mathematics 1"),
            ("ALG1.p", "Algebra 1"),
        ],
    )

    run_script(
        [
            "scripts/import_timetable.py",
            str(db_path),
            "--teachers",
            str(teachers),
            "--subjects",
            str(subjects),
        ],
        input_text="profuser_3A_MAT1.p_pon_8_10_406\n",
    )

    row = fetch_one(
        db_path,
        """
        SELECT c.code, ws.day_of_week, ws.start_slot, ws.end_slot, r.code AS room_code
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN rooms r ON r.id = ws.room_id
        """,
    )
    assert row["code"] == "MAT1"
    assert row["day_of_week"] == 0
    assert row["start_slot"] == 8
    assert row["end_slot"] == 10
    assert row["room_code"] == "406"

    run_script(
        [
            "scripts/import_timetable.py",
            str(db_path),
            "--teachers",
            str(teachers),
            "--subjects",
            str(subjects),
        ],
        input_text="profuser_3A_ALG1.p_uto_10_12_407\n",
    )

    weekly_sessions = fetch_all(db_path, "SELECT * FROM weekly_sessions")
    course_sessions = fetch_all(db_path, "SELECT * FROM course_sessions")

    assert len(weekly_sessions) == 1
    assert len(course_sessions) == 1

    row = fetch_one(
        db_path,
        """
        SELECT c.code, ws.day_of_week, ws.start_slot, ws.end_slot, r.code AS room_code
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN rooms r ON r.id = ws.room_id
        """,
    )
    assert row["code"] == "ALG1"
    assert row["day_of_week"] == 1
    assert row["start_slot"] == 10
    assert row["end_slot"] == 12
    assert row["room_code"] == "407"


def test_update_timetable_changes_existing_session(tmp_path):
    db_path = tmp_path / "timetable.db"
    teachers = tmp_path / "teachers.csv"
    subjects = tmp_path / "subjects.csv"
    timetable = tmp_path / "timetable.txt"

    init_timetable_db(db_path)
    write_metadata(teachers, [("profuser", "Prof Full Name")])
    write_metadata(subjects, [("MAT1.p", "Mathematics 1")])

    run_script(
        [
            "scripts/import_timetable.py",
            str(db_path),
            "--teachers",
            str(teachers),
            "--subjects",
            str(subjects),
        ],
        input_text="profuser_3A_MAT1.p_pon_8_10_406\n",
    )

    timetable.write_text("profuser_3A_MAT1.p_sre_12_14_407\n", encoding="utf-8")

    run_script([
        "scripts/update_timetable.py",
        str(timetable),
        str(db_path),
    ])

    rows = fetch_all(
        db_path,
        """
        SELECT c.code, ws.day_of_week, ws.start_slot, ws.end_slot, r.code AS room_code
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN rooms r ON r.id = ws.room_id
        """,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "MAT1"
    assert row["day_of_week"] == 2
    assert row["start_slot"] == 12
    assert row["end_slot"] == 14
    assert row["room_code"] == "407"


def test_update_timetable_reports_conflicts_and_can_be_skipped(tmp_path):
    db_path = tmp_path / "timetable.db"
    teachers = tmp_path / "teachers.csv"
    subjects = tmp_path / "subjects.csv"
    timetable = tmp_path / "timetable.txt"
    target_day = dt.date.today() + dt.timedelta(days=14)
    token = DAY_TOKENS[target_day.weekday()]

    init_timetable_db(db_path, target_day=target_day)
    write_metadata(teachers, [("profuser", "Prof Full Name")])
    write_metadata(subjects, [("MAT1.p", "Mathematics 1")])

    run_script(
        [
            "scripts/import_timetable.py",
            str(db_path),
            "--teachers",
            str(teachers),
            "--subjects",
            str(subjects),
        ],
        input_text=f"profuser_3A_MAT1.p_{token}_8_10_406\n",
    )

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (2, "alice", target_day.isoformat(), 8, 10, "future overlap"),
        )
    conn.close()

    timetable.write_text(
        f"profuser_3A_MAT1.p_{token}_8_10_407\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [PYTHON, "scripts/update_timetable.py", str(timetable), str(db_path)],
        cwd=REPO_ROOT,
        input="n\n",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Update would create future conflicts:" in result.stdout
    assert "future overlap" in result.stdout
    assert "Update skipped." in result.stdout

    row = fetch_one(
        db_path,
        """
        SELECT r.code AS room_code
        FROM weekly_sessions ws
        JOIN rooms r ON ws.room_id = r.id
        WHERE ws.id = 1
        """,
    )
    assert row["room_code"] == "406"


def test_update_timetable_reports_conflicts_and_can_be_confirmed(tmp_path):
    db_path = tmp_path / "timetable.db"
    teachers = tmp_path / "teachers.csv"
    subjects = tmp_path / "subjects.csv"
    timetable = tmp_path / "timetable.txt"
    target_day = dt.date.today() + dt.timedelta(days=14)
    token = DAY_TOKENS[target_day.weekday()]

    init_timetable_db(db_path, target_day=target_day)
    write_metadata(teachers, [("profuser", "Prof Full Name")])
    write_metadata(subjects, [("MAT1.p", "Mathematics 1")])

    run_script(
        [
            "scripts/import_timetable.py",
            str(db_path),
            "--teachers",
            str(teachers),
            "--subjects",
            str(subjects),
        ],
        input_text=f"profuser_3A_MAT1.p_{token}_8_10_406\n",
    )

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (2, "alice", target_day.isoformat(), 8, 10, "future overlap"),
        )
    conn.close()

    timetable.write_text(
        f"profuser_3A_MAT1.p_{token}_8_10_407\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [PYTHON, "scripts/update_timetable.py", str(timetable), str(db_path)],
        cwd=REPO_ROOT,
        input="y\n",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Update would create future conflicts:" in result.stdout
    assert "future overlap" in result.stdout
    assert "Update completed successfully." in result.stdout

    row = fetch_one(
        db_path,
        """
        SELECT r.code AS room_code
        FROM weekly_sessions ws
        JOIN rooms r ON ws.room_id = r.id
        WHERE ws.id = 1
        """,
    )
    assert row["room_code"] == "407"
