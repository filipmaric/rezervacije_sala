"""Rooms, working-day logic, and occupancy read endpoints."""

import datetime

from flask import Blueprint, abort, jsonify, request

from attendance import attendance_is_open_now
from db import query_db


bp = Blueprint("occupancy", __name__)


# Room list and occupancy helpers.


@bp.route("/rooms")
def list_rooms():
    """Return the rooms list, optionally filtered by room type."""
    query = "SELECT id, name, capacity, type, location, priority FROM rooms"
    params = ()

    room_type = request.args.get("type")
    if room_type:
        query += " WHERE type = ?"
        params = (room_type,)

    rows = query_db(query, params)
    rooms_list = [
        {
            "id": r["id"],
            "name": r["name"],
            "capacity": r["capacity"],
            "type": r["type"],
            "location": r["location"],
            "priority": r["priority"],
        }
        for r in rows
    ]

    return jsonify(rooms_list)


def iso_to_weekday(date_str):
    """Convert an ISO date string to a Monday-based weekday number."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    return dt.weekday()


def check_day(date):
    """Look up the calendar row for a date and derive its working-day status."""
    ad = query_db("SELECT is_working, week_day FROM days WHERE date = ?", (date,), one=True)
    if ad:
        is_working = ad["is_working"] == 1
        week_day = ad["week_day"]
    else:
        is_working = False
        week_day = -1

    if week_day == -1:
        dow = iso_to_weekday(date)
    else:
        dow = week_day

    return (is_working, week_day, dow)



# Read endpoints.


@bp.route("/occupancy")
def occupancy():
    """Return the merged room occupancy for a single date."""
    date = request.args.get("date")
    if not date:
        abort(400, "date param required YYYY-MM-DD")
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        abort(400, "invalid date format, expected YYYY-MM-DD")

    is_working, week_day, dow = check_day(date)
    result = {}

    if is_working:
        q = """
        SELECT ws.id AS ws_id,
               ws.room_id,
               r.name AS room,
               ws.start_slot,
               ws.end_slot,
               c.id AS course_id,
               c.name AS course_name,
               cs.type AS course_type,
               t.name AS teacher,
               t.username AS teacher_username,
               GROUP_CONCAT(g.name, ',') AS groups,
               CASE WHEN wxc.id IS NULL THEN 0 ELSE 1 END AS is_canceled
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN rooms r ON r.id = ws.room_id
        LEFT JOIN session_groups sg ON sg.session_id = cs.id
        LEFT JOIN groups g ON g.id = sg.group_id
        LEFT JOIN weekly_cancellations wxc
               ON wxc.weekly_session_id = ws.id
              AND wxc.date = ?
        WHERE ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        GROUP BY ws.id,
          ws.room_id,
          r.name,
          ws.start_slot,
          ws.end_slot,
          c.id,
          c.name,
          cs.type,
          t.name,
          t.username;
         """
        for r in query_db(q, (date, dow, date)):
            if bool(r["is_canceled"]):
                overlap = query_db(
                    """
                    SELECT 1
                    FROM reservations
                    WHERE room_id = ?
                      AND date = ?
                      AND (start_slot < ? AND ? < end_slot)
                    """,
                    (r["room_id"], date, r["end_slot"], r["start_slot"]),
                    one=True,
                )
                if overlap:
                    continue

            room_id = str(r["room_id"])
            result.setdefault(room_id, []).append(
                {
                    "type": "weekly",
                    "weekly_session_id": r["ws_id"],
                    "start": r["start_slot"],
                    "end": r["end_slot"],
                    "lecture_id": r["course_id"],
                    "lecture_name": r["course_name"],
                    "lecture_type": r["course_type"],
                    "teacher": r["teacher"],
                    "teacher_username": r["teacher_username"],
                    "room": r["room"],
                    "groups": r["groups"].split(",") if r["groups"] else [],
                    "canceled": bool(r["is_canceled"]),
                    "attendance_open": attendance_is_open_now(
                        {
                            "event_date": date,
                            "start_slot": r["start_slot"],
                            "end_slot": r["end_slot"],
                        }
                    ) and not bool(r["is_canceled"]),
                }
            )

    q2 = """
    SELECT reservations.id, room_id, r.name as room, start_slot, end_slot, description, username
    FROM reservations JOIN rooms r ON reservations.room_id = r.id
    WHERE date = ?
    """
    for r in query_db(q2, (date,)):
        room_id = str(r["room_id"])
        result.setdefault(room_id, []).append(
            {
                "type": "reservation",
                "id": r["id"],
                "start": r["start_slot"],
                "end": r["end_slot"],
                "description": r["description"],
                "username": r["username"],
                "room": r["room"],
                "attendance_open": attendance_is_open_now(
                    {
                        "reservation_date": date,
                        "start_slot": r["start_slot"],
                        "end_slot": r["end_slot"],
                    }
                ),
            }
        )

    for k in result:
        result[k].sort(key=lambda x: x["start"])

    return jsonify({"date": date, "is_working": is_working, "week_day": dow, "rooms": result})
