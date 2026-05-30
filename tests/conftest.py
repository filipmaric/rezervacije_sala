import pytest
import sqlite3
import tempfile
import os

import app as myapp

class ScheduleFactory:

    def __init__(self, db):
        self.db = db
        self.semester_id = db.semester()
        self.rooms = {}
        self.teachers = {}
        self.courses = {}

    def room(self, name="A1", capacity=50, type="lecture"):
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

    def lecture(self, course, teacher="Teacher", room="A1",
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

    def room(self, name="A1", capacity=50, type="lecture", location="A", priority=1):
        return self.execute(
            """INSERT INTO rooms (name, capacity, type, location, priority)
               VALUES (?, ?, ?, ?, ?)""",
            (name, capacity, type, location, priority),
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

    yield myapp.app

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    return TestDB(app)
