import datetime

import app as myapp
import attendance as attendancemod
import config as cfg


def login(client, username="alice", password="secret"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
    )


def mobile_login(client, username="alice", password="secret", device_id="device-1", device_name="Pixel"):
    response = client.post(
        "/mobile/login",
        json={
            "username": username,
            "password": password,
            "device_id": device_id,
            "device_name": device_name,
        },
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def freeze_attendance_now(monkeypatch, fixed_now):
    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.replace(tzinfo=tz)

    monkeypatch.setattr(attendancemod.datetime, "datetime", FrozenDatetime)
    return fixed_now


def make_weekly_event(db):
    semester = db.semester(name="Current 2026", start="2026-01-01", end="2026-12-31")
    db.building_location("A", 10.0, 20.0, radius_m=100)
    room = db.room("R1", building_name="A")
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
    return weekly_session_id


def test_mobile_attendance_challenge_and_submit(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    fixed_now = freeze_attendance_now(monkeypatch, datetime.datetime(2026, 6, 16, 10, 0, 0))
    weekly_session_id = make_weekly_event(db)
    event_date = "2026-03-09"
    join_token = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        event_date,
        now=fixed_now,
    )
    token = mobile_login(client)

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?join_token={join_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert challenge.status_code == 200
    payload = challenge.get_json()
    assert "challenge" in payload
    assert payload["challenge"]["options"]
    assert payload["attendance_attempt_token"]

    current_code = payload["challenge"]["current_code"]
    success = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        environ_base={"REMOTE_ADDR": "192.0.2.20"},
        json={
            "attendance_attempt_token": payload["attendance_attempt_token"],
            "selected_code": current_code,
            "latitude": 10.0,
            "longitude": 20.0,
        },
    )
    assert success.status_code == 200
    assert success.get_json()["success"] is True

    roster = myapp.query_db(
        """
        SELECT username, registration_source, client_ip, client_latitude, client_longitude, geofence_checked
        FROM attendance_records
        WHERE event_kind = ? AND event_id = ? AND event_date = ?
        """,
        ("weekly", weekly_session_id, event_date),
        one=True,
    )
    assert roster["username"] == "alice"
    assert roster["registration_source"] == "android"
    assert roster["client_ip"] == "192.0.2.20"
    assert roster["client_latitude"] == 10.0
    assert roster["client_longitude"] == 20.0
    assert int(roster["geofence_checked"]) == 1


def test_mobile_attendance_blocks_wrong_number_without_username_password(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    fixed_now = freeze_attendance_now(monkeypatch, datetime.datetime(2026, 6, 16, 10, 0, 0))
    weekly_session_id = make_weekly_event(db)
    event_date = "2026-03-09"
    join_token = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        event_date,
        now=fixed_now,
    )
    token = mobile_login(client)

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?join_token={join_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    challenge_payload = challenge.get_json()
    current_code = challenge_payload["challenge"]["current_code"]
    attendance_attempt_token = challenge_payload["attendance_attempt_token"]
    wrong_code = 1000 + ((current_code - 1000 + 1) % 9000)

    first = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "attendance_attempt_token": attendance_attempt_token,
            "selected_code": wrong_code,
            "latitude": 10.0,
            "longitude": 20.0,
        },
    )
    assert first.status_code == 409


