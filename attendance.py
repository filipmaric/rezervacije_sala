"""Attendance QR, challenge, and student check-in routes."""

import datetime
import hashlib
import hmac
import random
import secrets

from flask import Blueprint, abort, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from config import (
    ATTENDANCE_CHALLENGE_TTL,
    ATTENDANCE_JOIN_TOKEN_TTL,
    ATTENDANCE_PREVIOUS_CHALLENGE_ROUNDS,
    ATTENDANCE_SECRET,
    ATTENDANCE_SESSION_TTL,
)
from auth import RATE_LIMITS, check_if_admin, enforce_rate_limit, student_radius_auth
from db import execute_db, query_db


bp = Blueprint("attendance", __name__)


def attendance_kind_valid(kind):
    """Return True when the attendance kind is a supported event type."""
    return kind in {"weekly", "reservation"}


# Attendance challenge code helpers.
#
# The teacher page shows one 4-digit number that changes every few seconds.
# The student page shows the same number as clickable buttons. These helpers
# build that number, build the wrong options, and check the chosen answer.
#
# A "bucket" is one fixed-length time slice. For example, if the challenge
# lasts 10 seconds, then 12:00:00 to 12:00:09 is one bucket and
# 12:00:10 to 12:00:19 is the next bucket.


def attendance_code_seed(kind, event_id, event_date, bucket, salt="code"):
    """Build a repeatable hash seed for the current challenge round."""
    payload = f"{kind}:{event_id}:{event_date}:{bucket}:{salt}".encode("utf-8")
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big")


def attendance_code_for_bucket(kind, event_id, event_date, bucket):
    """Return the correct 4-digit challenge code for a given time bucket."""
    return 1000 + (attendance_code_seed(kind, event_id, event_date, bucket, "answer") % 9000)


def attendance_options_for_bucket(kind, event_id, event_date, bucket):
    """Return the multiple-choice answers for the current challenge bucket."""
    options = []
    seen = set()
    base = attendance_code_for_bucket(kind, event_id, event_date, bucket)

    seed = attendance_code_seed(kind, event_id, event_date, bucket, "options")
    rng = random.Random(seed)

    while len(options) < 4:
        if not options:
            candidate = base
        else:
            candidate = 1000 + rng.randrange(9000)
        if candidate in seen:
            continue
        seen.add(candidate)
        options.append(candidate)

    rng.shuffle(options)
    return options


