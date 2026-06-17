from datetime import datetime, timedelta


def login(client, username="alice", password="secret"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
    )


def test_index_template(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Резервације учионица" in r.get_data(as_text=True)
    assert "Моје резервације" in r.get_data(as_text=True)


def test_calendar_template(client):
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "Mesec:" in r.get_data(as_text=True)


def test_my_reservations_template(client):
    r = client.get("/my_reservations")
    assert r.status_code == 200
    assert "Моје резервације" in r.get_data(as_text=True)


def test_attendance_templates(client, db):
    login(client, "alice")

    now = datetime.now()
    event_date = now.date().isoformat()
    start_slot = max(0, now.hour - 1)
    end_slot = min(23, start_slot + 2)

    semester = db.semester(
        name="Current 2026",
        start=event_date,
        end=(now + timedelta(days=1)).date().isoformat(),
    )
    room = db.room("R1")
    teacher = db.teacher("Prof", "alice")
    course = db.course("NumericalMethods")
    session = db.course_session(course, teacher, semester)
    weekly_session_id = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=now.weekday(),
        start_slot=start_slot,
        end_slot=end_slot,
    )

    roster = client.get(f"/attendance/weekly/{weekly_session_id}/{event_date}/data")
    assert roster.status_code == 200
    token = roster.get_json()["join_token"]

    r = client.get(f"/attendance/weekly/1/{event_date}")
    assert r.status_code == 200
    assert "Присуство на часу" in r.get_data(as_text=True)

    r = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join/{token}",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Пријава присуства" in r.get_data(as_text=True)
