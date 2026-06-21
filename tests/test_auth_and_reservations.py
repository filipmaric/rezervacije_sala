import datetime
import time
import secrets

import app as myapp
import attendance as attendancemod
import auth as authmod
import config as cfg
import reservations as reservationsmod


def login(client, username="alice", password="secret"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
    )


def freeze_now(monkeypatch):
    fixed_now = datetime.datetime(2026, 6, 15, 10, 0, 0)
    monkeypatch.setattr(reservationsmod, "_current_datetime", lambda: fixed_now)
    return fixed_now


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


def test_login_accepts_teacher_email_username(client):
    r = login(client, "alice@example.edu")
    data = r.get_json()
    assert r.status_code == 200
    assert data["username"] == "alice"

    r = client.get("/whoami")
    assert r.get_json() == {"logged_in": True, "username": "alice"}


def test_login_failure(client, monkeypatch):
    monkeypatch.setattr(authmod, "radius_auth", lambda username, password: False)

    r = login(client, "alice")
    assert r.status_code == 401
    assert r.get_json() == {"error": "Invalid credentials"}


def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(authmod, "radius_auth", lambda username, password: True)
    monkeypatch.setitem(myapp.RATE_LIMITS, "login", (2, 60))

    assert login(client, "alice").status_code == 200
    assert login(client, "alice").status_code == 200

    r = login(client, "alice")
    assert r.status_code == 429
    assert r.get_json() == {"error": "Too many requests"}


def test_csrf_is_required_for_browser_posts(app):
    raw_client = app.test_client()
    csrf_token = secrets.token_urlsafe(32)
    with raw_client.session_transaction() as sess:
        sess["_csrf_token"] = csrf_token

    missing = raw_client.post("/login", json={"username": "alice", "password": "secret"})
    assert missing.status_code == 400
    assert missing.get_json()["error"] == "CSRF token missing or invalid"

    ok = raw_client.post(
        "/login",
        json={"username": "alice", "password": "secret"},
        headers={"X-CSRFToken": csrf_token},
    )
    assert ok.status_code == 200


def test_service_token_routes_are_exempt_from_csrf(app):
    raw_client = app.test_client()
    r = raw_client.post(
        "/update_calendar",
        json=[{"date": "2026-03-10", "is_working": 1, "week_day": 1}],
        headers={"Authorization": f"Bearer {myapp.app.config['SERVICE_API_KEY']}"},
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_is_admin(client, db):
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("alice",))

    r = client.get("/is_admin/alice")
    assert r.get_json() == {"username": "alice", "is_admin": True}

    r = client.get("/is_admin/bob")
    assert r.get_json() == {"username": "bob", "is_admin": False}


def test_my_reservations_data_groups_personal_and_courses(client, db):
    login(client, "alice")

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
    other_teacher = db.teacher("Other Prof", "other.prof")
    course = db.course("NumericalMethods")
    other_course = db.course("Databases")

    db.reservation(
        room_id=room,
        date="2026-03-09",
        start=8,
        end=10,
        description="current personal",
        username="alice",
    )
    db.reservation(
        room_id=room,
        date="2025-03-10",
        start=8,
        end=10,
        description="past personal",
        username="alice",
    )

    current_session = db.course_session(course, teacher, current_semester)
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
    db.execute(
        "INSERT INTO days (date, is_working, week_day) VALUES (?, ?, ?)",
        ("2026-03-23", 1, 1),
    )
    db.execute(
        """INSERT INTO weekly_cancellations
           (weekly_session_id, date, username)
           VALUES (?, ?, ?)""",
        (current_session, "2026-03-16", "alice"),
    )

    other_session = db.course_session(other_course, other_teacher, current_semester)
    db.weekly_session(
        session_id=other_session,
        room_id=room,
        day_of_week=2,
        start_slot=12,
        end_slot=14,
    )

    past_session = db.course_session(course, teacher, past_semester)
    db.weekly_session(
        session_id=past_session,
        room_id=room,
        day_of_week=1,
        start_slot=12,
        end_slot=14,
    )
    db.execute(
        "INSERT INTO days (date, is_working, week_day) VALUES (?, ?, ?)",
        ("2025-03-10", 1, 1),
    )

    r = client.get("/my_reservations_data")
    assert r.status_code == 200
    data = r.get_json()

    assert data["selected_semester"]["name"] == "Current 2026"
    assert [item["description"] for item in data["personal_reservations"]] == ["current personal"]
    assert [course_item["course_name"] for course_item in data["courses"]] == ["NumericalMethods"]
    assert [session["start_slot"] for session in data["courses"][0]["sessions"]] == [10]
    assert data["courses"][0]["sessions"][0]["instances"] == ["2026-03-09", "2026-03-23"]

    r = client.get(f"/my_reservations_data?semester_id={past_semester}")
    assert r.status_code == 200
    past_data = r.get_json()

    assert past_data["selected_semester"]["name"] == "Past 2025"
    assert [item["description"] for item in past_data["personal_reservations"]] == ["past personal"]
    assert [session["start_slot"] for session in past_data["courses"][0]["sessions"]] == [12]
    assert past_data["courses"][0]["sessions"][0]["instances"] == ["2025-03-10"]


