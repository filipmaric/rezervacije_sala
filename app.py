"""
Flask backend for Classroom Reservation System.
- Uses sqlite3 (builtin) with safe, parametrized queries.
- Provides endpoints:

Authentication:
POST   /login                         -> login via RADIUS (JSON: username, password)
POST   /logout                        -> logout current user
GET    /whoami                        -> info about current session
GET    /is_admin/<username>           -> check if user is administrator

Rooms and occupancy:
GET    /rooms                         -> list all rooms
GET    /occupancy?date=YYYY-MM-DD     -> merged weekly classes + reservations per room for given date

Reservations:
POST   /reserve                       -> create reservation (login required)
POST   /reserve/bulk                  -> create multiple reservations atomically
DELETE /reservation/<res_id>          -> cancel reservation (owner or admin)
POST   /weekly_session_cancel        -> cancel or restore a weekly lecture occurrence

Calendar:
GET    /calendar_data?month=MM&year=YYYY -> calendar metadata for month
POST   /update_calendar               -> update working days / overrides
GET    /calendar                      -> calendar HTML view
GET    /my_reservations               -> personal reservations page
GET    /my_reservations_data          -> JSON for personal/course reservations

Attendance:
GET    /attendance/<kind>/<id>/<date>               -> teacher attendance page
GET    /attendance/<kind>/<id>/<date>/join          -> student attendance page after QR scan
GET    /attendance/<kind>/<id>/<date>/join/<token>  -> short-lived QR entry point
GET    /attendance/<kind>/<id>/<date>/challenge     -> student challenge JSON
GET    /attendance/<kind>/<id>/<date>/data          -> teacher current attendance list JSON
POST   /attendance/<kind>/<id>/<date>/join          -> student check-in submit

Frontend:
GET    /                              -> index HTML view

Security notes are marked in comments (use HTTPS in production, strong password hashing, rate limiting, input validation).
"""

import os
import sqlite3
import datetime
import argparse
import threading
import time
import hmac
import hashlib
import random
import secrets
from flask import Flask, g, request, jsonify, abort, render_template, session, redirect, make_response, url_for
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from functools import wraps
from werkzeug.exceptions import HTTPException

from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import logging
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "app.db")
SCHEMA_FILE = os.path.join(BASE_DIR, "schema.sql")
APP_ENV = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}


def load_secret_env(name, dev_default):
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"{name} must be set when APP_ENV=production")
    return dev_default


def load_env(name, dev_default):
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"{name} must be set when APP_ENV=production")
    return dev_default


# Default to a root-mounted app for local development.
# In production, set APPLICATION_ROOT=/rezervacije and STATIC_URL_PATH=/rezervacije/static.
STATIC_URL_PATH = os.getenv("STATIC_URL_PATH", "/static")
APPLICATION_ROOT = os.getenv("APPLICATION_ROOT", "/")
LOG_FILE = os.getenv("APP_LOG_FILE", os.path.join(BASE_DIR, "app.log"))
ATTENDANCE_SECRET = load_secret_env("ATTENDANCE_SECRET", "attendance-secret")
ATTENDANCE_JOIN_TOKEN_TTL = int(os.getenv("ATTENDANCE_JOIN_TOKEN_TTL", "8"))
ATTENDANCE_CHALLENGE_TTL = int(os.getenv("ATTENDANCE_CHALLENGE_TTL", "10"))
ATTENDANCE_SESSION_TTL = int(os.getenv("ATTENDANCE_SESSION_TTL", "90"))
TEACHER_AUTH_BACKEND = os.getenv("TEACHER_AUTH_BACKEND", "mock").lower()
TEACHER_RADIUS_SERVER = load_env("TEACHER_RADIUS_SERVER", "147.91.66.2")
TEACHER_RADIUS_SECRET = load_secret_env("TEACHER_RADIUS_SECRET", "raspored2mainWebsite").encode()
TEACHER_RADIUS_DICTIONARY = load_env("TEACHER_RADIUS_DICTIONARY", "/var/www/rezervacije/radius/dictionary")
STUDENT_AUTH_BACKEND = os.getenv("STUDENT_AUTH_BACKEND", "mock").lower()
STUDENT_RADIUS_SERVER = load_env("STUDENT_RADIUS_SERVER", "147.91.66.2")
STUDENT_RADIUS_SECRET = load_secret_env("STUDENT_RADIUS_SECRET", "raspored2mainWebsite").encode()
STUDENT_RADIUS_DICTIONARY = load_env("STUDENT_RADIUS_DICTIONARY", "/var/www/rezervacije/radius/dictionary")

app = Flask(__name__, static_url_path=STATIC_URL_PATH)
app.config['APPLICATION_ROOT'] = APPLICATION_ROOT
app.config['SERVICE_API_KEY'] = load_secret_env(
    "SERVICE_API_KEY",
    "dev-service-api-key",
)
app.secret_key = load_secret_env("SECRET_KEY", "classroommatfreservations")  # required for sessions
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Local development defaults to the mock teacher authenticator.
# Set TEACHER_AUTH_BACKEND=radius to use the real teacher RADIUS server.
if IS_PRODUCTION:
    if TEACHER_AUTH_BACKEND != "radius":
        raise RuntimeError("TEACHER_AUTH_BACKEND must be radius when APP_ENV=production")
    if STUDENT_AUTH_BACKEND != "radius":
        raise RuntimeError("STUDENT_AUTH_BACKEND must be radius when APP_ENV=production")
    for name in ("TEACHER_RADIUS_SERVER", "TEACHER_RADIUS_SECRET", "TEACHER_RADIUS_DICTIONARY", "STUDENT_RADIUS_SERVER", "STUDENT_RADIUS_SECRET", "STUDENT_RADIUS_DICTIONARY"):
        if not os.getenv(name):
            raise RuntimeError(f"{name} must be set when APP_ENV=production")