def test_mobile_attendance_requires_allowed_location(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    fixed_now = freeze_attendance_now(monkeypatch, datetime.datetime(2026, 6, 16, 10, 0, 0))
    weekly_session_id = make_weekly_event(db)
    event_date = "2026-03-09"
    join_token = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        event_date,
        now=fixed_now,
    )
    token = mobile_login(client)

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?join_token={join_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    challenge_payload = challenge.get_json()
    current_code = challenge_payload["challenge"]["current_code"]
    attendance_attempt_token = challenge_payload["attendance_attempt_token"]
    assert challenge_payload["attendance_locations"][0]["name"] == "A"

    denied = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "attendance_attempt_token": attendance_attempt_token,
            "selected_code": current_code,
            "latitude": 44.0,
            "longitude": 21.0,
        },
    )
    assert denied.status_code == 403
    payload = denied.get_json()
    assert payload["error_code"] == "attendance_geofence_blocked"
    assert "закључан" in payload["error"]
    assert "Поново скенирајте QR код" in payload["error"]
    assert payload["current_location"] == {"latitude": 44.0, "longitude": 21.0}
    assert payload["closest_location"]["name"] == "A"
    assert payload["closest_location"]["distance_m"] > 0

    blocked = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?attendance_attempt_token={attendance_attempt_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 403
    assert blocked.get_json()["error_code"] == "attendance_attempt_blocked"
    assert "Превише погрешних покушаја" in blocked.get_json()["error"]

    allowed = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "attendance_attempt_token": attendance_attempt_token,
            "selected_code": current_code,
            "latitude": 10.0,
            "longitude": 20.0,
        },
    )
    assert allowed.status_code == 403


def test_mobile_attendance_persists_location_when_geofence_is_disabled(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    fixed_now = freeze_attendance_now(monkeypatch, datetime.datetime(2026, 6, 16, 10, 0, 0))
    weekly_session_id = make_weekly_event(db)
    event_date = "2026-03-09"
    join_token = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        event_date,
        now=fixed_now,
    )

    login(client, "alice")
    toggle = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/geofence",
        json={"enabled": False},
    )
    assert toggle.status_code == 200
    assert toggle.get_json()["attendance_geofence_enabled"] is False

    token = mobile_login(client)
    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?join_token={join_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    challenge_payload = challenge.get_json()
    assert challenge_payload["attendance_geofence_enabled"] is False
    assert challenge_payload["attendance_geofence_available"] is True
    attendance_attempt_token = challenge_payload["attendance_attempt_token"]

    current_code = challenge_payload["challenge"]["current_code"]
    success = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        environ_base={"REMOTE_ADDR": "192.0.2.21"},
        json={
            "attendance_attempt_token": attendance_attempt_token,
            "selected_code": current_code,
            "latitude": 44.0,
            "longitude": 21.0,
        },
    )
    assert success.status_code == 200

    stored = myapp.query_db(
        """
        SELECT client_latitude, client_longitude, geofence_checked
        FROM attendance_records
        WHERE event_kind = ? AND event_id = ? AND event_date = ?
        """,
        ("weekly", weekly_session_id, event_date),
        one=True,
    )
    assert stored["client_latitude"] == 44.0
    assert stored["client_longitude"] == 21.0
    assert int(stored["geofence_checked"]) == 0


def test_mobile_attendance_attempt_token_survives_qr_token_rotation(client, db, monkeypatch):
    monkeypatch.setattr(attendancemod, "attendance_is_open_now", lambda row, now=None: True)
    base_now = datetime.datetime(2026, 6, 16, 10, 0, 0)
    freeze_attendance_now(monkeypatch, base_now)
    weekly_session_id = make_weekly_event(db)
    event_date = "2026-03-09"
    join_token = myapp.attendance_join_token(
        "weekly",
        weekly_session_id,
        event_date,
        now=base_now,
    )
    token = mobile_login(client)

    challenge = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?join_token={join_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert challenge.status_code == 200
    challenge_payload = challenge.get_json()
    attendance_attempt_token = challenge_payload["attendance_attempt_token"]
    current_code = challenge_payload["challenge"]["current_code"]

    later_now = base_now + datetime.timedelta(seconds=20)
    freeze_attendance_now(monkeypatch, later_now)

    refreshed = client.get(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/challenge?attendance_attempt_token={attendance_attempt_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert refreshed.status_code == 200
    refreshed_payload = refreshed.get_json()
    assert refreshed_payload["attendance_attempt_token"] == attendance_attempt_token

    success = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        environ_base={"REMOTE_ADDR": "192.0.2.22"},
        json={
            "attendance_attempt_token": attendance_attempt_token,
            "selected_code": refreshed_payload["challenge"]["current_code"],
            "latitude": 10.0,
            "longitude": 20.0,
        },
    )
    assert success.status_code == 200