def test_attendance_join_and_roster(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    login(client, "alice")

    semester = db.semester(
        name="Current 2026",
        start="2026-01-01",
        end="2026-12-31",
    )
    db.building_location("A", 10.0, 20.0, radius_m=100)
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
    db.student("student1", "1140/2025", "Agildere", "Fritz Ali")

    roster = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/data")
    assert roster.status_code == 200
    roster_data = roster.get_json()
    token = roster_data["join_token"]

    scanned = client.get(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join/{token}",
        follow_redirects=True,
    )
    assert scanned.status_code == 200

    challenge = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/challenge")
    assert challenge.status_code == 200
    challenge_data = challenge.get_json()
    assert challenge_data["event"]["course_name"] == "NumericalMethods"
    assert isinstance(challenge_data["challenge"]["current_code"], int)
    assert len(challenge_data["challenge"]["options"]) == 4
    assert challenge_data["attendance_geofence_available"] is True
    assert challenge_data["attendance_geofence_enabled"] is True

    bucket = int(time.time() // cfg.ATTENDANCE_CHALLENGE_TTL)
    code = myapp.attendance_code_for_bucket("weekly", weekly_session_id, "2026-03-09", bucket)

    join = client.post(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join",
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
        json={
            "username": "student1",
            "password": "secret",
            "selected_code": code,
        },
    )
    assert join.status_code == 200
    assert join.get_json()["username"] == "student1"

    roster_after = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/data")
    assert roster_after.status_code == 200
    roster_after_data = roster_after.get_json()
    assert roster_after_data["event"]["course_name"] == "NumericalMethods"
    assert roster_after_data["attendance_geofence_available"] is True
    assert roster_after_data["attendance_geofence_enabled"] is True
    assert [student["username"] for student in roster_after_data["students"]] == ["student1"]
    assert roster_after_data["students"][0]["student_index"] == "1140/2025"
    assert roster_after_data["students"][0]["student_name"] == "Fritz Ali Agildere"
    assert roster_after_data["students"][0]["student_label"] == "Fritz Ali Agildere (1140/2025)"

    stored = myapp.query_db(
        """
        SELECT registration_source, client_ip
        FROM attendance_records
        WHERE event_kind = ? AND event_id = ? AND event_date = ? AND username = ?
        """,
        ("weekly", weekly_session_id, "2026-03-09", "student1"),
        one=True,
    )
    assert stored["registration_source"] == "web"
    assert stored["client_ip"] == "192.0.2.10"


def test_attendance_spot_check_shortlist_and_flags_unconfirmed(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
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
    db.student("android_one", "1000/2025", "Android", "One")
    db.student("web_one", "1001/2025", "Web", "One")
    db.student("web_two", "1002/2025", "Web", "Two")
    db.execute(
        """
        INSERT INTO attendance_records
            (event_kind, event_id, event_date, username, registration_source, failed_attempts_before_success)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("weekly", weekly_session_id, "2026-03-09", "android_one", "android", 0),
    )
    db.execute(
        """
        INSERT INTO attendance_records
            (event_kind, event_id, event_date, username, registration_source, failed_attempts_before_success)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("weekly", weekly_session_id, "2026-03-09", "web_one", "web", 0),
    )
    db.execute(
        """
        INSERT INTO attendance_records
            (event_kind, event_id, event_date, username, registration_source, failed_attempts_before_success)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("weekly", weekly_session_id, "2026-03-09", "web_two", "web", 2),
    )

    shortlist = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/spot_check?limit=2")
    assert shortlist.status_code == 200
    shortlist_data = shortlist.get_json()
    assert [student["username"] for student in shortlist_data["students"]] == ["web_two", "web_one"]
    assert "registration_source" not in shortlist_data["students"][0]

    submit = client.post(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/spot_check",
        json={
            "selected_usernames": ["web_two", "web_one"],
            "confirmed_usernames": ["web_two"],
        },
    )
    assert submit.status_code == 200
    assert submit.get_json() == {"success": True, "recorded": 1}

    flagged = myapp.query_db(
        """
        SELECT ar.username, f.teacher_username, f.flagged_at
        FROM attendance_records ar
        JOIN attendance_spot_check_flags f ON f.attendance_record_id = ar.id
        WHERE ar.event_kind = ? AND ar.event_id = ? AND ar.event_date = ?
        """,
        ("weekly", weekly_session_id, "2026-03-09"),
    )
    assert len(flagged) == 1
    assert flagged[0]["username"] == "web_one"
    assert flagged[0]["teacher_username"] == "alice"
    assert flagged[0]["flagged_at"] is not None


def test_attendance_roster_uses_unknown_fallback(client, db):
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

    db.execute(
        """
        INSERT INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        ("weekly", weekly_session_id, "2026-03-09", "ghost_student"),
    )

    roster = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/data")
    assert roster.status_code == 200
    student = roster.get_json()["students"][0]
    assert student["student_name"] is None
    assert student["student_index"] is None
    assert student["student_label"] == "Непознато"


def test_attendance_blocked_outside_class_window(client, db, monkeypatch):
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

    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: False)

    roster = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/data")
    assert roster.status_code == 200
    assert roster.get_json()["attendance_open"] is False

    challenge = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/challenge")
    assert challenge.status_code == 403
    assert challenge.get_json()["error"] == "Пријава присуства је могућа само током часа."

    join = client.post(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join",
        json={
            "username": "student1",
            "password": "secret",
            "selected_code": 1234,
        },
    )
    assert join.status_code == 403
    assert join.get_json()["error"] == "Пријава присуства је могућа само током часа."


def test_attendance_join_token_rotates_every_8_seconds(db):
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

    t1 = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        now=myapp.datetime.datetime(1970, 1, 1, 0, 0, 0),
    )
    t2 = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        now=myapp.datetime.datetime(1970, 1, 1, 0, 0, 10),
    )

    assert t1 != t2
    assert myapp.attendance_token_is_valid(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        t1,
        now=myapp.datetime.datetime(1970, 1, 1, 0, 0, 15),
    )
    assert not myapp.attendance_token_is_valid(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        t1,
        now=myapp.datetime.datetime(1970, 1, 1, 0, 0, 16),
    )


