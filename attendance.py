"""Attendance QR, challenge, and student check-in routes."""

import datetime
import hashlib
import hmac
import math
import random
import secrets

from flask import Blueprint, abort, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from config import (
    ATTENDANCE_CHALLENGE_TTL,
    ATTENDANCE_CLASS_GRACE_MINUTES,
    ATTENDANCE_JOIN_TOKEN_TTL,
    ATTENDANCE_PREVIOUS_CHALLENGE_ROUNDS,
    ATTENDANCE_SECRET,
    ATTENDANCE_ATTEMPT_TTL,
)
from auth import RATE_LIMITS, check_if_admin, enforce_rate_limit, student_radius_auth
from db import (
    execute_db,
    building_locations_for_room_building_name,
    building_locations_all,
    mobile_auth_get_session_by_token,
    mobile_auth_get_user_by_id,
    query_db,
)


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
                   r.building_name AS room_building_name,
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
                     r.building_name,
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
                   rm.building_name AS room_building_name,
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


def attendance_event_window(row):
    """Return the allowed attendance window for one lecture or reservation."""
    event_date = row.get("event_date") or row.get("reservation_date")
    if not event_date:
        return None

    start_slot = row.get("start_slot")
    end_slot = row.get("end_slot")
    if start_slot is None or end_slot is None:
        return None

    try:
        day = datetime.datetime.strptime(event_date, "%Y-%m-%d").date()
    except ValueError:
        return None

    start_dt = datetime.datetime.combine(day, datetime.time(hour=int(start_slot)))
    end_dt = datetime.datetime.combine(day, datetime.time(hour=int(end_slot)))
    grace = datetime.timedelta(minutes=ATTENDANCE_CLASS_GRACE_MINUTES)
    return (start_dt - grace, end_dt + grace)


def attendance_is_open_now(row, now=None):
    """Return True when the current time is inside the allowed attendance window."""
    window = attendance_event_window(row)
    if not window:
        return False
    now = now or datetime.datetime.now()
    window_start, window_end = window
    return window_start <= now <= window_end


def attendance_not_open_message():
    """Return the message shown when attendance is accessed outside class time."""
    return "Пријава присуства је могућа само током часа."


def attendance_attempt_blocked_message():
    """Return the message shown when too many wrong attempts block the attempt."""
    return "Превише погрешних покушаја. Морате поново да скенирате QR код."


# Attendance record helpers.
#
# `attendance_records` stores the students that successfully checked in for a
# specific lecture occurrence or personal reservation date.


def attendance_records_for_event(kind, event_id, event_date):
    """Return the students who have already checked in for an event."""
    rows = query_db(
        """
        SELECT ar.id,
               ar.username,
               ar.created_at,
               ar.registration_source,
               ar.client_ip,
               ar.client_latitude,
               ar.client_longitude,
               ar.geofence_checked,
               ar.failed_attempts_before_success,
               s.student_index,
               s.surname,
               s.given_name
        FROM attendance_records ar
        LEFT JOIN students s ON s.username = ar.username
        WHERE event_kind = ?
          AND ar.event_id = ?
          AND ar.event_date = ?
        ORDER BY ar.created_at, ar.id
        """,
        (kind, event_id, event_date),
    )
    students = []
    for row in rows:
        given_name = (row["given_name"] or "").strip()
        surname = (row["surname"] or "").strip()
        full_name = " ".join(part for part in (given_name, surname) if part).strip()
        student_index = (row["student_index"] or "").strip()
        student_label = "Непознато"
        if full_name and student_index:
            student_label = f"{full_name} ({student_index})"
        elif full_name:
            student_label = full_name
        elif student_index:
            student_label = student_index

        students.append(
            {
                "attendance_record_id": row["id"],
                "username": row["username"],
                "created_at": row["created_at"],
                "registration_source": row["registration_source"],
                "client_ip": row["client_ip"],
                "client_latitude": row["client_latitude"],
                "client_longitude": row["client_longitude"],
                "geofence_checked": bool(row["geofence_checked"]),
                "failed_attempts_before_success": int(row["failed_attempts_before_success"] or 0),
                "student_index": student_index or None,
                "student_name": full_name or None,
                "student_label": student_label,
            }
        )
    return students


