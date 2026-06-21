import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for site_packages in (
    ROOT / "venv" / "lib" / "python3.10" / "site-packages",
    ROOT / "venv" / "lib" / "python3.11" / "site-packages",
):
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
        break

import pytest
import sqlite3
import tempfile
import os
import secrets

import app as myapp

class ScheduleFactory:

    def __init__(self, db):
        self.db = db
        self.semester_id = db.semester()
        self.rooms = {}
        self.teachers = {}
        self.courses = {}

    def room(self, name="R1", capacity=50, type="lecture"):
        room_id = self.db.room(name, capacity, type)
        self.rooms[name] = room_id
        return room_id

    def teacher(self, name="Teacher", username=None):
        if username is None:
            username = name.lower()
        teacher_id = self.db.teacher(name, username)
        self.teachers[name] = teacher_id
        return teacher_id

    def course(self, name):
        course_id = self.db.course(name)
        self.courses[name] = course_id
        return course_id

    def lecture(self, course, teacher="Teacher", room="R1",
                day=1, start=2, end=4, type="lecture"):

        if course not in self.courses:
            self.course(course)

        if teacher not in self.teachers:
            self.teacher(teacher)

        if room not in self.rooms:
            self.room(room)

        session_id = self.db.course_session(
            self.courses[course],
            self.teachers[teacher],
            self.semester_id,
            type
        )

        self.db.weekly_session(
            session_id=session_id,
            room_id=self.rooms[room],
            day_of_week=day,
            start_slot=start,
            end_slot=end
        )    

class TestDB:
    def __init__(self, app):
        self.app = app

    def execute(self, query, args=()):
        with self.app.app_context():
            return myapp.execute_db(query, args)

    def room(self, name="R1", capacity=50, type="lecture", building_name="A", location=None, priority=1):
        if location is not None:
            building_name = location
        return self.execute(
            """INSERT INTO rooms (name, capacity, type, building_name, priority)
               VALUES (?, ?, ?, ?, ?)""",
            (name, capacity, type, building_name, priority),
        )

    def building_location(self, building_name, latitude, longitude, radius_m=100):
        return self.execute(
            """INSERT INTO building_locations (building_name, latitude, longitude, radius_m)
               VALUES (?, ?, ?, ?)""",
            (building_name, latitude, longitude, radius_m),
        )

    def teacher(self, name="Teacher", username="t1"):
        return self.execute(
            "INSERT INTO teachers (name, username) VALUES (?, ?)",
            (name, username),
        )

    def course(self, name="Course"):
        return self.execute(
            "INSERT INTO courses (name) VALUES (?)",
            (name,),
        )

    def semester(self, name="Winter 2026", start="2026-01-01", end="2026-12-31"):
        return self.execute(
            "INSERT INTO semesters (name, start_date, end_date) VALUES (?, ?, ?)",
            (name, start, end),
        )

    def student(self, username, student_index, surname, given_name):
        return self.execute(
            """
            INSERT INTO students (username, student_index, surname, given_name)
            VALUES (?, ?, ?, ?)
            """,
            (username, student_index, surname, given_name),
        )

    def course_session(self, course_id, teacher_id, semester_id, type="lecture"):
        return self.execute(
            """INSERT INTO course_sessions
               (course_id, teacher_id, semester_id, type)
               VALUES (?, ?, ?, ?)""",
            (course_id, teacher_id, semester_id, type),
        )

    def weekly_session(self, session_id, room_id, day_of_week, start_slot, end_slot):
        return self.execute(
            """INSERT INTO weekly_sessions
               (session_id, room_id, day_of_week, start_slot, end_slot)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, room_id, day_of_week, start_slot, end_slot),
        )

    def reservation(self, room_id, date, start, end,
                    description="test", username="user"):
        return self.execute(
            """INSERT INTO reservations
               (room_id, date, start_slot, end_slot, description, username)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (room_id, date, start, end, description, username),
        )

    def schedule(self):
        return ScheduleFactory(self)    


class CsrfClientProxy:
    def __init__(self, client, csrf_token):
        self._client = client
        self.csrf_token = csrf_token

    def _with_csrf(self, kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("X-CSRFToken", self.csrf_token)
        kwargs["headers"] = headers
        return kwargs

    def get(self, *args, **kwargs):
        return self._client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._client.post(*args, **self._with_csrf(kwargs))

    def delete(self, *args, **kwargs):
        return self._client.delete(*args, **self._with_csrf(kwargs))

    def put(self, *args, **kwargs):
        return self._client.put(*args, **self._with_csrf(kwargs))

    def patch(self, *args, **kwargs):
        return self._client.patch(*args, **self._with_csrf(kwargs))

    def open(self, *args, **kwargs):
        method = kwargs.get("method")
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            kwargs = self._with_csrf(kwargs)
        return self._client.open(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)

@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp()

    myapp.app.config["TESTING"] = True
    myapp.DATABASE = db_path

    with myapp.app.app_context():
        conn = sqlite3.connect(db_path)

        with open("schema.sql") as f:
            conn.executescript(f.read())

        conn.execute(
            "INSERT INTO days (date, is_working, week_day) VALUES (?, ?, ?)",
            ("2026-03-09", 1, 1),
        )

        conn.commit()
        conn.close()

    myapp.reset_rate_limits()

    yield myapp.app

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    raw_client = app.test_client()
    csrf_token = secrets.token_urlsafe(32)
    with raw_client.session_transaction() as sess:
        sess["_csrf_token"] = csrf_token
    return CsrfClientProxy(raw_client, csrf_token)


@pytest.fixture
def db(app):
    return TestDB(app)
