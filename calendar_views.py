"""Calendar metadata and update endpoints."""

import datetime

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user

from occupancy import iso_to_weekday

from auth import RATE_LIMITS, check_if_admin, enforce_rate_limit
from db import execute_db, query_db


bp = Blueprint("calendar_views", __name__)


# Calendar read helpers and endpoints.


@bp.route('/calendar_data')
def calendar_data():
    """Return the working-day metadata for one calendar month."""
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    if month is None or year is None:
        abort(400, 'month and year are required')
    if month < 1 or month > 12:
        abort(400, 'month must be between 1 and 12')

    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)

    rows = query_db('SELECT date, is_working, week_day FROM days WHERE date BETWEEN ? AND ?',
                    (first_day.isoformat(), last_day.isoformat()))

    calendar_dict = {}
    for r in rows:
        calendar_dict[r['date']] = {
            'is_working': r['is_working'],
            'week_day': r['week_day']
        }

    holidays = ['2026-01-01', '2026-01-07']

    return jsonify({'calendar': calendar_dict, 'holidays': holidays})


# Calendar update helpers and endpoints.


@bp.route('/update_calendar', methods=['POST'])
def update_calendar():
    """Update the calendar table for working days and overrides."""
    limited = enforce_rate_limit("calendar", *RATE_LIMITS["calendar"])
    if limited is not None:
        return limited

    updates = request.get_json()

    is_service = False
    if current_user.is_authenticated:
        if not check_if_admin(current_user.username):
            return jsonify({'error': 'Forbidden'}), 403
    else:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            is_service = token == current_app.config["SERVICE_API_KEY"]
        if not is_service:
            return jsonify({'error': 'Unauthorized'}), 401

    if not isinstance(updates, list):
        return jsonify({'error': 'expected a list of updates'}), 400

    for u in updates:
        date_str = u['date']
        is_working = u['is_working']
        week_day = u.get('week_day', -1)

        try:
            datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': f'invalid date format for {date_str}, expected YYYY-MM-DD'}), 400

        real_wd = iso_to_weekday(date_str)
        if week_day == real_wd:
            week_day = -1

        existing = query_db('SELECT 1 FROM days WHERE date = ?', (date_str,), one=True)
        if not is_working and not existing:
            continue

        execute_db(
            '''
            REPLACE INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            ''',
            (date_str, 1 if is_working else 0, week_day)
        )
    return jsonify(success=True)


# Page route.


@bp.route('/calendar')
def calendar_view():
    """Render the calendar page."""
    return render_template('calendar.html')
