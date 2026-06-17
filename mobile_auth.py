"""Android app authentication endpoints backed by student RADIUS sessions."""

import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from auth import RATE_LIMITS, enforce_rate_limit, student_radius_auth
from config import ATTENDANCE_ALLOWED_LOCATIONS
from db import (
    hash_token,
    mobile_auth_assert_device_login_allowed,
    mobile_auth_create_session,
    mobile_auth_get_or_create_user,
    mobile_auth_get_session_by_token,
    mobile_auth_get_user_by_id,
    mobile_auth_record_device_login,
    mobile_auth_revoke_active_sessions,
    mobile_auth_revoke_session,
    mobile_auth_touch_session,
)


bp = Blueprint("mobile_auth", __name__)


def _utcnow():
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _parse_iso(value):
    """Parse an ISO 8601 timestamp stored in SQLite."""
    return datetime.fromisoformat(value)


def _session_is_active(session):
    """Return True when the stored bearer session is still valid."""
    if session is None:
        return False
    if session["revoked_at"] is not None:
        return False
    return _parse_iso(session["expires_at"]) > _utcnow()


def _user_payload(user):
    """Serialize a mobile auth user row for the JSON API."""
    return {"id": user["id"], "radius_username": user["radius_username"]}


def _session_payload(session, include_last_seen=False):
    """Serialize a mobile auth session row for the JSON API."""
    payload = {
        "id": session["id"],
        "device_id": session["device_id"],
        "device_name": session["device_name"],
        "expires_at": session["expires_at"],
    }
    if include_last_seen:
        payload["last_seen_at"] = session["last_seen_at"]
    return payload


def _attendance_locations_payload():
    """Serialize the fixed Android QR geofences for the mobile client."""
    return ATTENDANCE_ALLOWED_LOCATIONS


def require_mobile_session(fn):
    """Require a valid Android bearer token for the wrapped route."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "missing_bearer_token"}), 401

        raw_token = auth_header.split(" ", 1)[1].strip()
        session = mobile_auth_get_session_by_token(raw_token)
        if not _session_is_active(session):
            return jsonify({"error": "invalid_or_expired_session"}), 401

        g.mobile_auth_session = mobile_auth_touch_session(session["id"])
        return fn(*args, **kwargs)

    return wrapper


@bp.route("/healthz", methods=["GET"])
def healthz():
    """Expose a lightweight health check for the Android backend."""
    return jsonify({"ok": True})


@bp.route("/auth/login", methods=["POST"])
def login():
    """Authenticate a student and create an opaque bearer session."""
    limited = enforce_rate_limit("login", *RATE_LIMITS["login"])
    if limited is not None:
        return limited

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    device_id = str(payload.get("device_id", "")).strip()
    device_name = str(payload.get("device_name", "Android phone")).strip()

    if not username or not password or not device_id:
        return jsonify({"error": "username_password_and_device_id_required"}), 400

    try:
        mobile_auth_assert_device_login_allowed(device_id, username)
    except RuntimeError as exc:
        return jsonify({"error": "device_username_locked_for_today", "detail": str(exc)}), 409

    try:
        ok = student_radius_auth(username, password, raise_on_error=True)
    except Exception as exc:
        return jsonify({"error": "radius_unavailable", "detail": str(exc)}), 503

    if not ok:
        return jsonify({"error": "invalid_credentials"}), 401

    user = mobile_auth_get_or_create_user(username)
    mobile_auth_revoke_active_sessions(user["id"], reason="replaced_by_new_login")

    raw_token = secrets.token_urlsafe(32)
    session = mobile_auth_create_session(
        user_id=user["id"],
        device_id=device_id,
        device_name=device_name,
        token_hash=hash_token(raw_token),
        session_days=current_app.config["MOBILE_AUTH_SESSION_DAYS"],
    )
    mobile_auth_record_device_login(device_id, username)

    return jsonify(
        {
            "token": raw_token,
            "token_type": "Bearer",
            "expires_at": session["expires_at"],
            "user": _user_payload(user),
            "session": _session_payload(session),
        }
    )


@bp.route("/auth/me", methods=["GET"])
@require_mobile_session
def me():
    """Return the current authenticated Android session."""
    session = g.mobile_auth_session
    user = mobile_auth_get_user_by_id(session["user_id"])
    return jsonify(
        {
            "user": _user_payload(user),
            "session": _session_payload(session, include_last_seen=True),
        }
    )


@bp.route("/auth/logout", methods=["POST"])
@require_mobile_session
def logout():
    """Revoke the current Android bearer token."""
    session = g.mobile_auth_session
    mobile_auth_revoke_session(session["id"], reason="logged_out")
    return jsonify({"ok": True})


@bp.route("/auth/sessions", methods=["GET"])
@require_mobile_session
def sessions():
    """Expose the current session id for clients that need to confirm login state."""
    session = g.mobile_auth_session
    user = mobile_auth_get_user_by_id(session["user_id"])
    return jsonify(
        {
            "user": _user_payload(user),
            "current_session_id": session["id"],
        }
    )


@bp.route("/attendance/locations", methods=["GET"])
@require_mobile_session
def attendance_locations():
    """Return the fixed geofences used by the Android QR scanner."""
    return jsonify({"attendance_locations": _attendance_locations_payload()})