def attendance_challenge_for_time(kind, event_id, event_date, now=None):
    """Build the current challenge payload for the teacher and student pages."""
    now = now or datetime.datetime.now()
    bucket = int(now.timestamp() // ATTENDANCE_CHALLENGE_TTL)
    options = attendance_options_for_bucket(kind, event_id, event_date, bucket)
    return {
        "bucket": bucket,
        "current_code": attendance_code_for_bucket(kind, event_id, event_date, bucket),
        "options": options,
        "expires_in": ATTENDANCE_CHALLENGE_TTL - (int(now.timestamp()) % ATTENDANCE_CHALLENGE_TTL),
    }


def attendance_code_is_valid(kind, event_id, event_date, code, now=None):
    """Accept the current challenge bucket plus a small number of previous buckets."""
    now = now or datetime.datetime.now()
    current_bucket = int(now.timestamp() // ATTENDANCE_CHALLENGE_TTL)
    for offset in range(ATTENDANCE_PREVIOUS_CHALLENGE_ROUNDS + 1):
        bucket = current_bucket - offset
        if code == attendance_code_for_bucket(kind, event_id, event_date, bucket):
            return True
    return False


# Attendance event lookup and access control helpers.
#
# These functions load the lecture/reservation that the attendance page refers
# to and verify whether the current logged-in user may view the teacher-side
# attendance list.


def attendance_event_row(kind, event_id, event_date):
    """Load the lecture or reservation associated with an attendance page."""
    if kind == "weekly":
        row = query_db(
            """
            SELECT ws.id AS event_id,
                   ws.room_id,
                   r.name AS room_name,
                   ws.start_slot,
                   ws.end_slot,
                   ws.day_of_week,
                   c.id AS course_id,
                   c.name AS course_name,
                   c.code AS course_code,
                   cs.type AS course_type,
                   t.name AS teacher_name,
                   t.username AS teacher_username,
                   s.name AS semester_name,
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
            WHERE ws.id = ?
              AND ? BETWEEN s.start_date AND s.end_date
            GROUP BY ws.id,
                     ws.room_id,
                     r.name,
                     ws.start_slot,
                     ws.end_slot,
                     ws.day_of_week,
                     c.id,
                     c.name,
                     c.code,
                     cs.type,
                     t.name,
                     t.username,
                     s.name,
                     wxc.id
            """,
            (event_date, event_id, event_date),
            one=True,
        )
        data = dict(row) if row else None
        if data:
            data["event_date"] = event_date
        return data

    if kind == "reservation":
        row = query_db(
            """
            SELECT r.id AS event_id,
                   r.room_id,
                   rm.name AS room_name,
                   r.start_slot,
                   r.end_slot,
                   r.date AS reservation_date,
                   r.description,
                   r.username AS owner_username
            FROM reservations r
            JOIN rooms rm ON rm.id = r.room_id
            WHERE r.id = ?
              AND r.date = ?
            """,
            (event_id, event_date),
            one=True,
        )
        data = dict(row) if row else None
        if data:
            data["event_date"] = event_date
        return data

    return None


def attendance_can_view(kind, row):
    """Check whether the current logged-in user may inspect the attendance list."""
    if not current_user.is_authenticated:
        return False
    if check_if_admin(current_user.username):
        return True
    if kind == "weekly":
        return current_user.username == row["teacher_username"]
    return current_user.username == row["owner_username"]


# Attendance record helpers.
#
# `attendance_records` stores the students that successfully checked in for a
# specific lecture occurrence or personal reservation date.


def attendance_records_for_event(kind, event_id, event_date):
    """Return the students who have already checked in for an event."""
    rows = query_db(
        """
        SELECT username, created_at
        FROM attendance_records
        WHERE event_kind = ?
          AND event_id = ?
          AND event_date = ?
        ORDER BY created_at, id
        """,
        (kind, event_id, event_date),
    )
    return [dict(row) for row in rows]


def attendance_record_student(kind, event_id, event_date, username):
    """Store one successful attendance check-in for a student."""
    execute_db(
        """
        INSERT OR IGNORE INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        (kind, event_id, event_date, username),
    )


# Attendance failure tracking and expiry cleanup.
#
# These helpers remember failed attempts for a student attendance session.
# After two wrong attempts, the session is blocked. Expired failure rows are
# cleaned when attendance endpoints are hit.


def attendance_session_failure_state(session_token):
    """Return or create the failure-tracking row for one attendance session."""
    if not session_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_session_failures
            (session_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (session_token,),
    )
    row = query_db(
        """
        SELECT session_token, failed_attempts, blocked, updated_at
        FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_session_failure_state_raw(session_token):
    """Return the failure-tracking row without creating a new one."""
    if not session_token:
        return None
    row = query_db(
        """
        SELECT session_token, failed_attempts, blocked, updated_at
        FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_session_record_failure(session_token):
    """Count one wrong challenge attempt for the current attendance session."""
    if not session_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_session_failures
            (session_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (session_token,),
    )
    execute_db(
        """
        UPDATE attendance_session_failures
        SET failed_attempts = failed_attempts + 1,
            blocked = CASE
                WHEN failed_attempts + 1 >= 2 THEN 1
                ELSE blocked
            END,
            updated_at = datetime('now')
        WHERE session_token = ?
        """,
        (session_token,),
    )
    return attendance_session_failure_state(session_token)


def attendance_session_clear_failures(session_token):
    """Remove failure tracking when the student checks in successfully."""
    if not session_token:
        return
    execute_db(
        """
        DELETE FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
    )


def attendance_session_token_expires_at(session_token):
    """Extract the expiry timestamp from a signed attendance session token."""
    if not session_token or "." not in session_token:
        return None
    payload, _signature = session_token.rsplit(".", 1)
    parts = payload.split(":")
    if len(parts) != 5:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def attendance_cleanup_expired_failures(now=None):
    """Delete stale failure rows for expired attendance sessions."""
    now = now or datetime.datetime.now()
    now_ts = int(now.timestamp())
    rows = query_db(
        """
        SELECT session_token
        FROM attendance_session_failures
        """,
    ) or []
    expired_tokens = []
    for row in rows:
        token = row["session_token"]
        expires_at = attendance_session_token_expires_at(token)
        if expires_at is not None and expires_at < now_ts:
            expired_tokens.append(token)
    if not expired_tokens:
        return 0
    for token in expired_tokens:
        execute_db(
            """
            DELETE FROM attendance_session_failures
            WHERE session_token = ?
            """,
            (token,),
        )
    return len(expired_tokens)


def attendance_session_is_blocked(session_token):
    """Check the failure row for a session, creating it if needed."""
    state = attendance_session_failure_state(session_token)
    return bool(state and state["blocked"])


def attendance_session_is_blocked_raw(session_token):
    """Check whether the session is blocked without creating a failure row."""
    state = attendance_session_failure_state_raw(session_token)
    return bool(state and state["blocked"])


# Student attendance session helpers.
#
# A student first scans the QR code, which creates a signed cookie-backed
# attendance session. The join page and submit endpoint both check that the
# session is present and still valid.


def attendance_session_status(kind, event_id, event_date):
    """Classify the current attendance session as valid, missing, blocked, or expired."""
    attendance_cleanup_expired_failures()
    session_token = attendance_session_from_request(kind, event_id, event_date)
    if not session_token:
        return "missing"
    if attendance_session_is_blocked_raw(session_token):
        return "blocked"
    if not attendance_validate_session_token(kind, event_id, event_date, session_token):
        return "expired"
    return "valid"


# QR token and attendance-session helpers.
#
# The QR token is short-lived and rotates every 8 seconds. Once a student
# opens the tokenized QR link, the server sets a longer-lived attendance
# session cookie (90 seconds by default) so the student has time to enter
# credentials and choose the correct challenge number.


def attendance_token_bucket(now=None):
    """Return the current QR-token time bucket."""
    now = now or datetime.datetime.now()
    # The QR token uses the same idea: one bucket = one 8-second time slice.
    return int(now.timestamp() // ATTENDANCE_JOIN_TOKEN_TTL)


def attendance_token_for_bucket(kind, event_id, event_date, bucket):
    """Derive the short-lived QR token for one event and one time bucket."""
    payload = f"{kind}:{event_id}:{event_date}:{bucket}:join".encode("utf-8")
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return digest[:48]


def attendance_join_token(kind, event_id, event_date, now=None):
    """Return the QR token for the current time bucket."""
    bucket = attendance_token_bucket(now)
    return attendance_token_for_bucket(kind, event_id, event_date, bucket)


def attendance_token_is_valid(kind, event_id, event_date, token, now=None):
    """Accept the current QR token bucket and the previous one for grace."""
    if not token:
        return False
    now = now or datetime.datetime.now()
    current_bucket = attendance_token_bucket(now)
    for bucket in (current_bucket, current_bucket - 1):
        if token == attendance_token_for_bucket(kind, event_id, event_date, bucket):
            return True
    return False


def attendance_session_cookie_name(kind, event_id, event_date):
    """Build the cookie name for one attendance session."""
    return f"attendance_session_{kind}_{event_id}_{event_date}"


def attendance_session_payload(kind, event_id, event_date, expires_at, nonce):
    """Serialize the signed attendance-session payload."""
    return f"{kind}:{event_id}:{event_date}:{expires_at}:{nonce}"


def attendance_session_sign(payload):
    """Sign an attendance-session payload with the shared secret."""
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def attendance_create_session_token(kind, event_id, event_date, now=None):
    """Create the signed cookie value that keeps a student on the join page."""
    now = now or datetime.datetime.now()
    expires_at = int(now.timestamp()) + ATTENDANCE_SESSION_TTL
    nonce = secrets.token_urlsafe(12)
    payload = attendance_session_payload(kind, event_id, event_date, expires_at, nonce)
    signature = attendance_session_sign(payload)
    return f"{payload}.{signature}"


def attendance_validate_session_token(kind, event_id, event_date, token, now=None):
    """Verify the signed attendance-session cookie and its expiry time."""
    if not token or "." not in token:
        return False
    now = now or datetime.datetime.now()
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(attendance_session_sign(payload), signature):
        return False
    parts = payload.split(":")
    if len(parts) != 5:
        return False
    token_kind, token_event_id, token_event_date, expires_at_str, _nonce = parts
    if token_kind != kind or token_event_date != event_date or token_event_id != str(event_id):
        return False
    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return False
    return int(now.timestamp()) <= expires_at


def attendance_session_from_request(kind, event_id, event_date):
    """Read the attendance-session cookie from the current request."""
    cookie_name = attendance_session_cookie_name(kind, event_id, event_date)
    return request.cookies.get(cookie_name)


def attendance_has_session(kind, event_id, event_date):
    """Return True when the request carries a valid attendance-session cookie."""
    token = attendance_session_from_request(kind, event_id, event_date)
    return attendance_validate_session_token(kind, event_id, event_date, token)


def attendance_make_session_response(response, kind, event_id, event_date):
    """Attach a fresh attendance-session cookie to a redirect response."""
    token = attendance_create_session_token(kind, event_id, event_date)
    cookie_name = attendance_session_cookie_name(kind, event_id, event_date)
    response.set_cookie(
        cookie_name,
        token,
        max_age=ATTENDANCE_SESSION_TTL,
        httponly=True,
        samesite="Lax",
    )
    return response


# Route handlers.
#
# These endpoints serve the teacher attendance page, the student join flow,
# the live challenge payload, the teacher current attendance list, and the
# final student check-in submission.


@bp.route('/attendance/<kind>/<int:event_id>/<event_date>')
def attendance_view(kind, event_id, event_date):
    """Render the teacher attendance page."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    return render_template(
        'attendance_teacher.html',
        attendance_kind=kind,
        attendance_event_id=event_id,
        attendance_event_date=event_date,
    )



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/join')
def attendance_join_view(kind, event_id, event_date):
    """Render the student attendance page after a QR scan creates a session."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    if not attendance_has_session(kind, event_id, event_date):
        abort(403)
    return render_template(
        'attendance_join.html',
        attendance_kind=kind,
        attendance_event_id=event_id,
        attendance_event_date=event_date,
    )



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/join/<token>')
def attendance_join_view_token(kind, event_id, event_date, token):
    """Validate a short-lived QR token and create the student attendance session."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    if not attendance_token_is_valid(kind, event_id, event_date, token):
        abort(404)
    response = make_response(
        redirect(url_for(
            'attendance.attendance_join_view',
            kind=kind,
            event_id=event_id,
            event_date=event_date,
        ))
    )
    return attendance_make_session_response(response, kind, event_id, event_date)



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/challenge')
def attendance_challenge_data(kind, event_id, event_date):
    """Return the current challenge code and event metadata."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    session_status = attendance_session_status(kind, event_id, event_date)
    if session_status == "blocked":
        return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
    if session_status != "valid":
        return jsonify({'error': 'Сесија је истекла. Поново скенирајте QR код.'}), 403

    challenge = attendance_challenge_for_time(kind, event_id, event_date)
    return jsonify({
        'event': row,
        'challenge': challenge,
    })



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/data')
def attendance_roster_data(kind, event_id, event_date):
    """Return teacher attendance data, including the current attendance list and QR token."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403

    challenge = attendance_challenge_for_time(kind, event_id, event_date)
    return jsonify({
        'event': row,
        'challenge': challenge,
        'join_token': attendance_join_token(kind, event_id, event_date),
        'students': attendance_records_for_event(kind, event_id, event_date),
        'can_view': True,
    })



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/join', methods=['POST'])
def attendance_join_submit(kind, event_id, event_date):
    """Accept a student attendance check-in after validating credentials and the challenge code."""
    limited = enforce_rate_limit("attendance", *RATE_LIMITS["attendance"])
    if limited is not None:
        return limited

    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    session_token = attendance_session_from_request(kind, event_id, event_date)
    session_status = attendance_session_status(kind, event_id, event_date)
    if session_status == "blocked":
        return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
    if session_status != "valid":
        return jsonify({'error': 'Сесија је истекла. Поново скенирајте QR код.'}), 403

    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    selected_code = data.get('selected_code')

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    try:
        selected_code = int(selected_code)
    except (TypeError, ValueError):
        return jsonify({'error': 'selected code is required'}), 400

    if not attendance_code_is_valid(kind, event_id, event_date, selected_code):
        state = attendance_session_record_failure(session_token)
        if state and state["blocked"]:
            return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
        return jsonify({'error': 'Погрешан број. Сачекајте нови круг.'}), 409

    if not student_radius_auth(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    attendance_session_clear_failures(session_token)
    attendance_record_student(kind, event_id, event_date, username)
    return jsonify({'success': True, 'username': username})
