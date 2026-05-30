#!/usr/bin/env python3

import argparse
import sqlite3
import sys

from timetable_common import (
    clear_schedule,
    get_latest_semester_id,
    get_or_create_course,
    get_or_create_group,
    get_or_create_teacher,
    get_room_id,
    load_metadata,
    parse_line,
)

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
        with conn:  # transaction
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
