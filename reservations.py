"""Reservation write and cancellation endpoints."""

import datetime

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from auth import RATE_LIMITS, check_if_admin, enforce_rate_limit, login_or_service_required
from db import execute_db, get_db, query_db
from occupancy import check_day

bp = Blueprint("reservations", __name__)


# Read endpoints.


@bp.route("/is_admin/<username>")
def is_admin(username):
    """Return the admin status for one username."""
    return jsonify({"username": username, "is_admin": check_if_admin(username)})


# Reservation write helpers.


def _create_single_reservation(data, username, is_service, commit=True):
    """Validate and store one reservation request."""
    room_id = data.get('room_id')
    date = data.get('date')
    start = data.get('start_slot')
    end = data.get('end_slot')
    desc = data.get('description', '')

    if not all([room_id is not None, date, start is not None, end is not None]):
        abort(400, 'missing fields')

    try:
        datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'invalid date format, expected YYYY-MM-DD')

    if not (isinstance(start, int) and isinstance(end, int) and start < end):
        abort(400, 'invalid slots')

    room = query_db('SELECT id FROM rooms WHERE id = ?', (room_id,), one=True)
    if room == None:
        abort(400, f'room not found {room_id}')

    (is_working, week_day, dow) = check_day(date)

    if is_working:
        wc_conf = query_db('''
            SELECT 1
            FROM weekly_sessions ws
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN semesters s ON s.id = cs.semester_id
            LEFT JOIN weekly_cancellations wxc
                   ON wxc.weekly_session_id = ws.id
                  AND wxc.date = ?
            WHERE ws.room_id = ?
              AND ws.day_of_week = ?
              AND ? BETWEEN s.start_date AND s.end_date
              AND (ws.start_slot < ? AND ? < ws.end_slot)
              AND wxc.id IS NULL
        ''', (date, room_id, dow, date, end, start))

        if wc_conf:
            abort(409, 'conflict with regular weekly class')

    res_conf = query_db('''
        SELECT 1 FROM reservations
        WHERE room_id = ? AND date = ?
        AND (start_slot < ? AND ? < end_slot)
    ''', (room_id, date, end, start))

    if res_conf:
        abort(409, 'conflict with existing reservation')

    rid = execute_db('''
        INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (room_id, username, date, start, end, desc), commit=commit)

    return rid


# Reservation write endpoints.


@bp.route('/reserve', methods=['POST'])
@login_or_service_required
def create_reservation():
    """Create one reservation from a browser or service request."""
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    data = request.get_json() or {}
    is_service = getattr(g, "service_auth", False)

    username = data.get('username', '')
    if not is_service and (not check_if_admin(current_user.username) or username == ''):
        username = current_user.username

    rid = _create_single_reservation(data, username, is_service)

    return jsonify({'reservation_id': rid}), 201


@bp.route('/reserve/bulk', methods=['POST'])
@login_or_service_required
def bulk_reservations():
    """Create several reservations atomically."""
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    payload = request.get_json() or {}
    reservations = payload.get('reservations')

    if not reservations or not isinstance(reservations, list):
        abort(400, 'reservations must be a list')

    is_service = getattr(g, "service_auth", False)

    created_ids = []

    conn = get_db()
    try:
        conn.execute("BEGIN")
        for r in reservations:
            username = r['username']
            if not is_service and (not check_if_admin(current_user.username) or username == ''):
                username = current_user.username

            rid = _create_single_reservation(r, username, is_service, commit=False)
            created_ids.append(rid)
        conn.commit()

    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            return jsonify({'error': 'bulk reservation failed'}), e.code
        current_app.logger.exception("Bulk reservation failed")
        return jsonify({'error': 'bulk reservation failed'}), 409

    return jsonify({
        'created': len(created_ids),
        'reservation_ids': created_ids
    }), 201


# Reservation cancellation endpoints.


@bp.route('/reservation/<int:res_id>', methods=['DELETE'])
@login_or_service_required
def cancel_reservation(res_id):
    """Cancel one reservation if the caller owns it or is an admin."""
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    # Check whether the reservation exists and belongs to the current user
    row = query_db('SELECT username FROM reservations WHERE id = ?', (res_id,), one=True)
    if not row:
        return jsonify({'error':'Reservation not found'}), 404

    is_service = getattr(g, "service_auth", False)
    if not is_service and row['username'] != current_user.username and not check_if_admin(current_user.username):
        return jsonify({'error':'Forbidden'}), 403

    # Delete the reservation
    execute_db('DELETE FROM reservations WHERE id = ?', (res_id,))
    return jsonify({'success': True})


# Weekly-session cancellation endpoints.


@bp.route('/weekly_session_cancel', methods=['POST'])
@login_or_service_required
def cancel_weekly_session_for_date():
    """Toggle cancellation for one weekly lecture occurrence."""
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    data = request.get_json() or {}
    ws_id = data.get('weekly_session_id')
    date = data.get('date')

    if not ws_id or not date:
        return jsonify({'error': 'weekly_session_id and date are required'}), 400

    # validate date format
    try:
        _ = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'invalid date format, expected YYYY-MM-DD'}), 400

    # resolve working day and weekday index
    is_working, week_day, dow = check_day(date)
    if not is_working:
        return jsonify({'error': 'selected date is not a working day'}), 400

    # verify that this weekly session is active on that date (semester bounds and weekday)
    row = query_db(
        '''
        SELECT ws.id,
               ws.room_id,
               ws.start_slot,
               ws.end_slot,
               t.username AS teacher_username
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN teachers t ON t.id = cs.teacher_id
        WHERE ws.id = ?
          AND ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        ''',
        (ws_id, dow, date),
        one=True
    )

    if not row:
        return jsonify({'error': 'weekly session not found for given date'}), 404

    is_service = getattr(g, "service_auth", False)

    # Allowed: the teacher who teaches the class, an administrator, or a service account
    if not is_service and current_user.is_authenticated:
        if not (current_user.username == row['teacher_username'] or check_if_admin(current_user.username)):
            return jsonify({'error': 'Forbidden'}), 403

    username = current_user.username if (current_user.is_authenticated and not is_service) else 'service'

    existing = query_db(
        '''
        SELECT id FROM weekly_cancellations
        WHERE weekly_session_id = ? AND date = ?
        ''',
        (ws_id, date),
        one=True
    )

    if existing:
        # already canceled -> before restoring it, check whether any reservation
        # overlaps with that slot
        res_conf = query_db(
            '''
            SELECT 1
            FROM reservations
            WHERE room_id = ?
              AND date = ?
              AND (start_slot < ? AND ? < end_slot)
            ''',
            (row['room_id'], date, row['end_slot'], row['start_slot']),
            one=True
        )
        if res_conf:
            return jsonify({
                'error': 'Cannot restore the canceled class because a reservation exists in this slot.'
            }), 409

        execute_db(
            'DELETE FROM weekly_cancellations WHERE id = ?',
            (existing['id'],)
        )
        return jsonify({'success': True, 'canceled': False})

    # no cancellation exists -> create one
    execute_db(
        '''
        INSERT INTO weekly_cancellations (weekly_session_id, date, username)
        VALUES (?, ?, ?)
        ''',
        (ws_id, date, username)
    )

    return jsonify({'success': True, 'canceled': True})
