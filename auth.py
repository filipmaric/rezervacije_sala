"""Authentication, CSRF, rate-limiting, and RADIUS helpers."""

import hmac
import secrets
import threading
import time
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, session
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
)
from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet

from config import (
    SERVICE_API_KEY,
    STUDENT_AUTH_BACKEND,
    STUDENT_RADIUS_DICTIONARY,
    STUDENT_RADIUS_SECRET,
    STUDENT_RADIUS_SERVER,
    TEACHER_AUTH_BACKEND,
    TEACHER_RADIUS_DICTIONARY,
    TEACHER_RADIUS_SECRET,
    TEACHER_RADIUS_SERVER,
)
from db import query_db


bp = Blueprint("auth", __name__)

CSRF_SESSION_KEY = "_csrf_token"

RATE_LIMITS = {
    "login": (10, 300),
    "reservation": (30, 60),
    "calendar": (10, 60),
    "attendance": (300, 60),
}

login_manager = LoginManager()
login_manager.login_view = None
login_manager.login_message = None

_rate_limit_lock = threading.Lock()
_rate_limit_state = {}

# CSRF helpers.


def init_app(app):
    """Register auth-related hooks on the Flask application."""
    login_manager.init_app(app)
    app.context_processor(inject_csrf_token)
    app.before_request(enforce_csrf)


def csrf_token():
    """Create or reuse the session CSRF token for browser requests."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def inject_csrf_token():
    """Expose the CSRF token helper to Jinja templates."""
    return {"csrf_token": csrf_token}


# Rate-limiting helpers.


def enforce_csrf():
    """Reject state-changing browser requests that do not send a CSRF token."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    if request.path.startswith("/auth/") or request.path.startswith("/mobile/"):
        return None

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return None

    sent = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
    )
    expected = session.get(CSRF_SESSION_KEY)
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        return jsonify({"error": "CSRF token missing or invalid"}), 400
    return None


def reset_rate_limits():
    """Clear the in-memory rate-limiting counters, mainly for tests."""
    with _rate_limit_lock:
        _rate_limit_state.clear()


def _rate_limit_bucket(scope, key=None):
    """Build the in-memory rate-limit key for the current client IP."""
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    client_ip = client_ip.split(",")[0].strip()
    suffix = f":{key}" if key else ""
    return f"{scope}:{client_ip}{suffix}"


def _is_bearer_service_request():
    """Return True when the request uses the bearer service API key."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth.split(" ", 1)[1]
    return token == current_app.config["SERVICE_API_KEY"]


def enforce_rate_limit(scope, limit, window_seconds, key=None):
    """Apply a fixed-window rate limit and return 429 when the client is over quota."""
    if _is_bearer_service_request():
        return None

    bucket = _rate_limit_bucket(scope, key)
    now = time.monotonic()

    with _rate_limit_lock:
        window_start, count = _rate_limit_state.get(bucket, (now, 0))
        if now - window_start >= window_seconds:
            window_start = now
            count = 0

        if count >= limit:
            retry_after = max(1, int(window_seconds - (now - window_start)))
            response = jsonify({"error": "Too many requests"})
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response

        _rate_limit_state[bucket] = (window_start, count + 1)

    return None


# Login and session helpers.


@login_manager.unauthorized_handler
def unauthorized():
    """Return the JSON response used when Flask-Login rejects a request."""
    return jsonify({"error": "Unauthorized"}), 401


def login_or_service_required(f):
    """Allow either a logged-in user or the bearer service key to call a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            if token == current_app.config["SERVICE_API_KEY"]:
                g.service_auth = True
                return f(*args, **kwargs)

        return jsonify({"error": "Unauthorized"}), 401

    return decorated


class User(UserMixin):
    """Flask-Login user object backed by a username string."""

    def __init__(self, username, role="teacher"):
        """Store the username and role for the authenticated user."""
        self.id = username
        self.username = username
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    """Recreate the Flask-Login user object from the stored session id."""
    return User(user_id)


# RADIUS authentication helpers.


def normalize_teacher_username(username):
    """Strip an email domain so teachers can log in with username or email."""
    return (username or "").strip().split("@", 1)[0]


def radius_auth_mock(username, password):
    """Local development fallback that accepts any non-empty username."""
    return bool(username)


def radius_auth(username, password):
    """Authenticate a teacher against the configured teacher RADIUS backend."""
    username = normalize_teacher_username(username)
    if TEACHER_AUTH_BACKEND != "radius":
        return radius_auth_mock(username, password)

    client = Client(
        server=TEACHER_RADIUS_SERVER,
        secret=TEACHER_RADIUS_SECRET,
        dict=Dictionary(TEACHER_RADIUS_DICTIONARY),
    )
    req = client.CreateAuthPacket(
        code=pyrad.packet.AccessRequest,
        User_Name=username,
    )
    req["User-Password"] = req.PwCrypt(password)

    try:
        reply = client.SendPacket(req)
    except Exception:
        return False

    return reply.code == pyrad.packet.AccessAccept


def student_radius_auth(username, password, raise_on_error=False):
    """Authenticate a student against the configured student RADIUS backend."""
    if STUDENT_AUTH_BACKEND != "radius":
        return radius_auth_mock(username, password)

    client = Client(
        server=STUDENT_RADIUS_SERVER,
        secret=STUDENT_RADIUS_SECRET,
        dict=Dictionary(STUDENT_RADIUS_DICTIONARY),
    )
    req = client.CreateAuthPacket(
        code=pyrad.packet.AccessRequest,
        User_Name=username,
    )
    req["User-Password"] = req.PwCrypt(password)

    try:
        reply = client.SendPacket(req)
    except Exception:
        if raise_on_error:
            raise
        return False

    return reply.code == pyrad.packet.AccessAccept


# Route helpers and endpoints.


def login_user_from_credentials(username, password):
    """Authenticate a teacher and create a browser session."""
    username = normalize_teacher_username(username)
    if radius_auth(username, password):
        user = User(username)
        login_user(user)
        return jsonify({"success": True, "username": user.username, "role": user.role}), 200
    return jsonify({"error": "Invalid credentials"}), 401


@bp.route("/login", methods=["POST"])
def login():
    """Authenticate a teacher and create a browser session."""
    limited = enforce_rate_limit("login", *RATE_LIMITS["login"])
    if limited is not None:
        return limited

    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    return login_user_from_credentials(username, password)


def logout_current_user():
    """Log out the current browser session or service caller."""
    logout_user()
    return jsonify({"success": True})


@bp.route("/logout", methods=["POST"])
@login_or_service_required
def logout():
    """Log out the current browser session or service caller."""
    return logout_current_user()


def whoami_payload():
    """Report whether the current request is authenticated."""
    if current_user.is_authenticated:
        return jsonify({"logged_in": True, "username": current_user.username})
    return jsonify({"logged_in": False})


@bp.route("/whoami", methods=["GET"])
def whoami():
    """Report whether the current request is authenticated."""
    return whoami_payload()


# Authorization helpers.


def check_if_admin(username):
    """Return True if the user is an administrator, False otherwise."""
    try:
        result = query_db(
            "SELECT 1 FROM administrators WHERE username = ?",
            (username,),
            one=True,
        )
        return bool(result)
    except Exception:
        return False
