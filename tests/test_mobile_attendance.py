import datetime

import app as myapp
import attendance as attendancemod
import config as cfg


def mobile_login(client, username="alice", password="secret", device_id="device-1", device_name="Pixel"):
    response = client.post(
        "/auth/login",
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

    current_code = payload["challenge"]["current_code"]
    success = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "join_token": join_token,
            "selected_code": current_code,
        },
    )
    assert success.status_code == 200
    assert success.get_json()["success"] is True

    roster = myapp.query_db(
        """
        SELECT username
        FROM attendance_records
        WHERE event_kind = ? AND event_id = ? AND event_date = ?
        """,
        ("weekly", weekly_session_id, event_date),
        one=True,
    )
    assert roster["username"] == "alice"


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
    current_code = challenge.get_json()["challenge"]["current_code"]
    wrong_code = 1000 + ((current_code - 1000 + 1) % 9000)

    first = client.post(
        f"/attendance/weekly/{weekly_session_id}/{event_date}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "join_token": join_token,
            "selected_code": wrong_code,
        },
    )
    assert first.status_code == 409

