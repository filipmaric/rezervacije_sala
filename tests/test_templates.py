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

    semester = db.semester(
        name="Current 2026",
        start="2026-01-01",
        end="2026-12-31",
    )
    room = db.room("R1")
    teacher = db.teacher("Prof", "alice")
    course = db.course("NumericalMethods")
    session = db.course_session(course, teacher, semester)
    weekly_session_id = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=10,
        end_slot=12,
    )

    roster = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/data")
    assert roster.status_code == 200
    token = roster.get_json()["join_token"]

    r = client.get("/attendance/weekly/1/2026-03-09")
    assert r.status_code == 200
    assert "Присуство на часу" in r.get_data(as_text=True)

    r = client.get(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join/{token}",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Пријава присуства" in r.get_data(as_text=True)