def test_attendance_join_blocks_after_two_wrong_numbers(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
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
    token = roster.get_json()["join_token"]
    client.get(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join/{token}",
        follow_redirects=True,
    )

    current_code = myapp.attendance_challenge_for_time(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        now=myapp.datetime.datetime.now(),
    )["current_code"]
    wrong_code = 1000 + ((current_code - 1000 + 1) % 9000)

    first = client.post(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join",
        json={
            "username": "student1",
            "password": "secret",
            "selected_code": wrong_code,
        },
    )
    assert first.status_code == 409
    assert first.get_json()["error"] == "Погрешан број. Сачекајте нови круг."

    second = client.post(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/join",
        json={
            "username": "student1",
            "password": "secret",
            "selected_code": wrong_code,
        },
    )
    assert second.status_code == 403
    assert "Превише погрешних покушаја" in second.get_json()["error"]

    challenge = client.get(f"/attendance/weekly/{weekly_session_id}/2026-03-09/challenge")
    assert challenge.status_code == 403
    assert "Превише погрешних покушаја" in challenge.get_json()["error"]


def test_attendance_attempt_expired_is_reported_separately(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    schedule = db.schedule()
    room = schedule.room("R1")
    teacher = schedule.teacher("Prof", "alice")
    course = schedule.course("NumericalMethods")
    session = db.course_session(course, teacher, schedule.semester_id)
    weekly_session_id = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=10,
        end_slot=12,
    )

    expired_now = myapp.datetime.datetime.now() - myapp.datetime.timedelta(
        seconds=cfg.ATTENDANCE_ATTEMPT_TTL + 1
    )
    cookie_name = myapp.attendance_attempt_cookie_name(
        "weekly",
        weekly_session_id,
        "2026-03-09",
    )
    expired_token = myapp.attendance_create_attempt_token(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        now=expired_now,
    )

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/challenge",
        headers={"Cookie": f"{cookie_name}={expired_token}"},
    )
    assert challenge.status_code == 403
    assert challenge.get_json()["error"] == "Покушај је истекао. Поново скенирајте QR код."


