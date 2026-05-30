#!/usr/bin/env python3

import argparse
import re
import sys
import sqlite3

DAY_MAP = {
    "pon": 0,
    "uto": 1,
    "sre": 2,
    "cet": 3,
    "pet": 4,
    "sub": 5,
    "ned": 6
}

REVERSE_DAY_MAP = {v: k for k, v in DAY_MAP.items()}

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

def find_session_ids(cur, teacher_username, course_code, course_type, groups):
    """
    Pronalazi session_id na osnovu nastavnika, koda kursa, tipa i liste grupa.
    """
    # Prvo tražimo sesije koje odgovaraju nastavniku i kursu
    cur.execute("""
        SELECT cs.id 
        FROM course_sessions cs
        JOIN teachers t ON cs.teacher_id = t.id
        JOIN courses c ON cs.course_id = c.id
        WHERE t.username = ? AND c.code = ? AND cs.type = ?
    """, (teacher_username, course_code, course_type))
    
    potential_sessions = cur.fetchall()

    ids = []
    for (s_id,) in potential_sessions:
        # Proveravamo da li se grupe za ovu sesiju poklapaju sa ulaznim grupama
        cur.execute("""
            SELECT g.name FROM groups g
            JOIN session_groups sg ON g.id = sg.group_id
            WHERE sg.session_id = ?
        """, (s_id,))
        
        existing_groups = {row[0] for row in cur.fetchall()}
        if existing_groups == set(groups):
            ids.append(s_id)
            
    return ids

def get_room_id(cur, code):
    cur.execute("SELECT id FROM rooms WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Soba nije pronađena: {code}")
    return row[0]

def update_session(cur, parsed):
    (teacher_username, groups, course_code, course_type, 
     day_of_week, start_slot, end_slot, room_code) = parsed

    try:
        new_room_id = get_room_id(cur, room_code)
    except ValueError as e:
        print(f"{e}")
        return

    session_ids = find_session_ids(cur, teacher_username, course_code, course_type, groups)
    
    if not session_ids:
        print(f"Sesija nije pronađena za {course_code} ({'.'.join(groups)})")
        return

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
        
            # Provera da li se išta zapravo promenilo
            if (old_room == room_code and old_day == day_of_week and 
                old_start == start_slot and old_end == end_slot):
                return # Nema promena, ne radimo ništa

    session_id = session_ids[0]
    if len(session_ids) > 1:
        print(f"{teacher_username} {course_code} ({'.'.join(groups)}) - postoji više od jedne sesije - koju sesiju ažurirati?")
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
            print("Unesite ID sesije koju treba promeniti:")
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
    
        # Ispis promene
        print(f"Ažuriranje: {teacher_username} {course_code} ({'.'.join(groups)})")
        print(f" Staro: {old_room}, {REVERSE_DAY_MAP[old_day]} {old_start}-{old_end}")
        print(f" Novo:  {room_code}, {REVERSE_DAY_MAP[day_of_week]} {start_slot}-{end_slot}")
    
        # Izvrši update
        cur.execute("""
            UPDATE weekly_sessions
            SET room_id = ?, day_of_week = ?, start_slot = ?, end_slot = ?
            WHERE session_id = ?
        """, (new_room_id, day_of_week, start_slot, end_slot, session_id))
    else:
        # Ako termin uopšte nije postojao, ubaci ga
        print(f"Dodavanje novog termina: {course_code} u {room_code}")
        cur.execute("""
            INSERT INTO weekly_sessions (session_id, room_id, day_of_week, start_slot, end_slot)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, new_room_id, day_of_week, start_slot, end_slot))

def main():
    parser = argparse.ArgumentParser(
        description="Update timetable entries from underscore-separated lines."
    )
    parser.add_argument("input_file", help="Text file with timetable lines")
    parser.add_argument("database", help="SQLite database path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.database)
    
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            with conn:
                cur = conn.cursor()
                for line in f:
                    if line.strip():
                        parsed = parse_line(line)
                        update_session(cur, parsed)
                print("Update uspešno završen.")
    except Exception as e:
        print("GREŠKA:", e)
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    main()        
