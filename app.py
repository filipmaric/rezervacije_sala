"""
Flask app entry point and route registration.

Route overview:
- Authentication: /login, /logout, /whoami, /is_admin/<username>
- Android auth: /auth/login, /auth/logout, /auth/me, /auth/sessions, /healthz
- Frontend: /
- Rooms and occupancy: /rooms, /occupancy
- Reservation writes: /reserve, /reserve/bulk, /reservation/<id>, /weekly_session_cancel
- Semester and personal reservations: /my_reservations, /my_reservations_data
- Calendar: /calendar, /calendar_data, /update_calendar
- Attendance: /attendance/<kind>/<id>/<date>, /attendance/<kind>/<id>/<date>/join,
  /attendance/<kind>/<id>/<date>/join/<token>, /attendance/<kind>/<id>/<date>/challenge,
  /attendance/<kind>/<id>/<date>/data, /attendance/<kind>/<id>/<date>/join (POST)
"""

import argparse
from config import (
    DATABASE,
)
from factory import create_app
from db import execute_db, get_db, init_db, query_db

app = create_app()
import main as main_mod  # noqa: E402,F401 - registers the main page route on import
import auth  # noqa: E402,F401 - registers auth routes and helpers on import
from auth import *  # noqa: F401,F403,E402 - re-export auth helpers and routes
import mobile_auth as mobile_auth_mod  # noqa: E402,F401 - registers Android auth routes on import

auth.init_app(app)

import occupancy as occupancy_mod  # noqa: E402,F401 - registers room and occupancy routes on import
from occupancy import *  # noqa: F401,F403,E402 - re-export occupancy helpers for tests and callers
import attendance as attendance_mod  # noqa: E402,F401 - registers attendance routes on import
from attendance import *  # noqa: F401,F403,E402 - re-export attendance helpers for tests and callers

import calendar_views as calendar_views_mod  # noqa: E402,F401 - registers calendar routes on import
from calendar_views import *  # noqa: F401,F403,E402 - re-export calendar helpers for tests and callers

import reservations as reservations_mod  # noqa: E402,F401 - registers reservation routes on import
import semester as semester_mod  # noqa: E402,F401 - shared semester helpers on import
import reservations_views as reservations_views_mod  # noqa: E402,F401 - registers semester/personal-reservation routes on import
from semester import *  # noqa: F401,F403,E402 - re-export semester helpers for tests and callers
from reservations_views import *  # noqa: F401,F403,E402 - re-export semester/personal-reservation helpers for tests and callers

app.register_blueprint(auth.bp)
app.register_blueprint(mobile_auth_mod.bp)
app.register_blueprint(main_mod.bp)
app.register_blueprint(occupancy_mod.bp)
app.register_blueprint(attendance_mod.bp)
app.register_blueprint(calendar_views_mod.bp)
app.register_blueprint(reservations_mod.bp)
app.register_blueprint(reservations_views_mod.bp)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classroom reservation app")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "init-db"),
        default="run",
        help="run the web app or initialize the database schema",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    with app.app_context():
        if args.command == "init-db":
            created = init_db()
            if created:
                print(f"Initialized database schema at {DATABASE}")
            else:
                print(f"Database already initialized at {DATABASE}")
        else:
            get_db()
            app.run(host=args.host, port=args.port, debug=False)
