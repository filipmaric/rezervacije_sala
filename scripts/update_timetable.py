#!/usr/bin/env python3

import argparse
import datetime as dt
import sys
import sqlite3

from timetable_common import (
    REVERSE_DAY_MAP,
    collect_future_conflicts,
    find_session_ids,
    format_conflict,
    get_room_id,
    parse_line,
)

def update_session(cur, parsed):
    (teacher_username, groups, course_code, course_type, 
     day_of_week, start_slot, end_slot, room_code) = parsed

    try:
        new_room_id = get_room_id(cur, room_code)
    except ValueError as e:
        print(f"{e}")
        return []

    session_ids = find_session_ids(cur, teacher_username, course_code, course_type, groups)
    
    if not session_ids:
        print(f"No session found for {course_code} ({'.'.join(groups)})")
        return []

    for session_id in session_ids:
        cur.execute("""
            SELECT r.code, ws.day_of_week, ws.start_slot, ws.end_slot 
            FROM weekly_sessions ws
            JOIN rooms r ON ws.room_id = r.id
            WHERE ws.session_id = ?
        """, (session_id,))
        old_data = cur.fetchone()

        if old_data:
            old_room, old_day, old_start, old_end = old_data
        
            # Check whether anything actually changed
            if (old_room == room_code and old_day == day_of_week and 
                old_start == start_slot and old_end == end_slot):
                return []

    session_id = session_ids[0]
    if len(session_ids) > 1:
        print(f"{teacher_username} {course_code} ({'.'.join(groups)}) - more than one matching session exists - which session should be updated?")
        for session_id in session_ids:
            cur.execute("""
                SELECT r.code, ws.day_of_week, ws.start_slot, ws.end_slot 
                FROM weekly_sessions ws
                JOIN rooms r ON ws.room_id = r.id
                WHERE ws.session_id = ?
            """, (session_id,))
            old_data = cur.fetchone()

            if old_data:
                old_room, old_day, old_start, old_end = old_data
                print(f"ID: {session_id}, {old_room}, {REVERSE_DAY_MAP[old_day]} {old_start}-{old_end}")

        while True:
            print("Enter the ID of the session to change:")
            session_id = int(input())
            if session_id in session_ids:
                break

    cur.execute("""
        SELECT r.code, ws.day_of_week, ws.start_slot, ws.end_slot 
        FROM weekly_sessions ws
        JOIN rooms r ON ws.room_id = r.id
        WHERE ws.session_id = ?
    """, (session_id,))
    old_data = cur.fetchone()

    if old_data:
        old_room, old_day, old_start, old_end = old_data
    
        # Print the change
        print(f"Updating: {teacher_username} {course_code} ({'.'.join(groups)})")
        print(f" Old: {old_room}, {REVERSE_DAY_MAP[old_day]} {old_start}-{old_end}")
        print(f" New: {room_code}, {REVERSE_DAY_MAP[day_of_week]} {start_slot}-{end_slot}")
    
        # Apply the update
        cur.execute("""
            UPDATE weekly_sessions
            SET room_id = ?, day_of_week = ?, start_slot = ?, end_slot = ?
            WHERE session_id = ?
        """, (new_room_id, day_of_week, start_slot, end_slot, session_id))
    else:
        # If the slot did not exist at all, insert it
        print(f"Adding new slot: {course_code} in {room_code}")
        cur.execute("""
            INSERT INTO weekly_sessions (session_id, room_id, day_of_week, start_slot, end_slot)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, new_room_id, day_of_week, start_slot, end_slot))

    return [session_id]


def prompt_for_conflicts(conflicts):
    print("Update would create future conflicts:")
    for conflict in conflicts:
        print(format_conflict(conflict))

    while True:
        answer = input("Apply this update anyway? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("Please answer y or n.")

def main():
    parser = argparse.ArgumentParser(
        description="Update timetable entries from underscore-separated lines."
    )
    parser.add_argument("input_file", help="Text file with timetable lines")
    parser.add_argument("database", help="SQLite database path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row
    now = dt.datetime.now()
    
    try:
        cur = conn.cursor()
        with open(args.input_file, "r", encoding="utf-8") as f:
            for index, line in enumerate(f, start=1):
                if line.strip():
                    savepoint = f"line_{index}"
                    cur.execute(f"SAVEPOINT {savepoint}")
                    parsed = parse_line(line)
                    changed_session_ids = set(update_session(cur, parsed))

                    if not changed_session_ids:
                        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                        continue

                    conflicts = collect_future_conflicts(
                        conn,
                        now,
                        focus_weekly_session_ids=changed_session_ids,
                    )
                    if conflicts and not prompt_for_conflicts(conflicts):
                        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                        print("Update skipped.")
                        continue

                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.commit()
        print("Update completed successfully.")
        return 0
    except Exception as e:
        print("ERROR:", e)
        conn.rollback()
        return 1
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())        