CSRF_SESSION_KEY = "_csrf_token"

RATE_LIMITS = {
    "login": (10, 300),
    "reservation": (30, 60),
    "calendar": (10, 60),
    "attendance": (300, 60),
}

try:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1000000, backupCount=3)
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
except OSError:
    # Keep the app usable when the log path is not writable locally.
    pass

_rate_limit_lock = threading.Lock()
_rate_limit_state = {}


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


@app.before_request
def enforce_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
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
    with _rate_limit_lock:
        _rate_limit_state.clear()


def _rate_limit_bucket(scope, key=None):
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    client_ip = client_ip.split(",")[0].strip()
    suffix = f":{key}" if key else ""
    return f"{scope}:{client_ip}{suffix}"


def _is_bearer_service_request():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth.split(" ", 1)[1]
    return token == app.config["SERVICE_API_KEY"]


def enforce_rate_limit(scope, limit, window_seconds, key=None):
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

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = None  # no redirect
login_manager.login_message = None

# JSON response for unauthorized requests
@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({'error':'Unauthorized'}), 401

# --- DB helpers -------------------------------------------------


def _database_has_schema(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
    ).fetchone()
    return bool(row)


def _ensure_attendance_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY,
            event_kind TEXT NOT NULL CHECK(event_kind IN ('weekly', 'reservation')),
            event_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(event_kind, event_id, event_date, username)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_records_event
            ON attendance_records(event_kind, event_id, event_date);

        CREATE TABLE IF NOT EXISTS attendance_session_failures (
            session_token TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def init_db(conn=None):
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        close_conn = True

    try:
        if _database_has_schema(conn):
            return False

        if not os.path.exists(SCHEMA_FILE):
            raise FileNotFoundError(f"Schema file not found: {SCHEMA_FILE}")

        with open(SCHEMA_FILE, encoding="utf-8") as f:
            conn.executescript(f.read())
        _ensure_attendance_schema(conn)
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        # enable WAL mode for concurrent access (Gunicorn)
        db.execute('PRAGMA journal_mode=WAL;')
        # ensure foreign keys are enforced
        db.execute('PRAGMA foreign_keys = ON;')
        init_db(db)
        _ensure_attendance_schema(db)
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=(), commit=True):
    conn = get_db()
    cur = conn.execute(query, args)
    if commit:
        conn.commit()
    return cur.lastrowid
    
# --- Login -------------
def login_or_service_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):

        # if the user is logged in normally
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        # otherwise, check the Bearer token for service access
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            if token == app.config["SERVICE_API_KEY"]:
                g.service_auth = True
                return f(*args, **kwargs)

        return jsonify({"error": "Unauthorized"}), 401

    return decorated

class User(UserMixin):
    def __init__(self, username, role="teacher"):
        self.id = username
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def radius_auth_mock(username, password):
    """
    PRIVREMENO: lokalni razvoj bez RADIUS-a.

    Prihvata bilo koju kombinaciju username/lozinka, pod uslovom da je
    username ne-prazan string. Za produkciju vratiti originalnu
    RADIUS implementaciju.
    """
    return bool(username)


def radius_auth(username, password):
    """
    Auth dispatcher.

    In production-like mode this uses the real RADIUS backend.
    For local development it falls back to the mock backend so the app
    stays runnable without external auth infrastructure.
    """
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


def student_radius_auth(username, password):
    """
    Student authentication against a separate RADIUS backend.
    Falls back to the mock backend locally unless STUDENT_AUTH_BACKEND=radius.
    """
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
        return False

    return reply.code == pyrad.packet.AccessAccept


def attendance_kind_valid(kind):
    return kind in {"weekly", "reservation"}


def attendance_code_seed(kind, event_id, event_date, bucket, salt="code"):
    payload = f"{kind}:{event_id}:{event_date}:{bucket}:{salt}".encode("utf-8")
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big")


def attendance_code_for_bucket(kind, event_id, event_date, bucket):
    return 1000 + (attendance_code_seed(kind, event_id, event_date, bucket, "answer") % 9000)


def attendance_options_for_bucket(kind, event_id, event_date, bucket):
    options = []
    seen = set()
    base = attendance_code_for_bucket(kind, event_id, event_date, bucket)

    seed = attendance_code_seed(kind, event_id, event_date, bucket, "options")
    rng = random.Random(seed)

    while len(options) < 4:
        if not options:
            candidate = base
        else:
            candidate = 1000 + rng.randrange(9000)
        if candidate in seen:
            continue
        seen.add(candidate)
        options.append(candidate)

    rng.shuffle(options)
    return options