def attendance_record_student(
    kind,
    event_id,
    event_date,
    username,
    registration_source="web",
    client_ip=None,
    client_latitude=None,
    client_longitude=None,
    geofence_checked=False,
    failed_attempts_before_success=0,
):
    """Store one successful attendance check-in for a student."""
    execute_db(
        """
        INSERT OR IGNORE INTO attendance_records
            (
                event_kind,
                event_id,
                event_date,
                username,
                registration_source,
                client_ip,
                client_latitude,
                client_longitude,
                geofence_checked,
                failed_attempts_before_success
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            event_id,
            event_date,
            username,
            registration_source,
            client_ip,
            client_latitude,
            client_longitude,
            int(bool(geofence_checked)),
            int(failed_attempts_before_success or 0),
        ),
    )


def attendance_spot_check_score(student, rank, total):
    """Compute a temporary suspicion score for one attendance record."""
    score = 0
    if str(student.get("registration_source", "")).lower() == "web":
        score += 3

    failed_attempts = int(student.get("failed_attempts_before_success") or 0)
    if failed_attempts >= 2:
        score += 4
    elif failed_attempts == 1:
        score += 2

    if total > 0:
        percentile = rank / total
        if percentile > 0.8:
            score += 2
        elif percentile > 0.6:
            score += 1

    return score


def attendance_spot_check_candidates_for_event(kind, event_id, event_date, limit=5):
    """Return the most suspicious students for a teacher spot check."""
    if limit <= 0:
        return []

    students = attendance_records_for_event(kind, event_id, event_date)
    total = len(students)
    scored = []
    for index, student in enumerate(students, start=1):
        scored.append(
            (
                attendance_spot_check_score(student, index, total),
                index,
                student,
            )
        )

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]["username"]))
    return [item[2] for item in scored[:limit]]


def attendance_spot_check_record_misses(kind, event_id, event_date, selected_usernames, confirmed_usernames, teacher_username):
    """Store the students from a spot-check shortlist that the teacher did not confirm."""
    selected = {username for username in selected_usernames if username}
    confirmed = {username for username in confirmed_usernames if username}
    missed = selected - confirmed
    if not missed:
        return 0

    updated = 0
    for username in sorted(missed):
        row = query_db(
            """
            SELECT id
            FROM attendance_records
            WHERE event_kind = ?
              AND event_id = ?
              AND event_date = ?
              AND username = ?
            """,
            (kind, event_id, event_date, username),
            one=True,
        )
        if not row:
            continue
        execute_db(
            """
            INSERT INTO attendance_spot_check_flags
                (attendance_record_id, teacher_username, flagged_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(attendance_record_id) DO UPDATE SET
                teacher_username = excluded.teacher_username,
                flagged_at = excluded.flagged_at
            """,
            (row["id"], teacher_username),
        )
        updated += 1

    return updated


# Attendance attempt failure tracking and expiry cleanup.
#
# These helpers remember failed attempts for a student attendance attempt.
# After two wrong attempts, the attempt is blocked. Expired failure rows are
# cleaned when attendance endpoints are hit.


def attendance_attempt_failure_state(attempt_token):
    """Return or create the failure-tracking row for one attendance attempt."""
    if not attempt_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_attempt_failures
            (attempt_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (attempt_token,),
    )
    row = query_db(
        """
        SELECT attempt_token, failed_attempts, blocked, updated_at
        FROM attendance_attempt_failures
        WHERE attempt_token = ?
        """,
        (attempt_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_attempt_failure_state_raw(attempt_token):
    """Return the failure-tracking row without creating a new one."""
    if not attempt_token:
        return None
    row = query_db(
        """
        SELECT attempt_token, failed_attempts, blocked, updated_at
        FROM attendance_attempt_failures
        WHERE attempt_token = ?
        """,
        (attempt_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_attempt_record_failure(attempt_token):
    """Count one wrong challenge attempt for the current attendance attempt."""
    if not attempt_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_attempt_failures
            (attempt_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (attempt_token,),
    )
    execute_db(
        """
        UPDATE attendance_attempt_failures
        SET failed_attempts = failed_attempts + 1,
            blocked = CASE
                WHEN failed_attempts + 1 >= 2 THEN 1
                ELSE blocked
            END,
            updated_at = datetime('now')
        WHERE attempt_token = ?
        """,
        (attempt_token,),
    )
    return attendance_attempt_failure_state(attempt_token)


def attendance_attempt_block(attempt_token):
    """Force the attendance attempt into the blocked state."""
    if not attempt_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_attempt_failures
            (attempt_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (attempt_token,),
    )
    execute_db(
        """
        UPDATE attendance_attempt_failures
        SET failed_attempts = CASE
                WHEN failed_attempts < 2 THEN 2
                ELSE failed_attempts
            END,
            blocked = 1,
            updated_at = datetime('now')
        WHERE attempt_token = ?
        """,
        (attempt_token,),
    )
    return attendance_attempt_failure_state(attempt_token)


def attendance_attempt_clear_failures(attempt_token):
    """Remove failure tracking when the student checks in successfully."""
    if not attempt_token:
        return
    execute_db(
        """
        DELETE FROM attendance_attempt_failures
        WHERE attempt_token = ?
        """,
        (attempt_token,),
    )


def attendance_attempt_token_expires_at(attempt_token):
    """Extract the expiry timestamp from a signed attendance attempt token."""
    if not attempt_token or "." not in attempt_token:
        return None
    payload, _signature = attempt_token.rsplit(".", 1)
    parts = payload.split(":")
    if len(parts) != 5:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def attendance_cleanup_expired_attempt_failures(now=None):
    """Delete stale failure rows for expired attendance attempts."""
    now = now or datetime.datetime.now()
    now_ts = int(now.timestamp())
    rows = query_db(
        """
        SELECT attempt_token
        FROM attendance_attempt_failures
        """,
    ) or []
    expired_tokens = []
    for row in rows:
        token = row["attempt_token"]
        expires_at = attendance_attempt_token_expires_at(token)
        if expires_at is not None and expires_at < now_ts:
            expired_tokens.append(token)
    if not expired_tokens:
        return 0
    for token in expired_tokens:
        execute_db(
            """
            DELETE FROM attendance_attempt_failures
            WHERE attempt_token = ?
            """,
            (token,),
        )
    return len(expired_tokens)


def attendance_attempt_is_blocked(attempt_token):
    """Check the failure row for an attendance attempt, creating it if needed."""
    state = attendance_attempt_failure_state(attempt_token)
    return bool(state and state["blocked"])


def attendance_attempt_is_blocked_raw(attempt_token):
    """Check whether the attempt is blocked without creating a failure row."""
    state = attendance_attempt_failure_state_raw(attempt_token)
    return bool(state and state["blocked"])


# Student attendance attempt helpers.
#
# A student first scans the QR code, which creates a signed cookie-backed
# attendance attempt. The join page and submit endpoint both check that the
# attempt is present and still valid.


def attendance_attempt_status(kind, event_id, event_date):
    """Classify the current attendance attempt as valid, missing, blocked, or expired."""
    attendance_cleanup_expired_attempt_failures()
    attempt_token = attendance_attempt_from_request(kind, event_id, event_date)
    if not attempt_token:
        return "missing"
    return attendance_attempt_status_for_token(kind, event_id, event_date, attempt_token)


def attendance_attempt_status_for_token(kind, event_id, event_date, attempt_token):
    """Classify a supplied attendance attempt token as valid, blocked, or expired."""
    if attendance_attempt_is_blocked_raw(attempt_token):
        return "blocked"
    if not attendance_validate_attempt_token(kind, event_id, event_date, attempt_token):
        return "expired"
    return "valid"


def attendance_mobile_session_from_request():
    """Return the Android auth session and user for a bearer-authenticated request."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None

    token = auth.split(" ", 1)[1].strip()
    session = mobile_auth_get_session_by_token(token)
    if not session or session["revoked_at"] is not None:
        return None

    try:
        expires_at = datetime.datetime.fromisoformat(session["expires_at"])
    except ValueError:
        return None

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at <= datetime.datetime.now(datetime.timezone.utc):
        return None

    user = mobile_auth_get_user_by_id(session["user_id"])
    if not user:
        return None

    return {"session": session, "user": user}


def attendance_client_ip():
    """Return the best-effort client IP address for attendance auditing."""
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    return client_ip.split(",")[0].strip() or "unknown"


def attendance_distance_meters(latitude1, longitude1, latitude2, longitude2):
    """Return the distance between two coordinates in meters."""
    radius_m = 6371000.0
    lat1 = math.radians(float(latitude1))
    lon1 = math.radians(float(longitude1))
    lat2 = math.radians(float(latitude2))
    lon2 = math.radians(float(longitude2))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def attendance_location_is_allowed(latitude, longitude):
    """Check whether the supplied coordinates are inside one of the allowed geofences."""
    return attendance_location_is_allowed_in_set(latitude, longitude, building_locations_all())


def attendance_location_is_allowed_in_set(latitude, longitude, allowed_locations):
    """Check whether the supplied coordinates are inside one of the provided geofences."""
    if not allowed_locations:
        return False, None

    closest = None
    closest_distance = None
    for location in allowed_locations:
        distance = attendance_distance_meters(
            latitude,
            longitude,
            location["latitude"],
            location["longitude"],
        )
        if closest_distance is None or distance < closest_distance:
            closest = location
            closest_distance = distance
        if distance <= location["radius_m"]:
            return True, location

    return False, closest


def attendance_allowed_locations_for_room_building_name(building_name):
    """Return the configured geofences for one room location."""
    return building_locations_for_room_building_name(building_name)


def attendance_allowed_locations_for_room_location(room_location):
    """Backward-compatible wrapper for room building-name lookups."""
    return attendance_allowed_locations_for_room_building_name(room_location)


def attendance_allowed_locations_for_row(row):
    """Return the configured geofences for the room used by one attendance event."""
    return attendance_allowed_locations_for_room_building_name(row.get("room_building_name"))


def attendance_geofence_state_for_event(kind, event_id, event_date, row=None):
    """Return the persisted geofence toggle state for one attendance attempt."""
    row = row or attendance_event_row(kind, event_id, event_date)
    allowed_locations = attendance_allowed_locations_for_row(row) if row else []
    available = bool(allowed_locations)

    setting = query_db(
        """
        SELECT enabled
        FROM attendance_attempt_geofence_settings
        WHERE event_kind = ?
          AND event_id = ?
          AND event_date = ?
        """,
        (kind, event_id, event_date),
        one=True,
    )
    enabled = bool(setting["enabled"]) if setting is not None else available
    if not available:
        enabled = False

    return {
        "available": available,
        "enabled": enabled,
        "warning": None if available else "Локација за ову учионицу није подешена.",
        "locations": allowed_locations,
    }


def attendance_geofence_set_for_event(kind, event_id, event_date, enabled):
    """Persist the geofence toggle for one attendance attempt."""
    execute_db(
        """
        INSERT INTO attendance_attempt_geofence_settings
            (event_kind, event_id, event_date, enabled)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(event_kind, event_id, event_date) DO UPDATE SET
            enabled = excluded.enabled
        """,
        (
            kind,
            event_id,
            event_date,
            int(bool(enabled)),
        ),
    )
    return attendance_geofence_state_for_event(kind, event_id, event_date)


def attendance_optional_coordinates_from_payload(payload):
    """Parse optional numeric coordinates from a request payload."""
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude is None or longitude is None:
        return None, None
    try:
        return float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None, None


def attendance_geofence_rejection_payload(latitude, longitude, closest):
    """Build a diagnostic payload for a rejected mobile geofence check."""
    payload = {
        "error_code": "attendance_geofence_blocked",
        "error": "Пријава је могућа само у близини дозвољене локације.",
        "current_location": {
            "latitude": float(latitude),
            "longitude": float(longitude),
        },
    }
    if closest:
        distance = attendance_distance_meters(
            latitude,
            longitude,
            closest["latitude"],
            closest["longitude"],
        )
        payload["closest_location"] = {
            "name": closest.get("name"),
            "latitude": closest.get("latitude"),
            "longitude": closest.get("longitude"),
            "radius_m": closest.get("radius_m"),
            "distance_m": round(distance, 1),
        }
    return payload


# QR token and attendance-attempt helpers.
#
# The QR token is short-lived and rotates every 8 seconds. Once a student
# opens the tokenized QR link, the server sets a longer-lived attendance
# attempt cookie (90 seconds by default) so the student has time to enter
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


def attendance_attempt_cookie_name(kind, event_id, event_date):
    """Build the cookie name for one attendance attempt."""
    return f"attendance_attempt_{kind}_{event_id}_{event_date}"


def attendance_attempt_payload(kind, event_id, event_date, expires_at, nonce):
    """Serialize the signed attendance-attempt payload."""
    return f"{kind}:{event_id}:{event_date}:{expires_at}:{nonce}"


def attendance_attempt_sign(payload):
    """Sign an attendance-attempt payload with the shared secret."""
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def attendance_create_attempt_token(kind, event_id, event_date, now=None):
    """Create the signed cookie value that keeps a student on the join page."""
    now = now or datetime.datetime.now()
    expires_at = int(now.timestamp()) + ATTENDANCE_ATTEMPT_TTL
    nonce = secrets.token_urlsafe(12)
    payload = attendance_attempt_payload(kind, event_id, event_date, expires_at, nonce)
    signature = attendance_attempt_sign(payload)
    return f"{payload}.{signature}"


def attendance_validate_attempt_token(kind, event_id, event_date, token, now=None):
    """Verify the signed attendance-attempt cookie and its expiry time."""
    if not token or "." not in token:
        return False
    now = now or datetime.datetime.now()
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(attendance_attempt_sign(payload), signature):
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


def attendance_attempt_from_request(kind, event_id, event_date):
    """Read the attendance-attempt cookie from the current request."""
    cookie_name = attendance_attempt_cookie_name(kind, event_id, event_date)
    return request.cookies.get(cookie_name)


def attendance_has_attempt(kind, event_id, event_date):
    """Return True when the request carries a valid attendance-attempt cookie."""
    token = attendance_attempt_from_request(kind, event_id, event_date)
    return attendance_validate_attempt_token(kind, event_id, event_date, token)


def attendance_make_attempt_response(response, kind, event_id, event_date):
    """Attach a fresh attendance-attempt cookie to a redirect response."""
    token = attendance_create_attempt_token(kind, event_id, event_date)
    cookie_name = attendance_attempt_cookie_name(kind, event_id, event_date)
    response.set_cookie(
        cookie_name,
        token,
        max_age=ATTENDANCE_ATTEMPT_TTL,
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
    attendance_cleanup_expired_attempt_failures()
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
    """Render the student attendance page after a QR scan creates an attempt."""
    attendance_cleanup_expired_attempt_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    if not attendance_has_attempt(kind, event_id, event_date):
        abort(403)
    return render_template(
        'attendance_join.html',
        attendance_kind=kind,
        attendance_event_id=event_id,
        attendance_event_date=event_date,
    )



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/join/<token>')
def attendance_join_view_token(kind, event_id, event_date, token):
    """Validate a short-lived QR token and create the student attendance attempt."""
    attendance_cleanup_expired_attempt_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({"error": "event not found"}), 404
    if not attendance_is_open_now(row):
        return jsonify({"error_code": "attendance_outside_class_time", "error": attendance_not_open_message()}), 403
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
    return attendance_make_attempt_response(response, kind, event_id, event_date)



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/challenge')
def attendance_challenge_data(kind, event_id, event_date):
    """Return the current challenge code and event metadata."""
    attendance_cleanup_expired_attempt_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    if not attendance_is_open_now(row):
        return jsonify({'error_code': 'attendance_outside_class_time', 'error': attendance_not_open_message()}), 403
    mobile_session = attendance_mobile_session_from_request()
    attendance_attempt_token = attendance_attempt_from_request(kind, event_id, event_date) or ""
    if mobile_session:
        attendance_attempt_token = request.args.get("attendance_attempt_token", "").strip()
        join_token = request.args.get("join_token", "").strip()
        if attendance_attempt_token:
            if attendance_attempt_is_blocked_raw(attendance_attempt_token):
                return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
            if not attendance_validate_attempt_token(kind, event_id, event_date, attendance_attempt_token):
                return jsonify({'error_code': 'attendance_attempt_expired', 'error': 'Покушај је истекао. Поново скенирајте QR код.'}), 403
        else:
            if attendance_attempt_is_blocked_raw(join_token):
                return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
            if not attendance_token_is_valid(kind, event_id, event_date, join_token):
                return jsonify({'error_code': 'attendance_attempt_expired', 'error': 'Покушај је истекао. Поново скенирајте QR код.'}), 403
            attendance_attempt_token = attendance_create_attempt_token(kind, event_id, event_date)
    else:
        attempt_status = attendance_attempt_status(kind, event_id, event_date)
        if attempt_status == "blocked":
            return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
        if attempt_status != "valid":
            return jsonify({'error_code': 'attendance_attempt_expired', 'error': 'Покушај је истекао. Поново скенирајте QR код.'}), 403

    challenge = attendance_challenge_for_time(kind, event_id, event_date)
    geofence_state = attendance_geofence_state_for_event(kind, event_id, event_date, row=row)
    payload = {
        'event': row,
        'challenge': challenge,
        'attendance_geofence_available': geofence_state["available"],
        'attendance_geofence_enabled': geofence_state["enabled"],
        'attendance_geofence_warning': geofence_state["warning"],
        'attendance_attempt_token': attendance_attempt_token,
    }
    if mobile_session:
        payload['attendance_locations'] = geofence_state["locations"]
    response = jsonify(payload)
    return response



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/data')
def attendance_roster_data(kind, event_id, event_date):
    """Return teacher attendance data, including the current attendance list and QR token."""
    attendance_cleanup_expired_attempt_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403

    open_now = attendance_is_open_now(row)
    geofence_state = attendance_geofence_state_for_event(kind, event_id, event_date, row=row)

    return jsonify({
        'event': row,
        'students': attendance_records_for_event(kind, event_id, event_date),
        'can_view': True,
        'attendance_open': open_now,
        'attendance_geofence_available': geofence_state["available"],
        'attendance_geofence_enabled': geofence_state["enabled"],
        'attendance_geofence_warning': geofence_state["warning"],
        **(
            {
                'challenge': attendance_challenge_for_time(kind, event_id, event_date),
                'join_token': attendance_join_token(kind, event_id, event_date),
            }
            if open_now
            else {}
        ),
    })


@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/geofence', methods=['POST'])
def attendance_geofence_update(kind, event_id, event_date):
    """Persist the teacher geofence toggle for a single attendance attempt."""
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403

    geofence_state = attendance_geofence_state_for_event(kind, event_id, event_date, row=row)
    if not geofence_state["available"]:
        return jsonify({'error': 'Локација за ову учионицу није подешена.'}), 409

    payload = request.get_json(silent=True) or {}
    if "enabled" not in payload:
        return jsonify({'error': 'enabled is required'}), 400

    state = attendance_geofence_set_for_event(
        kind,
        event_id,
        event_date,
        bool(payload.get("enabled")),
    )
    return jsonify({
        'success': True,
        'attendance_geofence_available': state["available"],
        'attendance_geofence_enabled': state["enabled"],
        'attendance_geofence_warning': state["warning"],
    })


@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/spot_check', methods=['GET'])
def attendance_spot_check_data(kind, event_id, event_date):
    """Return a shortlist of the most suspicious students for a manual teacher check."""
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403
    if not attendance_is_open_now(row):
        return jsonify({'error_code': 'attendance_outside_class_time', 'error': attendance_not_open_message()}), 403

    limit = request.args.get('limit', '5').strip()
    try:
        limit_value = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid limit'}), 400

    students = attendance_spot_check_candidates_for_event(kind, event_id, event_date, limit=limit_value)
    return jsonify({
        'event': row,
        'limit': limit_value,
        'students': [
            {
                'username': student['username'],
                'student_label': student['student_label'],
            }
            for student in students
        ],
    })


@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/spot_check', methods=['POST'])
def attendance_spot_check_submit(kind, event_id, event_date):
    """Store the shortlist entries that the teacher did not confirm."""
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403
    if not attendance_is_open_now(row):
        return jsonify({'error_code': 'attendance_outside_class_time', 'error': attendance_not_open_message()}), 403

    payload = request.get_json(silent=True) or {}
    selected_usernames = payload.get('selected_usernames') or []
    confirmed_usernames = payload.get('confirmed_usernames') or []
    if not isinstance(selected_usernames, list) or not isinstance(confirmed_usernames, list):
        return jsonify({'error': 'selected_usernames_and_confirmed_usernames_are_required'}), 400

    teacher_username = getattr(current_user, 'username', '') or getattr(current_user, 'id', '')
    recorded = attendance_spot_check_record_misses(
        kind,
        event_id,
        event_date,
        selected_usernames,
        confirmed_usernames,
        teacher_username,
    )
    return jsonify({'success': True, 'recorded': recorded})



@bp.route('/attendance/<kind>/<int:event_id>/<event_date>/join', methods=['POST'])
def attendance_join_submit(kind, event_id, event_date):
    """Accept a student attendance check-in after validating credentials and the challenge code."""
    limited = enforce_rate_limit("attendance", *RATE_LIMITS["attendance"])
    if limited is not None:
        return limited

    attendance_cleanup_expired_attempt_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    if not attendance_is_open_now(row):
        return jsonify({'error_code': 'attendance_outside_class_time', 'error': attendance_not_open_message()}), 403
    data = request.get_json() or {}
    selected_code = data.get('selected_code')
    mobile_session = attendance_mobile_session_from_request()
    geofence_state = attendance_geofence_state_for_event(kind, event_id, event_date, row=row)
    latitude, longitude = attendance_optional_coordinates_from_payload(data)
    if mobile_session:
        attempt_token = data.get('attendance_attempt_token', '').strip() or data.get('join_token', '').strip()
        if not attempt_token:
            return jsonify({'error_code': 'attendance_attempt_required', 'error': 'attendance attempt token is required'}), 400
        if attendance_attempt_is_blocked_raw(attempt_token):
            return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
        if not attendance_validate_attempt_token(kind, event_id, event_date, attempt_token):
            return jsonify({'error_code': 'attendance_attempt_expired', 'error': 'Покушај је истекао. Поново скенирајте QR код.'}), 403
        username = mobile_session["user"]["radius_username"].strip()
        password = None
        geofence_checked = bool(geofence_state["available"] and geofence_state["enabled"])
        if geofence_checked and not geofence_state["locations"]:
            return jsonify({"error_code": "attendance_location_missing", "error": "Локација за ову учионицу није подешена."}), 403
        if geofence_checked:
            if latitude is None or longitude is None:
                return jsonify({"error_code": "attendance_location_required", "error": "Потребна је локација уређаја."}), 403
            allowed, closest = attendance_location_is_allowed_in_set(latitude, longitude, geofence_state["locations"])
            if not allowed:
                attendance_attempt_block(attempt_token)
                payload = attendance_geofence_rejection_payload(latitude, longitude, closest)
                payload["error"] = "Локација није у дозвољеном опсегу. Покушај је закључан. Поново скенирајте QR код."
                return jsonify(payload), 403
        else:
            geofence_checked = False
    else:
        attempt_token = attendance_attempt_from_request(kind, event_id, event_date)
        attempt_status = attendance_attempt_status(kind, event_id, event_date)
        if attempt_status == "blocked":
            return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
        if attempt_status != "valid":
            return jsonify({'error_code': 'attendance_attempt_expired', 'error': 'Покушај је истекао. Поново скенирајте QR код.'}), 403
        username = data.get('username', '').strip()
        password = data.get('password', '')
        geofence_checked = False

    if not username or (password is not None and not password):
        return jsonify({'error': 'username and password are required'}), 400

    try:
        selected_code = int(selected_code)
    except (TypeError, ValueError):
        return jsonify({'error_code': 'attendance_selected_code_required', 'error': 'selected code is required'}), 400

    if not attendance_code_is_valid(kind, event_id, event_date, selected_code):
        state = attendance_attempt_record_failure(attempt_token)
        if state and state["blocked"]:
            return jsonify({'error_code': 'attendance_attempt_blocked', 'error': attendance_attempt_blocked_message()}), 403
        return jsonify({'error_code': 'attendance_wrong_code', 'error': 'Погрешан број. Сачекајте нови круг.'}), 409

    if password is not None and not student_radius_auth(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    failure_state = attendance_attempt_failure_state_raw(attempt_token) or {}
    attendance_attempt_clear_failures(attempt_token)
    registration_source = "android" if mobile_session else "web"
    attendance_record_student(
        kind,
        event_id,
        event_date,
        username,
        registration_source=registration_source,
        client_ip=attendance_client_ip(),
        client_latitude=latitude,
        client_longitude=longitude,
        geofence_checked=geofence_checked,
        failed_attempts_before_success=int(failure_state.get("failed_attempts") or 0),
    )
    return jsonify({'success': True, 'username': username})
