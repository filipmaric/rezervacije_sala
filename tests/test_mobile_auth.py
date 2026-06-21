import app as myapp


def _mobile_login(client, username="alice", password="secret", device_id="device-1", device_name="Pixel"):
    return client.post(
        "/mobile/login",
        json={
            "username": username,
            "password": password,
            "device_id": device_id,
            "device_name": device_name,
        },
    )


def test_mobile_auth_login_me_and_logout(client, db, monkeypatch):
    import attendance as attendancemod

    response = _mobile_login(client)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["token"]
    assert payload["token_type"] == "Bearer"
    assert payload["user"]["radius_username"] == "alice"
    assert payload["session"]["device_id"] == "device-1"
    assert "attendance_locations" not in payload

    token = payload["token"]

    response = client.get("/mobile/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["radius_username"] == "alice"
    assert payload["session"]["device_name"] == "Pixel"
    assert "last_seen_at" in payload["session"]
    assert "attendance_locations" not in payload

    semester = db.semester(name="Current 2026", start="2026-01-01", end="2026-12-31")
    db.building_location("A", 10.0, 20.0, radius_m=100)
    room = db.room("R1", building_name="A")
    teacher = db.teacher("Prof", "alice")
    course = db.course("NumericalMethods")
    session = db.course_session(course, teacher, semester)
    weekly_session = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=10,
        end_slot=12,
    )
    join_token = myapp.attendance_join_token("weekly", weekly_session, "2026-03-09")

    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)

    response = client.get(
        f"/attendance/weekly/{weekly_session}/2026-03-09/challenge",
        headers={"Authorization": f"Bearer {token}"},
        query_string={"join_token": join_token},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["attendance_locations"][0]["name"] == "A"

    response = client.get("/mobile/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_session_id"]
    assert "attendance_locations" not in payload

    response = client.post("/mobile/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}

    response = client.get("/mobile/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_mobile_auth_blocks_second_username_on_same_device_same_day(client):
    assert _mobile_login(client, username="alice", device_id="device-2").status_code == 200

    response = _mobile_login(client, username="bob", device_id="device-2")
    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"] == "device_username_locked_for_today"


def test_mobile_auth_revokes_previous_session_for_same_user(client):
    first = _mobile_login(client, username="alice", device_id="device-3")
    assert first.status_code == 200
    first_token = first.get_json()["token"]

    second = _mobile_login(client, username="alice", device_id="device-4")
    assert second.status_code == 200
    second_token = second.get_json()["token"]

    response = client.get("/mobile/me", headers={"Authorization": f"Bearer {first_token}"})
    assert response.status_code == 401

    response = client.get("/mobile/me", headers={"Authorization": f"Bearer {second_token}"})
    assert response.status_code == 200


def test_mobile_auth_rejects_invalid_credentials(client, monkeypatch):
    import mobile_auth

    monkeypatch.setattr(
        mobile_auth,
        "student_radius_auth",
        lambda username, password, raise_on_error=False: False,
    )

    response = _mobile_login(client, username="alice", password="wrong")
    assert response.status_code == 401


def test_mobile_auth_attendance_history_returns_course_summary_for_current_semester(client, db, monkeypatch):
    import mobile_attendance

    current_semester = db.semester(
        name="Current 2026",
        start="2026-01-01",
        end="2026-12-31",
    )
    past_semester = db.semester(
        name="Past 2025",
        start="2025-01-01",
        end="2025-12-31",
    )
    room = db.room("R1")
    teacher = db.teacher("Prof", "alice")
    course = db.course("NumericalMethods")
    current_session = db.course_session(course, teacher, current_semester)
    past_session = db.course_session(course, teacher, past_semester)
    db.weekly_session(
        session_id=current_session,
        room_id=room,
        day_of_week=1,
        start_slot=10,
        end_slot=12,
    )
    db.execute(
        "INSERT INTO days (date, is_working, week_day) VALUES (?, ?, ?)",
        ("2026-03-16", 1, 1),
    )
    db.weekly_session(
        session_id=past_session,
        room_id=room,
        day_of_week=1,
        start_slot=12,
        end_slot=14,
    )
    db.execute(
        """
        INSERT INTO attendance_records
            (event_kind, event_id, event_date, username, registration_source, client_ip, failed_attempts_before_success)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("weekly", current_session, "2026-03-09", "alice", "android", "198.51.100.10", 1),
    )

    token = _mobile_login(client, username="alice", device_id="device-history").get_json()["token"]
    response = client.get(
        "/mobile/attendance/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_semester"]["name"] == "Current 2026"
    assert [item["course_name"] for item in payload["summaries"]] == ["NumericalMethods"]
    assert payload["summaries"][0]["attended_lessons"] == 1
    assert payload["summaries"][0]["total_lessons_with_recorded_attendance"] == 1
