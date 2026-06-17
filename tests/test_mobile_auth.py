def _mobile_login(client, username="alice", password="secret", device_id="device-1", device_name="Pixel"):
    return client.post(
        "/auth/login",
        json={
            "username": username,
            "password": password,
            "device_id": device_id,
            "device_name": device_name,
        },
    )


def test_mobile_auth_login_me_and_logout(client):
    response = _mobile_login(client)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["token"]
    assert payload["token_type"] == "Bearer"
    assert payload["user"]["radius_username"] == "alice"
    assert payload["session"]["device_id"] == "device-1"
    assert "attendance_locations" not in payload

    token = payload["token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["radius_username"] == "alice"
    assert payload["session"]["device_name"] == "Pixel"
    assert "last_seen_at" in payload["session"]
    assert "attendance_locations" not in payload

    response = client.get("/attendance/locations", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert "attendance_locations" in payload
    assert isinstance(payload["attendance_locations"], list)

    response = client.get("/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_session_id"]
    assert "attendance_locations" not in payload

    response = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
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

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {first_token}"})
    assert response.status_code == 401

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {second_token}"})
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


def test_mobile_attendance_locations_requires_session(client):
    response = client.get("/attendance/locations")
    assert response.status_code == 401
