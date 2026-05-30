import app as myapp


def login(client, username="alice", password="secret"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
    )


def test_login_logout_and_whoami(client):
    r = client.get("/whoami")
    assert r.get_json() == {"logged_in": False}

    r = login(client, "alice")
    data = r.get_json()
    assert r.status_code == 200
    assert data["success"] is True
    assert data["username"] == "alice"
    assert data["role"] == "teacher"

    r = client.get("/whoami")
    assert r.get_json() == {"logged_in": True, "username": "alice"}

    r = client.post("/logout")
    assert r.status_code == 200
    assert r.get_json() == {"success": True}

    r = client.get("/whoami")
    assert r.get_json() == {"logged_in": False}


def test_login_failure(client, monkeypatch):
    monkeypatch.setattr(myapp, "radius_auth", lambda username, password: False)

    r = login(client, "alice")
    assert r.status_code == 401
    assert r.get_json() == {"error": "Invalid credentials"}


def test_is_admin(client, db):
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("alice",))

    r = client.get("/is_admin/alice")
    assert r.get_json() == {"username": "alice", "is_admin": True}

    r = client.get("/is_admin/bob")
    assert r.get_json() == {"username": "bob", "is_admin": False}


def test_reserve_success_and_conflicts(client, db):
    room = db.room("A1")

    login(client, "alice")

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-09",
            "start_slot": 8,
            "end_slot": 10,
            "description": "study",
        },
    )
    assert r.status_code == 201
    first_id = r.get_json()["reservation_id"]

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-09",
            "start_slot": 9,
            "end_slot": 11,
            "description": "overlap",
        },
    )
    assert r.status_code == 409

    teacher = db.teacher("Prof", "prof")
    course = db.course("Algorithms")
    semester = db.semester(name="Spring 2026")
    session = db.course_session(course, teacher, semester)
    db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=12,
        end_slot=14,
    )

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-09",
            "start_slot": 12,
            "end_slot": 13,
            "description": "weekly conflict",
        },
    )
    assert r.status_code == 409

    r = client.delete(f"/reservation/{first_id}")
    assert r.status_code == 200


def test_reserve_validation_errors(client, db):
    db.room("A1")
    login(client, "alice")

    r = client.post("/reserve", json={})
    assert r.status_code == 400

    r = client.post(
        "/reserve",
        json={
            "room_id": 999,
            "date": "2026-03-09",
            "start_slot": 8,
            "end_slot": 10,
            "description": "missing room",
        },
    )
    assert r.status_code == 400

    r = client.post(
        "/reserve",
        json={
            "room_id": 1,
            "date": "2026-03-09",
            "start_slot": 10,
            "end_slot": 10,
            "description": "bad slots",
        },
    )
    assert r.status_code == 400

    r = client.post(
        "/reserve",
        json={
            "room_id": 1,
            "date": "2026-13-01",
            "start_slot": 8,
            "end_slot": 10,
            "description": "bad date",
        },
    )
    assert r.status_code == 400


def test_admin_can_set_username_on_reserve(client, db):
    db.room("A1")
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("admin",))
    login(client, "admin")

    r = client.post(
        "/reserve",
        json={
            "room_id": 1,
            "date": "2026-03-09",
            "start_slot": 8,
            "end_slot": 10,
            "description": "admin owned",
            "username": "other.user",
        },
    )
    assert r.status_code == 201

    occupancy = client.get("/occupancy?date=2026-03-09").get_json()
    assert occupancy["rooms"]["1"][0]["username"] == "other.user"


def test_delete_reservation_permissions(client, db):
    room = db.room("A1")
    res_id = db.reservation(
        room_id=room,
        date="2026-03-09",
        start=8,
        end=10,
        description="meeting",
        username="owner",
    )

    login(client, "other")
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 403

    client.post("/logout")
    login(client, "admin")
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("admin",))
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_delete_reservation_unauthorized(client, db):
    room = db.room("A1")
    res_id = db.reservation(
        room_id=room,
        date="2026-03-09",
        start=8,
        end=10,
        description="meeting",
        username="owner",
    )

    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 401
    assert r.get_json() == {"error": "Unauthorized"}


