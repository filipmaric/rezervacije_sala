"""Mobile attendance summary endpoints."""

from flask import Blueprint, g, jsonify

from db import query_db
from mobile_auth import mobile_auth_get_user_by_id, require_mobile_session
from semester import current_semester_id, semester_by_id


bp = Blueprint("mobile_attendance", __name__)


def attendance_summary_rows(semester_id, radius_username):
    """Return one row per course with the student's attendance summary."""
    return query_db(
        """
        WITH lesson_instances AS (
            SELECT c.id AS course_id,
                   c.name AS course_name,
                   c.code AS course_code,
                   ws.id AS weekly_session_id,
                   d.date AS lesson_date
            FROM weekly_sessions ws
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN courses c ON c.id = cs.course_id
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
              AND wxc.id IS NULL
        ),
        attended_lessons AS (
            SELECT c.id AS course_id,
                   COUNT(DISTINCT ws.id || '|' || ar.event_date) AS attended_lessons
            FROM attendance_records ar
            JOIN weekly_sessions ws
                 ON ar.event_kind = 'weekly'
                AND ws.id = ar.event_id
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN courses c ON c.id = cs.course_id
            WHERE ar.username = ?
              AND cs.semester_id = ?
            GROUP BY c.id
        ),
        recorded_lessons AS (
            SELECT c.id AS course_id,
                   COUNT(DISTINCT li.weekly_session_id || '|' || li.lesson_date) AS total_lessons_with_recorded_attendance
            FROM lesson_instances li
            JOIN attendance_records ar
                 ON ar.event_kind = 'weekly'
                AND ar.event_id = li.weekly_session_id
                AND ar.event_date = li.lesson_date
            JOIN courses c ON c.id = li.course_id
            GROUP BY c.id
        )
        SELECT li.course_id AS course_id,
               li.course_name AS course_name,
               li.course_code AS course_code,
               COALESCE(al.attended_lessons, 0) AS attended_lessons,
               MAX(
                   COALESCE(al.attended_lessons, 0),
                   COALESCE(rl.total_lessons_with_recorded_attendance, 0)
               ) AS total_lessons_with_recorded_attendance
        FROM lesson_instances li
        LEFT JOIN attended_lessons al ON al.course_id = li.course_id
        LEFT JOIN recorded_lessons rl ON rl.course_id = li.course_id
        WHERE COALESCE(al.attended_lessons, 0) > 0
        GROUP BY li.course_id, li.course_name, li.course_code
        ORDER BY li.course_name, li.course_code, li.course_id
        """,
        (semester_id, radius_username, semester_id),
    )


@bp.route("/mobile/attendance/history", methods=["GET"])
@require_mobile_session
def attendance_history():
    """Return the current-semester attendance summary for the authenticated student."""
    session = g.mobile_auth_session
    user = mobile_auth_get_user_by_id(session["user_id"])
    semester = semester_by_id(current_semester_id())
    if semester is None:
        return jsonify({"current_semester": None, "summaries": []})

    return jsonify(
        {
            "current_semester": semester,
            "summaries": [dict(row) for row in attendance_summary_rows(semester["id"], user["radius_username"])],
        }
    )