def attendance_challenge_for_time(kind, event_id, event_date, now=None):
    now = now or datetime.datetime.now()
    bucket = int(now.timestamp() // ATTENDANCE_CHALLENGE_TTL)
    options = attendance_options_for_bucket(kind, event_id, event_date, bucket)
    return {
        "bucket": bucket,
        "current_code": attendance_code_for_bucket(kind, event_id, event_date, bucket),
        "options": options,
        "expires_in": ATTENDANCE_CHALLENGE_TTL - (int(now.timestamp()) % ATTENDANCE_CHALLENGE_TTL),
    }


def attendance_code_is_valid(kind, event_id, event_date, code, now=None):
    now = now or datetime.datetime.now()
    current_bucket = int(now.timestamp() // ATTENDANCE_CHALLENGE_TTL)
    for offset in range(4):
        bucket = current_bucket - offset
        if code == attendance_code_for_bucket(kind, event_id, event_date, bucket):
            return True
    return False


def attendance_event_row(kind, event_id, event_date):
    if kind == "weekly":
        row = query_db(
            """
            SELECT ws.id AS event_id,
                   ws.room_id,
                   r.name AS room_name,
                   ws.start_slot,
                   ws.end_slot,
                   ws.day_of_week,
                   c.id AS course_id,
                   c.name AS course_name,
                   c.code AS course_code,
                   cs.type AS course_type,
                   t.name AS teacher_name,
                   t.username AS teacher_username,
                   s.name AS semester_name,
                   GROUP_CONCAT(g.name, ',') AS groups,
                   CASE WHEN wxc.id IS NULL THEN 0 ELSE 1 END AS is_canceled
            FROM weekly_sessions ws
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN courses c ON c.id = cs.course_id
            JOIN teachers t ON t.id = cs.teacher_id
            JOIN semesters s ON s.id = cs.semester_id
            JOIN rooms r ON r.id = ws.room_id
            LEFT JOIN session_groups sg ON sg.session_id = cs.id
            LEFT JOIN groups g ON g.id = sg.group_id
            LEFT JOIN weekly_cancellations wxc
                   ON wxc.weekly_session_id = ws.id
                  AND wxc.date = ?
            WHERE ws.id = ?
              AND ? BETWEEN s.start_date AND s.end_date
            GROUP BY ws.id,
                     ws.room_id,
                     r.name,
                     ws.start_slot,
                     ws.end_slot,
                     ws.day_of_week,
                     c.id,
                     c.name,
                     c.code,
                     cs.type,
                     t.name,
                     t.username,
                     s.name,
                     wxc.id
            """,
            (event_date, event_id, event_date),
            one=True,
        )
        data = dict(row) if row else None
        if data:
            data["event_date"] = event_date
        return data

    if kind == "reservation":
        row = query_db(
            """
            SELECT r.id AS event_id,
                   r.room_id,
                   rm.name AS room_name,
                   r.start_slot,
                   r.end_slot,
                   r.date AS reservation_date,
                   r.description,
                   r.username AS owner_username
            FROM reservations r
            JOIN rooms rm ON rm.id = r.room_id
            WHERE r.id = ?
              AND r.date = ?
            """,
            (event_id, event_date),
            one=True,
        )
        data = dict(row) if row else None
        if data:
            data["event_date"] = event_date
        return data

    return None


def attendance_can_view(kind, row):
    if not current_user.is_authenticated:
        return False
    if check_if_admin(current_user.username):
        return True
    if kind == "weekly":
        return current_user.username == row["teacher_username"]
    return current_user.username == row["owner_username"]


def attendance_records_for_event(kind, event_id, event_date):
    rows = query_db(
        """
        SELECT username, created_at
        FROM attendance_records
        WHERE event_kind = ?
          AND event_id = ?
          AND event_date = ?
        ORDER BY created_at, id
        """,
        (kind, event_id, event_date),
    )
    return [dict(row) for row in rows]


def attendance_record_student(kind, event_id, event_date, username):
    execute_db(
        """
        INSERT OR IGNORE INTO attendance_records (event_kind, event_id, event_date, username)
        VALUES (?, ?, ?, ?)
        """,
        (kind, event_id, event_date, username),
    )


# Attendance failure tracking and expiry cleanup.
def attendance_session_failure_state(session_token):
    if not session_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_session_failures
            (session_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (session_token,),
    )
    row = query_db(
        """
        SELECT session_token, failed_attempts, blocked, updated_at
        FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_session_failure_state_raw(session_token):
    if not session_token:
        return None
    row = query_db(
        """
        SELECT session_token, failed_attempts, blocked, updated_at
        FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
        one=True,
    )
    return dict(row) if row else None


def attendance_session_token_expires_at(session_token):
    if not session_token or "." not in session_token:
        return None
    payload, _signature = session_token.rsplit(".", 1)
    parts = payload.split(":")
    if len(parts) != 5:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def attendance_cleanup_expired_failures(now=None):
    now = now or datetime.datetime.now()
    now_ts = int(now.timestamp())
    rows = query_db(
        """
        SELECT session_token
        FROM attendance_session_failures
        """,
    ) or []
    expired_tokens = []
    for row in rows:
        token = row["session_token"]
        expires_at = attendance_session_token_expires_at(token)
        if expires_at is not None and expires_at < now_ts:
            expired_tokens.append(token)
    if not expired_tokens:
        return 0
    for token in expired_tokens:
        execute_db(
            """
            DELETE FROM attendance_session_failures
            WHERE session_token = ?
            """,
            (token,),
        )
    return len(expired_tokens)


def attendance_session_is_blocked(session_token):
    state = attendance_session_failure_state(session_token)
    return bool(state and state["blocked"])


def attendance_session_is_blocked_raw(session_token):
    state = attendance_session_failure_state_raw(session_token)
    return bool(state and state["blocked"])


def attendance_session_status(kind, event_id, event_date):
    attendance_cleanup_expired_failures()
    session_token = attendance_session_from_request(kind, event_id, event_date)
    if not session_token:
        return "missing"
    if attendance_session_is_blocked_raw(session_token):
        return "blocked"
    if not attendance_validate_session_token(kind, event_id, event_date, session_token):
        return "expired"
    return "valid"


# Attendance QR token and attendance session helpers.
def attendance_session_record_failure(session_token):
    if not session_token:
        return None

    execute_db(
        """
        INSERT OR IGNORE INTO attendance_session_failures
            (session_token, failed_attempts, blocked)
        VALUES (?, 0, 0)
        """,
        (session_token,),
    )
    execute_db(
        """
        UPDATE attendance_session_failures
        SET failed_attempts = failed_attempts + 1,
            blocked = CASE
                WHEN failed_attempts + 1 >= 2 THEN 1
                ELSE blocked
            END,
            updated_at = datetime('now')
        WHERE session_token = ?
        """,
        (session_token,),
    )
    return attendance_session_failure_state(session_token)


def attendance_session_clear_failures(session_token):
    if not session_token:
        return
    execute_db(
        """
        DELETE FROM attendance_session_failures
        WHERE session_token = ?
        """,
        (session_token,),
    )


def attendance_token_bucket(now=None):
    now = now or datetime.datetime.now()
    return int(now.timestamp() // ATTENDANCE_JOIN_TOKEN_TTL)


def attendance_token_for_bucket(kind, event_id, event_date, bucket):
    payload = f"{kind}:{event_id}:{event_date}:{bucket}:join".encode("utf-8")
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return digest[:48]


def attendance_join_token(kind, event_id, event_date, now=None):
    bucket = attendance_token_bucket(now)
    return attendance_token_for_bucket(kind, event_id, event_date, bucket)


def attendance_token_is_valid(kind, event_id, event_date, token, now=None):
    if not token:
        return False
    now = now or datetime.datetime.now()
    current_bucket = attendance_token_bucket(now)
    for bucket in (current_bucket, current_bucket - 1):
        if token == attendance_token_for_bucket(kind, event_id, event_date, bucket):
            return True
    return False


def attendance_session_cookie_name(kind, event_id, event_date):
    return f"attendance_session_{kind}_{event_id}_{event_date}"


def attendance_session_payload(kind, event_id, event_date, expires_at, nonce):
    return f"{kind}:{event_id}:{event_date}:{expires_at}:{nonce}"


def attendance_session_sign(payload):
    digest = hmac.new(ATTENDANCE_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def attendance_create_session_token(kind, event_id, event_date, now=None):
    now = now or datetime.datetime.now()
    expires_at = int(now.timestamp()) + ATTENDANCE_SESSION_TTL
    nonce = secrets.token_urlsafe(12)
    payload = attendance_session_payload(kind, event_id, event_date, expires_at, nonce)
    signature = attendance_session_sign(payload)
    return f"{payload}.{signature}"


def attendance_validate_session_token(kind, event_id, event_date, token, now=None):
    if not token or "." not in token:
        return False
    now = now or datetime.datetime.now()
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(attendance_session_sign(payload), signature):
        return False
    parts = payload.split(":")
    if len(parts) != 5:
        return False
    token_kind, token_event_id, token_event_date, expires_at_str, _nonce = parts
    if token_kind != kind or token_event_date != event_date or token_event_id != str(event_id):
        return False
    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return False
    return int(now.timestamp()) <= expires_at


def attendance_session_from_request(kind, event_id, event_date):
    cookie_name = attendance_session_cookie_name(kind, event_id, event_date)
    return request.cookies.get(cookie_name)


def attendance_has_session(kind, event_id, event_date):
    token = attendance_session_from_request(kind, event_id, event_date)
    return attendance_validate_session_token(kind, event_id, event_date, token)


def attendance_make_session_response(response, kind, event_id, event_date):
    token = attendance_create_session_token(kind, event_id, event_date)
    cookie_name = attendance_session_cookie_name(kind, event_id, event_date)
    response.set_cookie(
        cookie_name,
        token,
        max_age=ATTENDANCE_SESSION_TTL,
        httponly=True,
        samesite="Lax",
    )
    return response

@app.route('/login', methods=['POST'])
def login():
    limited = enforce_rate_limit("login", *RATE_LIMITS["login"])
    if limited is not None:
        return limited

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if radius_auth(username, password):
        user = User(username)
        login_user(user)
        return jsonify({'success': True, 'username': user.username, 'role': user.role})
    return jsonify({'error':'Invalid credentials'}), 401

@app.route('/logout', methods=['POST'])
@login_or_service_required
def logout():
    logout_user()
    response = jsonify({'success': True})
    return response

@app.route('/whoami', methods=['GET'])
def whoami():
    if current_user.is_authenticated:
        return jsonify({'logged_in': True, 'username': current_user.username})
    else:
        return jsonify({'logged_in': False})


def check_if_admin(username):
    """Return True if the user is an administrator, False otherwise."""
    try:
        result = query_db('SELECT 1 FROM administrators WHERE username = ?', (username,), one=True)
        return bool(result)
    except:
        return False
    
@app.route("/is_admin/<username>")
def is_admin(username):
    return jsonify({"username": username, "is_admin": check_if_admin(username)})


# --- Endpoints --------------------------------------------------

# Calendar and semester helpers.

@app.route('/rooms')
def list_rooms():
    query = 'SELECT id, name, capacity, type, location, priority FROM rooms'
    params = ()

    # If type is provided, filter only rooms of that type
    room_type = request.args.get("type")
    if room_type:
        query += ' WHERE type = ?'
        params = (room_type,)

    rows = query_db(query, params)
    rooms_list = [
        {
            "id": r["id"],
            "name": r["name"],
            "capacity": r["capacity"],
            "type": r["type"],
            "location": r["location"],
            "priority": r["priority"]
        }
        for r in rows
    ]

    return jsonify(rooms_list)


def iso_to_weekday(date_str):
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    # Python weekday(): Monday=0 ... Sunday=6 (matches our schema)
    return dt.weekday()

def check_day(date):
    # Look up the calendar entry for this date.
    ad = query_db('SELECT is_working, week_day FROM days WHERE date = ?', (date,), one=True)
    if ad:
        is_working = ad['is_working'] == 1
        week_day = ad['week_day']        # Taken from the database.
    else:
        is_working = False
        week_day = -1                     # Default if no record exists in the database.

    if week_day == -1:
        dow = iso_to_weekday(date)   # Use the standard weekday calculation.
    else:
        dow = week_day               # Use the value from the database.

    return (is_working, week_day, dow)


def fetch_semesters():
    rows = query_db(
        'SELECT id, name, start_date, end_date FROM semesters ORDER BY start_date DESC, id DESC'
    )
    return [dict(row) for row in rows]


def current_semester_id():
    today = datetime.date.today().isoformat()
    row = query_db(
        '''
        SELECT id
        FROM semesters
        WHERE ? BETWEEN start_date AND end_date
        ORDER BY start_date DESC, id DESC
        LIMIT 1
        ''',
        (today,),
        one=True,
    )
    if row:
        return row["id"]

    row = query_db(
        'SELECT id FROM semesters ORDER BY start_date DESC, id DESC LIMIT 1',
        one=True,
    )
    return row["id"] if row else None


def semester_by_id(semester_id):
    if semester_id is None:
        return None
    row = query_db(
        'SELECT id, name, start_date, end_date FROM semesters WHERE id = ?',
        (semester_id,),
        one=True,
    )
    return dict(row) if row else None


# Reservation payload helpers.
def personal_reservations_for_semester(username, semester):
    if not semester:
        return []

    rows = query_db(
        '''
        SELECT r.id,
               r.room_id,
               rm.name AS room_name,
               r.date,
               r.start_slot,
               r.end_slot,
               r.description,
               r.username,
               r.created_at
        FROM reservations r
        JOIN rooms rm ON rm.id = r.room_id
        WHERE r.username = ?
          AND r.date BETWEEN ? AND ?
        ORDER BY r.date, r.start_slot, r.room_id, r.id
        ''',
        (username, semester["start_date"], semester["end_date"]),
    )
    return [dict(row) for row in rows]


def course_sessions_for_semester(semester_id, teacher_username=None):
    params = [semester_id]
    teacher_clause = ""
    if teacher_username:
        teacher_clause = "AND t.username = ?"
        params.append(teacher_username)

    rows = query_db(
        '''
        SELECT c.id AS course_id,
               c.name AS course_name,
               c.code AS course_code,
               cs.id AS course_session_id,
               cs.type AS course_type,
               t.name AS teacher_name,
               t.username AS teacher_username,
               ws.id AS weekly_session_id,
               ws.room_id,
               rm.name AS room_name,
               ws.day_of_week,
               ws.start_slot,
               ws.end_slot,
               GROUP_CONCAT(g.name, ',') AS groups
        FROM course_sessions cs
        JOIN courses c ON c.id = cs.course_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN weekly_sessions ws ON ws.session_id = cs.id
        JOIN rooms rm ON rm.id = ws.room_id
        LEFT JOIN session_groups sg ON sg.session_id = cs.id
        LEFT JOIN groups g ON g.id = sg.group_id
        WHERE cs.semester_id = ?
        {teacher_clause}
        GROUP BY c.id,
                 c.name,
                 c.code,
                 cs.id,
                 cs.type,
                 t.name,
                 t.username,
                 ws.id,
                 ws.room_id,
                 rm.name,
                 ws.day_of_week,
                 ws.start_slot,
                 ws.end_slot
        ORDER BY c.name, c.code, cs.id, ws.day_of_week, ws.start_slot, ws.room_id
        '''.format(teacher_clause=teacher_clause),
        tuple(params),
    )

    courses = {}
    for row in rows:
        course_id = row["course_id"]
        course = courses.setdefault(
            course_id,
            {
                "course_id": course_id,
                "course_name": row["course_name"],
                "course_code": row["course_code"],
                "sessions": [],
            },
        )
        course["sessions"].append({
            "course_session_id": row["course_session_id"],
            "weekly_session_id": row["weekly_session_id"],
            "course_type": row["course_type"],
            "teacher_name": row["teacher_name"],
            "teacher_username": row["teacher_username"],
            "room_id": row["room_id"],
            "room_name": row["room_name"],
            "day_of_week": row["day_of_week"],
            "start_slot": row["start_slot"],
            "end_slot": row["end_slot"],
            "groups": row["groups"].split(",") if row["groups"] else [],
        })

    return list(courses.values())


def weekly_session_instances_for_semester(semester_id, teacher_username=None):
    params = [semester_id]
    teacher_clause = ""
    if teacher_username:
        teacher_clause = "AND t.username = ?"
        params.append(teacher_username)

    rows = query_db(
        '''
        SELECT ws.id AS weekly_session_id,
               d.date AS date
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN days d
          ON d.date BETWEEN s.start_date AND s.end_date
         AND d.is_working = 1
         AND ws.day_of_week = CASE
                WHEN d.week_day = -1 THEN ((CAST(strftime('%w', d.date) AS INTEGER) + 6) % 7)
                ELSE d.week_day
             END
        LEFT JOIN weekly_cancellations wxc
               ON wxc.weekly_session_id = ws.id
              AND wxc.date = d.date
        WHERE cs.semester_id = ?
          {teacher_clause}
          AND wxc.id IS NULL
        ORDER BY ws.id, d.date
        '''.format(teacher_clause=teacher_clause),
        tuple(params),
    )

    instances = {}
    for row in rows:
        instances.setdefault(row["weekly_session_id"], []).append(row["date"])

    return instances


def my_reservations_payload(username, semester_id=None):
    semesters = fetch_semesters()
    selected_semester_id = semester_id if semester_id is not None else current_semester_id()
    if selected_semester_id is None and semesters:
        selected_semester_id = semesters[0]["id"]

    selected_semester = semester_by_id(selected_semester_id)
    personal = personal_reservations_for_semester(username, selected_semester)
    courses = []
    if selected_semester_id:
        courses = course_sessions_for_semester(selected_semester_id, teacher_username=username)
        instances_by_session = weekly_session_instances_for_semester(
            selected_semester_id,
            teacher_username=username,
        )
        for course in courses:
            for session in course["sessions"]:
                session["instances"] = instances_by_session.get(session["weekly_session_id"], [])

    return {
        "semesters": semesters,
        "current_semester_id": current_semester_id(),
        "selected_semester": selected_semester,
        "personal_reservations": personal,
        "courses": courses,
    }


# --- Read APIs --------------------------------------------------

@app.route('/occupancy')
def occupancy():
    date = request.args.get('date')
    if not date:
        abort(400, 'date param required YYYY-MM-DD')
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'invalid date format, expected YYYY-MM-DD')

    # determine day
    (is_working, week_day, dow) = check_day(date)
    result = {}

    # weekly classes (if working day)
    if is_working:
        q = '''
        SELECT ws.id AS ws_id,
               ws.room_id,
               r.name AS room,
               ws.start_slot,
               ws.end_slot,
               c.id AS course_id,
               c.name AS course_name,
               cs.type AS course_type,
               t.name AS teacher,
               t.username AS teacher_username,
               GROUP_CONCAT(g.name, ',') AS groups,
               CASE WHEN wxc.id IS NULL THEN 0 ELSE 1 END AS is_canceled
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN rooms r ON r.id = ws.room_id
        LEFT JOIN session_groups sg ON sg.session_id = cs.id
        LEFT JOIN groups g ON g.id = sg.group_id
        LEFT JOIN weekly_cancellations wxc
               ON wxc.weekly_session_id = ws.id
              AND wxc.date = ?
        WHERE ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        GROUP BY ws.id,
          ws.room_id,
          r.name,
          ws.start_slot,
          ws.end_slot,
          c.id,
          c.name,
          cs.type,
          t.name,
          t.username;
         '''
        for r in query_db(q, (date, dow, date)):
            # If the class is canceled and there is any overlap with a reservation
            # in the same slot, we do not show it in the schedule.
            if bool(r['is_canceled']):
                overlap = query_db(
                    '''
                    SELECT 1
                    FROM reservations
                    WHERE room_id = ?
                      AND date = ?
                      AND (start_slot < ? AND ? < end_slot)
                    ''',
                    (r['room_id'], date, r['end_slot'], r['start_slot']),
                    one=True,
                )
                if overlap:
                    continue

            room_id = str(r['room_id'])
            result.setdefault(room_id, []).append({
                'type': 'weekly',
                'weekly_session_id': r['ws_id'],
                'start': r['start_slot'],
                'end': r['end_slot'],
                'lecture_id': r['course_id'],
                'lecture_name': r['course_name'],
                'lecture_type': r['course_type'],
                'teacher': r['teacher'],
                'teacher_username': r['teacher_username'],
                'room': r['room'],
                'groups': r['groups'].split(',') if r['groups'] else [],
                'canceled': bool(r['is_canceled'])
            })

    # reservations for that date
    q2 = '''
    SELECT reservations.id, room_id, r.name as room, start_slot, end_slot, description, username
    FROM reservations JOIN rooms r ON reservations.room_id = r.id
    WHERE date = ?
    '''
    for r in query_db(q2, (date,)):
        room_id = str(r['room_id'])
        result.setdefault(room_id, []).append({
            'type': 'reservation',
            'id': r['id'],
            'start': r['start_slot'],
            'end': r['end_slot'],
            'description': r['description'],
            'username': r['username'],
            'room': r['room']
        })


    # sort each room's list by start
    for k in result:
        result[k].sort(key=lambda x: x['start'])

    return jsonify({'date': date, 'is_working': is_working, 'week_day': dow, 'rooms': result})


# --- Reservation writes ----------------------------------------

def _create_single_reservation(data, username, is_service, commit=True):
    room_id = data.get('room_id')
    date = data.get('date')
    start = data.get('start_slot')
    end = data.get('end_slot')
    desc = data.get('description', '')

    if not all([room_id is not None, date, start is not None, end is not None]):
        abort(400, 'missing fields')

    try:
        datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'invalid date format, expected YYYY-MM-DD')

    if not (isinstance(start, int) and isinstance(end, int) and start < end):
        abort(400, 'invalid slots')

    room = query_db('SELECT id FROM rooms WHERE id = ?', (room_id,), one=True)
    if room == None:
        abort(400, f'room not found {room_id}')

    (is_working, week_day, dow) = check_day(date)

    if is_working:
        wc_conf = query_db('''
            SELECT 1
            FROM weekly_sessions ws
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN semesters s ON s.id = cs.semester_id
            LEFT JOIN weekly_cancellations wxc
                   ON wxc.weekly_session_id = ws.id
                  AND wxc.date = ?
            WHERE ws.room_id = ?
              AND ws.day_of_week = ?
              AND ? BETWEEN s.start_date AND s.end_date
              AND (ws.start_slot < ? AND ? < ws.end_slot)
              AND wxc.id IS NULL
        ''', (date, room_id, dow, date, end, start))

        if wc_conf:
            abort(409, 'conflict with regular weekly class')

    res_conf = query_db('''
        SELECT 1 FROM reservations
        WHERE room_id = ? AND date = ?
        AND (start_slot < ? AND ? < end_slot)
    ''', (room_id, date, end, start))

    if res_conf:
        abort(409, 'conflict with existing reservation')

    rid = execute_db('''
        INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (room_id, username, date, start, end, desc), commit=commit)

    return rid


@app.route('/reserve', methods=['POST'])
@login_or_service_required
def create_reservation():
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    data = request.get_json() or {}
    is_service = getattr(g, "service_auth", False)

    username = data.get('username', '')
    if not is_service and (not check_if_admin(current_user.username) or username == ''):
        username = current_user.username

    rid = _create_single_reservation(data, username, is_service)

    return jsonify({'reservation_id': rid}), 201

@app.route('/reserve/bulk', methods=['POST'])
@login_or_service_required
def bulk_reservations():
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    payload = request.get_json() or {}
    reservations = payload.get('reservations')

    if not reservations or not isinstance(reservations, list):
        abort(400, 'reservations must be a list')

    is_service = getattr(g, "service_auth", False)

    created_ids = []

    conn = get_db()
    try:
        conn.execute("BEGIN")
        for r in reservations:
            username = r['username']
            if not is_service and (not check_if_admin(current_user.username) or username == ''):
                username = current_user.username

            rid = _create_single_reservation(r, username, is_service, commit=False)
            created_ids.append(rid)
        conn.commit()

    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            return jsonify({'error': 'bulk reservation failed'}), e.code
        app.logger.exception("Bulk reservation failed")
        return jsonify({'error': 'bulk reservation failed'}), 409

    return jsonify({
        'created': len(created_ids),
        'reservation_ids': created_ids
    }), 201

@app.route('/reservation/<int:res_id>', methods=['DELETE'])
@login_or_service_required
def cancel_reservation(res_id):
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    # Check whether the reservation exists and belongs to the current user
    row = query_db('SELECT username FROM reservations WHERE id = ?', (res_id,), one=True)
    if not row:
        return jsonify({'error':'Reservation not found'}), 404

    is_service = getattr(g, "service_auth", False)
    if not is_service and row['username'] != current_user.username and not check_if_admin(current_user.username):
        return jsonify({'error':'Forbidden'}), 403

    # Delete the reservation
    execute_db('DELETE FROM reservations WHERE id = ?', (res_id,))
    return jsonify({'success': True})


@app.route('/weekly_session_cancel', methods=['POST'])
@login_or_service_required
def cancel_weekly_session_for_date():
    """
    Toggles cancellation of a single weekly session occurrence for a specific date.

    If there is no cancellation for the given (weekly_session_id, date), one is created.
    If a cancellation already exists, it is removed and the class is considered scheduled again.

    Request JSON:
    {
        "weekly_session_id": <int>,
        "date": "YYYY-MM-DD"
    }

    Response JSON:
    {
        "success": true,
        "canceled": true/false   # true = now canceled, false = restored
    }
    """
    limited = enforce_rate_limit("reservation", *RATE_LIMITS["reservation"])
    if limited is not None:
        return limited

    data = request.get_json() or {}
    ws_id = data.get('weekly_session_id')
    date = data.get('date')

    if not ws_id or not date:
        return jsonify({'error': 'weekly_session_id and date are required'}), 400

    # validate date format
    try:
        _ = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'invalid date format, expected YYYY-MM-DD'}), 400

    # resolve working day and weekday index
    is_working, week_day, dow = check_day(date)
    if not is_working:
        return jsonify({'error': 'selected date is not a working day'}), 400

    # verify that this weekly session is active on that date (semester bounds and weekday)
    row = query_db(
        '''
        SELECT ws.id,
               ws.room_id,
               ws.start_slot,
               ws.end_slot,
               t.username AS teacher_username
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN teachers t ON t.id = cs.teacher_id
        WHERE ws.id = ?
          AND ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        ''',
        (ws_id, dow, date),
        one=True
    )

    if not row:
        return jsonify({'error': 'weekly session not found for given date'}), 404

    is_service = getattr(g, "service_auth", False)

    # Allowed: the teacher who teaches the class, an administrator, or a service account
    if not is_service and current_user.is_authenticated:
        if not (current_user.username == row['teacher_username'] or check_if_admin(current_user.username)):
            return jsonify({'error': 'Forbidden'}), 403

    username = current_user.username if (current_user.is_authenticated and not is_service) else 'service'

    existing = query_db(
        '''
        SELECT id FROM weekly_cancellations
        WHERE weekly_session_id = ? AND date = ?
        ''',
        (ws_id, date),
        one=True
    )

    if existing:
        # already canceled -> before restoring it, check whether any reservation
        # overlaps with that slot
        res_conf = query_db(
            '''
            SELECT 1
            FROM reservations
            WHERE room_id = ?
              AND date = ?
              AND (start_slot < ? AND ? < end_slot)
            ''',
            (row['room_id'], date, row['end_slot'], row['start_slot']),
            one=True
        )
        if res_conf:
            return jsonify({
                'error': 'Cannot restore the canceled class because a reservation exists in this slot.'
            }), 409

        execute_db(
            'DELETE FROM weekly_cancellations WHERE id = ?',
            (existing['id'],)
        )
        return jsonify({'success': True, 'canceled': False})

    # no cancellation exists -> create one
    execute_db(
        '''
        INSERT INTO weekly_cancellations (weekly_session_id, date, username)
        VALUES (?, ?, ?)
        ''',
        (ws_id, date, username)
    )

    return jsonify({'success': True, 'canceled': True})


# --- Calendar and semester state --------------------------------

@app.route('/calendar_data')
def calendar_data():
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    if month is None or year is None:
        abort(400, 'month and year are required')
    if month < 1 or month > 12:
        abort(400, 'month must be between 1 and 12')

    # all days in the month
    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    
    rows = query_db('SELECT date, is_working, week_day FROM days WHERE date BETWEEN ? AND ?',
                    (first_day.isoformat(), last_day.isoformat()))

    # convert to a dict
    calendar_dict = dict()
    for r in rows:
        calendar_dict[r['date']] = {
            'is_working': r['is_working'],
            'week_day': r['week_day']
        }

    # optional: add holidays
    holidays = ['2026-01-01', '2026-01-07']

    return jsonify({'calendar': calendar_dict, 'holidays': holidays})

@app.route('/update_calendar', methods=['POST'])
def update_calendar():
    limited = enforce_rate_limit("calendar", *RATE_LIMITS["calendar"])
    if limited is not None:
        return limited

    updates = request.get_json()  # list of objects {date, is_working, week_day}

    is_service = False
    if current_user.is_authenticated:
        if not check_if_admin(current_user.username):
            return jsonify({'error': 'Forbidden'}), 403
    else:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            is_service = token == app.config["SERVICE_API_KEY"]
        if not is_service:
            return jsonify({'error': 'Unauthorized'}), 401

    if not isinstance(updates, list):
        return jsonify({'error': 'expected a list of updates'}), 400

    for u in updates:
        date_str = u['date']
        is_working = u['is_working']
        week_day = u.get('week_day', -1)

        try:
            datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': f'invalid date format for {date_str}, expected YYYY-MM-DD'}), 400

        # Calculate the real weekday for this date (0=Monday ... 6=Sunday).
        real_wd = iso_to_weekday(date_str)

        # If week_day matches the real weekday, use -1.
        if week_day == real_wd:
            week_day = -1

        # Check whether a record already exists.
        existing = query_db('SELECT 1 FROM days WHERE date = ?', (date_str,), one=True)

        # If it is not a working day and no record exists, skip it.
        if not is_working and not existing:
            continue

        # Replace the existing calendar row.
        execute_db(
            '''
            REPLACE INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            ''',
            (date_str, 1 if is_working else 0, week_day)
        )
    return jsonify(success=True)


@app.route('/calendar')
def calendar_view():
    return render_template('calendar.html')


@app.route('/my_reservations')
def my_reservations_view():
    return render_template("my_reservations.html")

@app.route('/my_reservations_data')
@login_required
def my_reservations_data():
    semester_id = request.args.get('semester_id', type=int)
    semesters = fetch_semesters()
    selected_semester_id = semester_id if semester_id is not None else current_semester_id()
    if selected_semester_id is None and semesters:
        selected_semester_id = semesters[0]["id"]

    selected_semester = semester_by_id(selected_semester_id)
    if semester_id is not None and selected_semester is None:
        abort(404, 'semester not found')

    payload = my_reservations_payload(current_user.username, selected_semester_id)
    return jsonify(payload)


# --- Attendance -------------------------------------------------

@app.route('/attendance/<kind>/<int:event_id>/<event_date>')
def attendance_view(kind, event_id, event_date):
    """Render the teacher attendance page."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    return render_template(
        'attendance_teacher.html',
        attendance_kind=kind,
        attendance_event_id=event_id,
        attendance_event_date=event_date,
    )


@app.route('/attendance/<kind>/<int:event_id>/<event_date>/join')
def attendance_join_view(kind, event_id, event_date):
    """Render the student attendance page after a QR scan creates a session."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    if not attendance_has_session(kind, event_id, event_date):
        abort(403)
    return render_template(
        'attendance_join.html',
        attendance_kind=kind,
        attendance_event_id=event_id,
        attendance_event_date=event_date,
    )


@app.route('/attendance/<kind>/<int:event_id>/<event_date>/join/<token>')
def attendance_join_view_token(kind, event_id, event_date, token):
    """Validate a short-lived QR token and create the student attendance session."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)
    if not attendance_token_is_valid(kind, event_id, event_date, token):
        abort(404)
    response = make_response(
        redirect(url_for(
            'attendance_join_view',
            kind=kind,
            event_id=event_id,
            event_date=event_date,
        ))
    )
    return attendance_make_session_response(response, kind, event_id, event_date)


@app.route('/attendance/<kind>/<int:event_id>/<event_date>/challenge')
def attendance_challenge_data(kind, event_id, event_date):
    """Return the current challenge code and event metadata."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    session_status = attendance_session_status(kind, event_id, event_date)
    if session_status == "blocked":
        return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
    if session_status != "valid":
        return jsonify({'error': 'Сесија је истекла. Поново скенирајте QR код.'}), 403

    challenge = attendance_challenge_for_time(kind, event_id, event_date)
    return jsonify({
        'event': row,
        'challenge': challenge,
    })


@app.route('/attendance/<kind>/<int:event_id>/<event_date>/data')
def attendance_roster_data(kind, event_id, event_date):
    """Return teacher attendance data, including the current attendance list and QR token."""
    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if not attendance_can_view(kind, row):
        return jsonify({'error': 'Forbidden'}), 403

    challenge = attendance_challenge_for_time(kind, event_id, event_date)
    return jsonify({
        'event': row,
        'challenge': challenge,
        'join_token': attendance_join_token(kind, event_id, event_date),
        'students': attendance_records_for_event(kind, event_id, event_date),
        'can_view': True,
    })


@app.route('/attendance/<kind>/<int:event_id>/<event_date>/join', methods=['POST'])
def attendance_join_submit(kind, event_id, event_date):
    """Accept a student attendance check-in after validating credentials and the challenge code."""
    limited = enforce_rate_limit("attendance", *RATE_LIMITS["attendance"])
    if limited is not None:
        return limited

    attendance_cleanup_expired_failures()
    if not attendance_kind_valid(kind):
        abort(404)

    row = attendance_event_row(kind, event_id, event_date)
    if not row:
        return jsonify({'error': 'event not found'}), 404
    if kind == 'weekly' and bool(row.get('is_canceled')):
        return jsonify({'error': 'this lecture occurrence is canceled'}), 409
    session_token = attendance_session_from_request(kind, event_id, event_date)
    session_status = attendance_session_status(kind, event_id, event_date)
    if session_status == "blocked":
        return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
    if session_status != "valid":
        return jsonify({'error': 'Сесија је истекла. Поново скенирајте QR код.'}), 403

    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    selected_code = data.get('selected_code')

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    try:
        selected_code = int(selected_code)
    except (TypeError, ValueError):
        return jsonify({'error': 'selected code is required'}), 400

    if not attendance_code_is_valid(kind, event_id, event_date, selected_code):
        state = attendance_session_record_failure(session_token)
        if state and state["blocked"]:
            return jsonify({'error': 'Морате поново да скенирате QR код.'}), 403
        return jsonify({'error': 'Погрешан број. Сачекајте нови круг.'}), 409

    if not student_radius_auth(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    attendance_session_clear_failures(session_token)
    attendance_record_student(kind, event_id, event_date, username)
    return jsonify({'success': True, 'username': username})

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classroom reservation app")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "init-db"),
        default="run",
        help="run the web app or initialize the database schema",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    with app.app_context():
        if args.command == "init-db":
            created = init_db()
            if created:
                print(f"Initialized database schema at {DATABASE}")
            else:
                print(f"Database already initialized at {DATABASE}")
        else:
            get_db()
            app.run(host=args.host, port=args.port, debug=False)
