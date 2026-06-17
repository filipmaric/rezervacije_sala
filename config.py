"""Central application configuration and environment-backed constants."""

import os


# Absolute path to the application directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# SQLite database file used by the application.
DATABASE = os.path.join(BASE_DIR, "app.db")
# SQL schema file used when creating the database from scratch.
SCHEMA_FILE = os.path.join(BASE_DIR, "schema.sql")
# Current runtime mode: development, prod, or production.
APP_ENV = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
# True when the application should enforce production-only checks.
IS_PRODUCTION = APP_ENV in {"production", "prod"}


def load_secret_env(name, dev_default):
    """Read a secret from the environment and require it in production."""
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"{name} must be set when APP_ENV=production")
    return dev_default


def load_env(name, dev_default):
    """Read a non-secret configuration value from the environment."""
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"{name} must be set when APP_ENV=production")
    return dev_default


# URL prefix where the app is mounted. In production it is /rezervacije.
STATIC_URL_PATH = os.getenv("STATIC_URL_PATH", "/static")
# Base URL path used when Flask builds links for this app.
APPLICATION_ROOT = os.getenv("APPLICATION_ROOT", "/")
# Path to the application log file.
LOG_FILE = os.getenv("APP_LOG_FILE", os.path.join(BASE_DIR, "app.log"))
# Secret used to sign attendance QR/session tokens.
ATTENDANCE_SECRET = load_secret_env("ATTENDANCE_SECRET", "attendance-secret")
# How many seconds one QR token stays valid before it rotates.
ATTENDANCE_JOIN_TOKEN_TTL = int(os.getenv("ATTENDANCE_JOIN_TOKEN_TTL", "8"))
# How many seconds one challenge number stays visible before it changes.
ATTENDANCE_CHALLENGE_TTL = int(os.getenv("ATTENDANCE_CHALLENGE_TTL", "10"))
# How many seconds the student attendance form stays valid after scanning the QR code.
ATTENDANCE_SESSION_TTL = int(os.getenv("ATTENDANCE_SESSION_TTL", "90"))
# How many older challenge rounds are still accepted as a grace window.
ATTENDANCE_PREVIOUS_CHALLENGE_ROUNDS = int(os.getenv("ATTENDANCE_PREVIOUS_CHALLENGE_ROUNDS", "2"))
# How many minutes before and after class attendance is allowed.
ATTENDANCE_CLASS_GRACE_MINUTES = int(os.getenv("ATTENDANCE_CLASS_GRACE_MINUTES", "15"))
# How many days an Android app session remains valid.
MOBILE_AUTH_SESSION_DAYS = int(os.getenv("MOBILE_AUTH_SESSION_DAYS", "30"))
# Teacher login mode: "mock" for local development or "radius" in production.
TEACHER_AUTH_BACKEND = os.getenv("TEACHER_AUTH_BACKEND", "mock").lower()
# Address of the teacher RADIUS server.
TEACHER_RADIUS_SERVER = load_env("TEACHER_RADIUS_SERVER", "147.91.66.2")
# Shared secret used when talking to the teacher RADIUS server.
TEACHER_RADIUS_SECRET = load_secret_env("TEACHER_RADIUS_SECRET", "raspored2mainWebsite").encode()
# Dictionary file that tells the RADIUS client which attribute names to use.
TEACHER_RADIUS_DICTIONARY = load_env("TEACHER_RADIUS_DICTIONARY", "/var/www/rezervacije/radius/dictionary")
# Student login mode: "mock" for local development or "radius" in production.
STUDENT_AUTH_BACKEND = os.getenv("STUDENT_AUTH_BACKEND", "mock").lower()
# Address of the student RADIUS server.
STUDENT_RADIUS_SERVER = load_env("STUDENT_RADIUS_SERVER", "147.91.66.2")
# Shared secret used when talking to the student RADIUS server.
STUDENT_RADIUS_SECRET = load_secret_env("STUDENT_RADIUS_SECRET", "raspored2mainWebsite").encode()
# Dictionary file that tells the RADIUS client which attribute names to use.
STUDENT_RADIUS_DICTIONARY = load_env("STUDENT_RADIUS_DICTIONARY", "/var/www/rezervacije/radius/dictionary")
# Secret key used to protect service-only API requests.
SERVICE_API_KEY = load_secret_env("SERVICE_API_KEY", "dev-service-api-key")
# Flask session secret used for signed cookies and CSRF protection.
SECRET_KEY = load_secret_env("SECRET_KEY", "classroommatfreservations")
