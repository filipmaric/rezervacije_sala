import app as myapp


def test_occupancy_reservation(client, db):

    room_id = db.room("A1")

    db.reservation(
        room_id=room_id,
        date="2026-03-09",
        start=2,
        end=4,
        description="meeting",
    )

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()

    assert data["date"] == "2026-03-09"
    assert str(room_id) in data["rooms"]

    events = data["rooms"][str(room_id)]

    assert events[0]["type"] == "reservation"
    assert events[0]["description"] == "meeting"
    
def test_occupancy_missing_date(client):
    r = client.get("/occupancy")

    assert r.status_code == 400

def test_weekly_session(client, db):
    room = db.room("A1")
    teacher = db.teacher("Prof", "prof")
    course = db.course("Algorithms")
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

def test_weekly_lecture(client, db):
    schedule = db.schedule()

    schedule.lecture(
        course="Algorithms",
        teacher="Prof",
        room="A1",
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

    s.lecture("Algorithms", teacher="Prof1", room="A1", day=1, start=2, end=4)
    s.lecture("Databases", teacher="Prof2", room="A2", day=1, start=3, end=5)

    r = client.get("/occupancy?date=2026-03-09")

    data = r.get_json()

    assert len(data["rooms"]) == 2    

def test_canceled_class(client, db):
    s = db.schedule()

    room = s.room("A1")

    s.lecture("Algorithms", teacher="Prof", room="A1", day=1, start=2, end=4)

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

    schedule.room("A1")
    schedule.teacher("Prof", "prof")
    schedule.course("Algorithms")
    past_semester = db.semester(name="Past 2025", start="2025-01-01", end="2025-12-31")
    session = db.course_session(
        schedule.courses["Algorithms"],
        schedule.teachers["Prof"],
        past_semester,
    )
    db.weekly_session(
        session_id=session,
        room_id=schedule.rooms["A1"],
        day_of_week=1,
        start_slot=2,
        end_slot=4,
    )

    r = client.get("/occupancy?date=2026-03-09")
    data = r.get_json()

    assert data["rooms"] == {}