def test_expired_attendance_attempt_clears_failure_rows(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    schedule = db.schedule()
    room = schedule.room("R1")
    teacher = schedule.teacher("Prof", "alice")
    course = schedule.course("NumericalMethods")
    session = db.course_session(course, teacher, schedule.semester_id)
    weekly_session_id = db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=10,
        end_slot=12,
    )

    expired_now = myapp.datetime.datetime.now() - myapp.datetime.timedelta(
        seconds=cfg.ATTENDANCE_ATTEMPT_TTL + 1
    )
    cookie_name = myapp.attendance_attempt_cookie_name(
        "weekly",
        weekly_session_id,
        "2026-03-09",
    )
    expired_token = myapp.attendance_create_attempt_token(
        "weekly",
        weekly_session_id,
        "2026-03-09",
        now=expired_now,
    )

    myapp.execute_db(
        """
        INSERT INTO attendance_attempt_failures
            (attempt_token, failed_attempts, blocked)
        VALUES (?, 1, 0)
        """,
        (expired_token,),
    )
    assert myapp.query_db(
        """
        SELECT attempt_token
        FROM attendance_attempt_failures
        WHERE attempt_token = ?
        """,
        (expired_token,),
        one=True,
    ) is not None

    client.set_cookie(cookie_name, expired_token)

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/2026-03-09/challenge",
    )
    assert challenge.status_code == 403
    assert challenge.get_json()["error"] == "Покушај је истекао. Поново скенирајте QR код."
    assert myapp.query_db(
        """
        SELECT attempt_token
        FROM attendance_attempt_failures
        WHERE attempt_token = ?
        """,
        (expired_token,),
        one=True,
    ) is None


def test_reserve_success_and_conflicts(client, db, monkeypatch):
    room = db.room("R1")
    future_date = "2026-07-09"
    freeze_now(monkeypatch)

    login(client, "alice")

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": future_date,
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
            "date": future_date,
            "start_slot": 9,
            "end_slot": 11,
            "description": "overlap",
        },
    )
    assert r.status_code == 409

    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
    semester = db.semester(name="Spring 2026")
    session = db.course_session(course, teacher, semester)
    db.weekly_session(
        session_id=session,
        room_id=room,
        day_of_week=1,
        start_slot=12,
        end_slot=14,
    )
    monkeypatch.setattr(reservationsmod, "check_day", lambda date: (True, 0, 1))

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": future_date,
            "start_slot": 12,
            "end_slot": 13,
            "description": "weekly conflict",
        },
    )
    assert r.status_code == 409

    r = client.delete(f"/reservation/{first_id}")
    assert r.status_code == 200