def test_bulk_reservations_atomic(client, db):
    room1 = db.room("A1")
    room2 = db.room("A2")

    login(client, "alice")

    r = client.post(
        "/reserve/bulk",
        json={
            "reservations": [
                {
                    "room_id": room1,
                    "date": "2026-03-09",
                    "start_slot": 8,
                    "end_slot": 10,
                    "description": "first",
                    "username": "",
                },
                {
                    "room_id": room2,
                    "date": "2026-03-09",
                    "start_slot": 9,
                    "end_slot": 11,
                    "description": "second",
                    "username": "",
                },
            ]
        },
    )
    assert r.status_code == 201
    assert r.get_json()["created"] == 2

    r = client.post(
        "/reserve/bulk",
        json={
            "reservations": [
                {
                    "room_id": room1,
                    "date": "2026-03-09",
                    "start_slot": 8,
                    "end_slot": 10,
                    "description": "dup",
                    "username": "",
                },
                {
                    "room_id": room2,
                    "date": "2026-03-09",
                    "start_slot": 8,
                    "end_slot": 10,
                    "description": "should rollback",
                    "username": "",
                },
            ]
        },
    )
    assert r.status_code == 409

    r = client.get("/occupancy?date=2026-03-09")
    rooms = r.get_json()["rooms"]
    assert len(rooms[str(room1)]) == 1
    assert len(rooms[str(room2)]) == 1


def test_calendar_and_update_calendar(client, db):
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("admin",))

    r = client.get("/calendar_data?month=3&year=2026")
    assert r.status_code == 200
    data = r.get_json()
    assert "calendar" in data
    assert "holidays" in data

    r = client.get("/calendar_data?month=13&year=2026")
    assert r.status_code == 400

    login(client, "admin")
    r = client.post(
        "/update_calendar",
        json=[
            {
                "date": "2026-03-10",
                "is_working": True,
                "week_day": 2,
            }
        ],
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True}

    client.post("/logout")
    r = client.post(
        "/update_calendar",
        json=[{"date": "2026-03-11", "is_working": True, "week_day": 3}],
    )
    assert r.status_code == 401


def test_service_token_update_calendar(client, db):
    r = client.post(
        "/update_calendar",
        headers={"Authorization": f"Bearer {myapp.app.config['SERVICE_API_KEY']}"},
        json=[
            {
                "date": "2026-03-12",
                "is_working": True,
                "week_day": 3,
            }
        ],
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_service_token_bulk_reservations(client, db):
    room1 = db.room("A1")
    room2 = db.room("A2")

    r = client.post(
        "/reserve/bulk",
        headers={"Authorization": f"Bearer {myapp.app.config['SERVICE_API_KEY']}"},
        json={
            "reservations": [
                {
                    "room_id": room1,
                    "date": "2026-03-09",
                    "start_slot": 8,
                    "end_slot": 10,
                    "description": "service one",
                    "username": "service.user",
                },
                {
                    "room_id": room2,
                    "date": "2026-03-09",
                    "start_slot": 10,
                    "end_slot": 12,
                    "description": "service two",
                    "username": "service.user",
                },
            ]
        },
    )
    assert r.status_code == 201
    assert r.get_json()["created"] == 2

    occupancy = client.get("/occupancy?date=2026-03-09").get_json()
    assert occupancy["rooms"][str(room1)][0]["username"] == "service.user"
    assert occupancy["rooms"][str(room2)][0]["username"] == "service.user"


def test_weekly_session_cancel_and_conflict(client, db):
    room = db.room("A1")
    teacher = db.teacher("Prof", "prof")
    course = db.course("Algorithms")
    semester = db.semester()
    session = db.course_session(course, teacher, semester)
    weekly_id = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=2,
        end_slot=4,
    )

    login(client, "prof")

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": "2026-03-09"},
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "canceled": True}

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-09",
            "start_slot": 2,
            "end_slot": 4,
            "description": "occupy slot",
        },
    )
    assert r.status_code == 201

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": "2026-03-09"},
    )
    assert r.status_code == 409


def test_weekly_session_cancel_validation(client, db):
    teacher = db.teacher("Prof", "prof")
    course = db.course("Algorithms")
    semester = db.semester()
    session = db.course_session(course, teacher, semester)
    weekly_id = db.weekly_session(
        session_id=session,
        room_id=db.room("A1"),
        day_of_week=1,
        start_slot=2,
        end_slot=4,
    )

    login(client, "prof")

    r = client.post("/weekly_session_cancel", json={"weekly_session_id": weekly_id})
    assert r.status_code == 400

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": "2026-13-40"},
    )
    assert r.status_code == 400

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": 9999, "date": "2026-03-10"},
    )
    assert r.status_code == 400

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": 9999, "date": "2026-03-09"},
    )
    assert r.status_code == 404
