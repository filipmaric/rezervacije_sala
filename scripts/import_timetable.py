#!/usr/bin/env python3

import argparse
import csv
import re
import sqlite3
import sys

DAY_MAP = {
    "pon": 0,
    "uto": 1,
    "sre": 2,
    "cet": 3,
    "pet": 4,
    "sub": 5,
    "ned": 6
}


# -----------------------------
# Metadata loading
# -----------------------------

def load_metadata(filename):
    mapping = {}

    with open(filename, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) >= 2:
                mapping[row[0].strip()] = row[1].strip()

    return mapping


def clear_schedule(cur):
    """
    Briše sve podatke vezane za raspored.
    """
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("DELETE FROM weekly_sessions;")
    cur.execute("DELETE FROM session_groups;")
    cur.execute("DELETE FROM course_sessions;")
    
# -----------------------------
# Parsing timetable line
# -----------------------------

def parse_line(line):
    parts = line.strip().split("_")

    if len(parts) != 7:
        raise ValueError(f"Invalid format: {line}")

    teacher = parts[0]
    groups = [g.strip() for g in re.split(r"[.,;|]+", parts[1]) if g.strip()]
    course_code = parts[2]
    day_str = parts[3]
    start_slot = int(parts[4])
    end_slot = int(parts[5])
    room_code = parts[6]

    course_parts = course_code.split(".")
    if len(course_parts) < 2:
        raise ValueError(f"Invalid course code: {course_code}")
    course_name = course_parts[0]
    course_type = course_parts[1]

    if day_str not in DAY_MAP:
        raise ValueError(f"Invalid day: {day_str}")

    return (
        teacher,
        groups,
        course_name,
        course_type,
        DAY_MAP[day_str],
        start_slot,
        end_slot,
        room_code
    )


# -----------------------------
# Database helpers
# -----------------------------

def get_or_create_teacher(cur, username, full_name):
    cur.execute("""
        INSERT OR IGNORE INTO teachers(username, name)
        VALUES (?, ?)
    """, (username, full_name))

    cur.execute("SELECT id FROM teachers WHERE username = ?", (username,))
    return cur.fetchone()[0]


def get_or_create_course(cur, code, name):
    cur.execute("""
        INSERT OR IGNORE INTO courses(name, code)
        VALUES (?, ?)
    """, (name, code))

    cur.execute("SELECT id FROM courses WHERE code = ?", (code,))
    return cur.fetchone()[0]


def get_or_create_group(cur, name):
    cur.execute("""
        INSERT OR IGNORE INTO groups(name)
        VALUES (?)
    """, (name,))

    cur.execute("SELECT id FROM groups WHERE name = ?", (name,))
    return cur.fetchone()[0]


def get_latest_semester_id(cur):
    cur.execute("""
        SELECT id FROM semesters
        ORDER BY start_date DESC
        LIMIT 1
    """)
    return cur.fetchone()[0]


def get_room_id(cur, code):
    cur.execute("SELECT id FROM rooms WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Room not found: {code}")
    return row[0]


# -----------------------------
# Main insertion logic
# -----------------------------

def insert_session(cur, parsed, teacher_metadata, subject_metadata):

    (teacher_username,
     groups,
     course_code,
     course_type,
     day_of_week,
     start_slot,
     end_slot,
     room_code) = parsed

    teacher_name = teacher_metadata.get(teacher_username, teacher_username)
    course_name = subject_metadata.get(course_code, course_code)

    teacher_id = get_or_create_teacher(cur, teacher_username, teacher_name)
    course_id = get_or_create_course(cur, course_code, course_name)
    semester_id = get_latest_semester_id(cur)
    room_id = get_room_id(cur, room_code)

    # -----------------------------
    # Create course_session
    # -----------------------------
    cur.execute("""
        INSERT INTO course_sessions
        (course_id, teacher_id, semester_id, type)
        VALUES (?, ?, ?, ?)
    """, (course_id, teacher_id, semester_id, course_type))

    session_id = cur.lastrowid   # ← OVDE PAMTIMO ID

    # -----------------------------
    # Attach groups
    # -----------------------------
    for g in groups:
        group_id = get_or_create_group(cur, g)

        cur.execute("""
            INSERT INTO session_groups(session_id, group_id)
            VALUES (?, ?)
        """, (session_id, group_id))

    # -----------------------------
    # Insert weekly session
    # -----------------------------
    cur.execute("""
        INSERT INTO weekly_sessions
        (session_id, room_id, day_of_week, start_slot, end_slot)
        VALUES (?, ?, ?, ?, ?)
    """, (session_id, room_id, day_of_week, start_slot, end_slot))


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import a timetable from underscore-separated lines."
    )
    parser.add_argument("database", help="SQLite database path")
    parser.add_argument(
        "--teachers",
        default="teachers.csv",
        help="Tab-separated metadata file mapping teacher usernames to names",
    )
    parser.add_argument(
        "--subjects",
        default="subjects.csv",
        help="Tab-separated metadata file mapping course codes to course names",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not clear existing timetable data before import",
    )
    args = parser.parse_args()

    teacher_metadata = load_metadata(args.teachers)
    subject_metadata = load_metadata(args.subjects)

    conn = sqlite3.connect(args.database)
    try:
        with conn:  # transakcija
            cur = conn.cursor()
            if not args.keep_existing:
                clear_schedule(cur)

            for line in sys.stdin:
                if line.strip():
                    parsed = parse_line(line)
                    insert_session(cur, parsed, teacher_metadata, subject_metadata)

    except Exception as e:
        print("ERROR:", e)
        conn.rollback()
        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