def test_delete_reservation_current_allowed_until_end_when_no_attendance(client, db, monkeypatch):
    room = db.room("R1")
    res_id = db.reservation(
        room_id=room,
        date="2026-06-15",
        start=8,
        end=12,
        description="current meeting",
        username="owner",
    )

    freeze_now(monkeypatch)
    login(client, "owner")
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_delete_reservation_current_blocked_when_attendance_exists(client, db, monkeypatch):
    room = db.room("R1")
    res_id = db.reservation(
        room_id=room,
        date="2026-06-15",
        start=8,
        end=12,
        description="current meeting",
        username="owner",
    )
    db.execute(
        """
        INSERT INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        ("reservation", res_id, "2026-06-15", "student1"),
    )

    freeze_now(monkeypatch)
    login(client, "owner")
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 409
    assert r.get_json() == {
        "error": "Reservations can be canceled only on the current or future dates and only if attendance has not been recorded."
    }


def test_delete_reservation_past_blocked(client, db, monkeypatch):
    room = db.room("R1")
    res_id = db.reservation(
        room_id=room,
        date="2026-06-14",
        start=8,
        end=10,
        description="past meeting",
        username="owner",
    )

    freeze_now(monkeypatch)
    login(client, "owner")
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 409
    assert r.get_json() == {
        "error": "Reservations can be canceled only on the current or future dates and only if attendance has not been recorded."
    }


def test_reserve_validation_errors(client, db):
    db.room("R1")
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
    db.room("R1")
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


def test_delete_reservation_permissions(client, db, monkeypatch):
    room = db.room("R1")
    res_id = db.reservation(
        room_id=room,
        date="2026-07-09",
        start=8,
        end=10,
        description="meeting",
        username="owner",
    )

    freeze_now(monkeypatch)
    login(client, "other")
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 403

    client.post("/logout")
    login(client, "admin")
    db.execute("INSERT INTO administrators (username) VALUES (?)", ("admin",))
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_delete_reservation_unauthorized(client, db, monkeypatch):
    room = db.room("R1")
    res_id = db.reservation(
        room_id=room,
        date="2026-07-09",
        start=8,
        end=10,
        description="meeting",
        username="owner",
    )

    freeze_now(monkeypatch)
    r = client.delete(f"/reservation/{res_id}")
    assert r.status_code == 401
    assert r.get_json() == {"error": "Unauthorized"}


def test_bulk_reservations_atomic(client, db):
    room1 = db.room("R1")
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
    assert r.get_json() == {"error": "bulk reservation failed"}

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
    myapp.RATE_LIMITS["calendar"] = (1, 60)
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

    r = client.post(
        "/update_calendar",
        headers={"Authorization": f"Bearer {myapp.app.config['SERVICE_API_KEY']}"},
        json=[
            {
                "date": "2026-03-13",
                "is_working": True,
                "week_day": 4,
            }
        ],
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True}


def test_service_token_bulk_reservations(client, db):
    room1 = db.room("R1")
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


def test_reservation_rate_limit(client, db, monkeypatch):
    room = db.room("R1")
    login(client, "alice")
    monkeypatch.setitem(myapp.RATE_LIMITS, "reservation", (1, 60))

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-14",
            "start_slot": 8,
            "end_slot": 10,
            "description": "first",
        },
    )
    assert r.status_code == 201

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": "2026-03-14",
            "start_slot": 10,
            "end_slot": 12,
            "description": "second",
        },
    )
    assert r.status_code == 429
    assert r.get_json() == {"error": "Too many requests"}


def test_weekly_session_cancel_and_conflict(client, db, monkeypatch):
    room = db.room("R1")
    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
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
    freeze_now(monkeypatch)
    future_date = "2026-06-22"
    monkeypatch.setattr(reservationsmod, "check_day", lambda date: (True, 0, 1))

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": future_date},
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "canceled": True}

    r = client.post(
        "/reserve",
        json={
            "room_id": room,
            "date": future_date,
            "start_slot": 2,
            "end_slot": 4,
            "description": "occupy slot",
        },
    )
    assert r.status_code == 201

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": future_date},
    )
    assert r.status_code == 409


def test_weekly_session_cancel_current_allowed_until_end_when_no_attendance(client, db, monkeypatch):
    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
    semester = db.semester()
    session = db.course_session(course, teacher, semester)
    weekly_id = db.weekly_session(
        session_id=session,
        room_id=db.room("R1"),
        day_of_week=1,
        start_slot=2,
        end_slot=12,
    )

    login(client, "prof")
    freeze_now(monkeypatch)
    monkeypatch.setattr(reservationsmod, "check_day", lambda date: (True, 0, 1))

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": "2026-06-15"},
    )
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "canceled": True}


def test_weekly_session_cancel_current_blocked_when_attendance_exists(client, db, monkeypatch):
    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
    semester = db.semester()
    session = db.course_session(course, teacher, semester)
    weekly_id = db.weekly_session(
        session_id=session,
        room_id=db.room("R1"),
        day_of_week=1,
        start_slot=2,
        end_slot=12,
    )
    db.execute(
        """
        INSERT INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        ("weekly", weekly_id, "2026-06-15", "student1"),
    )

    login(client, "prof")
    freeze_now(monkeypatch)
    monkeypatch.setattr(reservationsmod, "check_day", lambda date: (True, 0, 1))

    r = client.post(
        "/weekly_session_cancel",
        json={"weekly_session_id": weekly_id, "date": "2026-06-15"},
    )
    assert r.status_code == 409
    assert r.get_json() == {
        "error": "Weekly classes can be canceled only before the end time and only if attendance has not been recorded."
    }


def test_weekly_session_cancel_validation(client, db):
    teacher = db.teacher("Prof", "prof")
    course = db.course("NumericalMethods")
    semester = db.semester()
    session = db.course_session(course, teacher, semester)
    weekly_id = db.weekly_session(
        session_id=session,
        room_id=db.room("R1"),
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
