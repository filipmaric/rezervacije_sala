import app as myapp
import occupancy as occmod


def test_occupancy_reservation(client, db):

    room_id = db.room("R1")

    db.reservation(
        room_id=room_id,
        date="2026-06-18",
        start=2,
        end=4,
        description="meeting",
    )

    r = client.get("/occupancy?date=2026-06-18")

    data = r.get_json()

    assert data["date"] == "2026-06-18"
    assert str(room_id) in data["rooms"]

    events = data["rooms"][str(room_id)]

    assert events[0]["type"] == "reservation"
    assert events[0]["description"] == "meeting"
    assert events[0]["attendance_open"] is False
    assert events[0]["can_cancel"] is True


def test_occupancy_reservation_past_date_cannot_cancel(client, db):
    room_id = db.room("R1")

    db.reservation(
        room_id=room_id,
        date="2026-03-08",
        start=2,
        end=4,
        description="meeting",
    )

    r = client.get("/occupancy?date=2026-03-08")
    data = r.get_json()
    events = data["rooms"][str(room_id)]
    assert events[0]["can_cancel"] is False


def test_occupancy_reservation_with_attendance_cannot_cancel(client, db):
    room_id = db.room("R1")

    res_id = db.reservation(
        room_id=room_id,
        date="2026-06-18",
        start=2,
        end=4,
        description="meeting",
    )
    db.execute(
        """
        INSERT INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        ("reservation", res_id, "2026-06-18", "student1"),
    )

    r = client.get("/occupancy?date=2026-06-18")
    data = r.get_json()
    events = data["rooms"][str(room_id)]
    assert events[0]["can_cancel"] is False

def test_occupancy_missing_date(client):
    r = client.get("/occupancy")

    assert r.status_code == 400

def test_weekly_session(client, db):
    room = db.room("R1")
    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
    semester = db.semester()

    session = db.course_session(course, teacher, semester)

    db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=2,
        end_slot=4
    )

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()

    assert str(room) in data["rooms"]    


def test_weekly_session_attendance_open_flag(client, db, monkeypatch):
    schedule = db.schedule()

    schedule.lecture(
        course="NumericalMethods",
        teacher="Prof",
        room="R1",
        day=1,
        start=2,
        end=4
    )

    monkeypatch.setattr(occmod, "attendance_is_open_now", lambda row, now=None: True)
    r = client.get("/occupancy?date=2026-03-09")
    data = r.get_json()

    events = data["rooms"][str(schedule.rooms["R1"])]
    assert events[0]["attendance_open"] is True

def test_weekly_lecture(client, db):
    schedule = db.schedule()

    schedule.lecture(
        course="NumericalMethods",
        teacher="Prof",
        room="R1",
        day=1,
        start=2,
        end=4
    )

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()

    rooms = data["rooms"]

    assert len(rooms) == 1    

def test_multiple_rooms(client, db):
    s = db.schedule()

    s.lecture("NumericalMethods", teacher="Prof1", room="R1", day=1, start=2, end=4)
    s.lecture("Databases", teacher="Prof2", room="A2", day=1, start=3, end=5)

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()

    assert len(data["rooms"]) == 2    

def test_canceled_class(client, db):
    s = db.schedule()

    room = s.room("R1")

    s.lecture("NumericalMethods", teacher="Prof", room="R1", day=1, start=2, end=4)

    db.execute(
        """INSERT INTO weekly_cancellations
           (weekly_session_id, date, username)
           VALUES (1,'2026-03-09','user')"""
    )

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()    
    events = data["rooms"][str(room)]

    assert events[0]["type"] == "weekly"
    assert events[0]["canceled"] is True


def test_weekly_session_outside_semester_is_hidden(client, db):
    schedule = db.schedule()

    schedule.room("R1")
    schedule.teacher("Prof", "prof")
    schedule.course("NumericalMethods")
    past_semester = db.semester(name="Past 2025", start="2025-01-01", end="2025-12-31")
    session = db.course_session(
        schedule.courses["NumericalMethods"],
        schedule.teachers["Prof"],
        past_semester,
    )
    db.weekly_session(
        session_id=session,
        room_id=schedule.rooms["R1"],
        day_of_week=1,
        start_slot=2,
        end_slot=4,
    )

    r = client.get("/occupancy?date=2026-03-09")
    data = r.get_json()

    assert data["rooms"] == {}
