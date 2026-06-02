"""Semester and personal-reservations endpoints."""

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from auth import check_if_admin
from db import query_db
from semester import current_semester_id, fetch_semesters, select_semester


bp = Blueprint("reservations_views", __name__)


# Reservations page-data helpers.


def personal_reservations_for_semester(username, semester):
    """Return the logged-in user's personal reservations for one semester."""
    if not semester:
        return []

    rows = query_db(
        """
        SELECT r.id,
               r.room_id,
               rm.name AS room_name,
               r.date,
               r.start_slot,
               r.end_slot,
               r.description,
               r.username,
               r.created_at
        FROM reservations r
        JOIN rooms rm ON rm.id = r.room_id
        WHERE r.username = ?
          AND r.date BETWEEN ? AND ?
        ORDER BY r.date, r.start_slot, r.room_id, r.id
        """,
        (username, semester["start_date"], semester["end_date"]),
    )
    return [dict(row) for row in rows]


def course_sessions_for_semester(semester_id, teacher_username=None):
    """Return the course sessions that belong to a semester, optionally filtered by teacher."""
    params = [semester_id]
    teacher_clause = ""
    if teacher_username:
        teacher_clause = "AND t.username = ?"
        params.append(teacher_username)

    rows = query_db(
        """
        SELECT c.id AS course_id,
               c.name AS course_name,
               c.code AS course_code,
               cs.id AS course_session_id,
               cs.type AS course_type,
               t.name AS teacher_name,
               t.username AS teacher_username,
               ws.id AS weekly_session_id,
               ws.room_id,
               rm.name AS room_name,
               ws.day_of_week,
               ws.start_slot,
               ws.end_slot,
               GROUP_CONCAT(g.name, ',') AS groups
        FROM course_sessions cs
        JOIN courses c ON c.id = cs.course_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN weekly_sessions ws ON ws.session_id = cs.id
        JOIN rooms rm ON rm.id = ws.room_id
        LEFT JOIN session_groups sg ON sg.session_id = cs.id
        LEFT JOIN groups g ON g.id = sg.group_id
        WHERE cs.semester_id = ?
        {teacher_clause}
        GROUP BY c.id,
                 c.name,
                 c.code,
                 cs.id,
                 cs.type,
                 t.name,
                 t.username,
                 ws.id,
                 ws.room_id,
                 rm.name,
                 ws.day_of_week,
                 ws.start_slot,
                 ws.end_slot
        ORDER BY c.name, c.code, cs.id, ws.day_of_week, ws.start_slot, ws.room_id
        """.format(teacher_clause=teacher_clause),
        tuple(params),
    )

    courses = {}
    for row in rows:
        course_id = row["course_id"]
        course = courses.setdefault(
            course_id,
            {
                "course_id": course_id,
                "course_name": row["course_name"],
                "course_code": row["course_code"],
                "sessions": [],
            },
        )
        course["sessions"].append(
            {
                "course_session_id": row["course_session_id"],
                "weekly_session_id": row["weekly_session_id"],
                "course_type": row["course_type"],
                "teacher_name": row["teacher_name"],
                "teacher_username": row["teacher_username"],
                "room_id": row["room_id"],
                "room_name": row["room_name"],
                "day_of_week": row["day_of_week"],
                "start_slot": row["start_slot"],
                "end_slot": row["end_slot"],
                "groups": row["groups"].split(",") if row["groups"] else [],
            }
        )

    return list(courses.values())


def weekly_session_instances_for_semester(semester_id, teacher_username=None):
    """Return the actual held dates for each weekly session in a semester."""
    params = [semester_id]
    teacher_clause = ""
    if teacher_username:
        teacher_clause = "AND t.username = ?"
        params.append(teacher_username)

    rows = query_db(
        """
        SELECT ws.id AS weekly_session_id,
               d.date AS date
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN days d
          ON d.date BETWEEN s.start_date AND s.end_date
         AND d.is_working = 1
         AND ws.day_of_week = CASE
                WHEN d.week_day = -1 THEN ((CAST(strftime('%w', d.date) AS INTEGER) + 6) % 7)
                ELSE d.week_day
             END
        LEFT JOIN weekly_cancellations wxc
               ON wxc.weekly_session_id = ws.id
              AND wxc.date = d.date
        WHERE cs.semester_id = ?
          {teacher_clause}
          AND wxc.id IS NULL
        ORDER BY ws.id, d.date
        """.format(teacher_clause=teacher_clause),
        tuple(params),
    )

    instances = {}
    for row in rows:
        instances.setdefault(row["weekly_session_id"], []).append(row["date"])

    return instances


def attach_weekly_session_instances(courses, semester_id, teacher_username=None):
    """Attach the held dates to each weekly session in the course list."""
    if not semester_id:
        return courses

    instances_by_session = weekly_session_instances_for_semester(
        semester_id,
        teacher_username=teacher_username,
    )
    for course in courses:
        for session in course["sessions"]:
            session["instances"] = instances_by_session.get(session["weekly_session_id"], [])
    return courses


# Routes.


@bp.route("/my_reservations")
def my_reservations_view():
    """Render the My Reservations page."""
    return render_template("my_reservations.html")


@bp.route("/my_reservations_data")
@login_required
def my_reservations_json():
    """Return the semester-scoped JSON used by the My Reservations page."""
    semester_id = request.args.get("semester_id", type=int)
    semesters = fetch_semesters()
    selected_semester_id, selected_semester = select_semester(semester_id, semesters)
    if semester_id is not None and selected_semester is None:
        abort(404, "semester not found")

    personal_reservations = personal_reservations_for_semester(current_user.username, selected_semester)
    course_sessions = []
    if selected_semester_id:
        course_sessions = course_sessions_for_semester(selected_semester_id, teacher_username=current_user.username)
        attach_weekly_session_instances(
            course_sessions,
            selected_semester_id,
            teacher_username=current_user.username,
        )

    return jsonify(
        {
            "semesters": semesters,
            "current_semester_id": current_semester_id(),
            "selected_semester": selected_semester,
            "personal_reservations": personal_reservations,
            "courses": course_sessions,
        }
    )
