"""Semester lookup helpers shared by reservation views."""

import datetime

from db import query_db


def fetch_semesters():
    """Return all semesters ordered from newest to oldest."""
    rows = query_db(
        "SELECT id, name, start_date, end_date FROM semesters ORDER BY start_date DESC, id DESC"
    )
    return [dict(row) for row in rows]


def current_semester_id():
    """Return the currently active semester, or the latest one if none is active."""
    today = datetime.date.today().isoformat()
    row = query_db(
        """
        SELECT id
        FROM semesters
        WHERE ? BETWEEN start_date AND end_date
        ORDER BY start_date DESC, id DESC
        LIMIT 1
        """,
        (today,),
        one=True,
    )
    if row:
        return row["id"]

    row = query_db(
        "SELECT id FROM semesters ORDER BY start_date DESC, id DESC LIMIT 1",
        one=True,
    )
    return row["id"] if row else None


def semester_by_id(semester_id):
    """Return one semester by id."""
    if semester_id is None:
        return None
    row = query_db(
        "SELECT id, name, start_date, end_date FROM semesters WHERE id = ?",
        (semester_id,),
        one=True,
    )
    return dict(row) if row else None


def select_semester(semester_id=None, semesters=None):
    """Select the currently active semester from a known semester list."""
    selected_semester_id = semester_id if semester_id is not None else current_semester_id()
    if selected_semester_id is None and semesters:
        selected_semester_id = semesters[0]["id"]

    selected_semester = semester_by_id(selected_semester_id)
    return selected_semester_id, selected_semester
