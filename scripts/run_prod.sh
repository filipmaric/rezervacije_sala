#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Production-style defaults for deployment under /rezervacije.
# TEACHER_AUTH_BACKEND controls teacher authentication; STUDENT_AUTH_BACKEND controls student authentication.
export APP_ENV="${APP_ENV:-production}"
export APPLICATION_ROOT="${APPLICATION_ROOT:-/rezervacije}"
export STATIC_URL_PATH="${STATIC_URL_PATH:-/rezervacije/static}"
export TEACHER_AUTH_BACKEND="${TEACHER_AUTH_BACKEND:-radius}"
export STUDENT_AUTH_BACKEND="${STUDENT_AUTH_BACKEND:-radius}"

: "${SECRET_KEY:?Set SECRET_KEY before starting the server}"
: "${SERVICE_API_KEY:?Set SERVICE_API_KEY before starting the server}"
: "${ATTENDANCE_SECRET:?Set ATTENDANCE_SECRET before starting the server}"
: "${TEACHER_RADIUS_SERVER:?Set TEACHER_RADIUS_SERVER before starting the server}"
: "${TEACHER_RADIUS_SECRET:?Set TEACHER_RADIUS_SECRET before starting the server}"
: "${TEACHER_RADIUS_DICTIONARY:?Set TEACHER_RADIUS_DICTIONARY before starting the server}"
: "${STUDENT_RADIUS_SERVER:?Set STUDENT_RADIUS_SERVER before starting the server}"
: "${STUDENT_RADIUS_SECRET:?Set STUDENT_RADIUS_SECRET before starting the server}"
: "${STUDENT_RADIUS_DICTIONARY:?Set STUDENT_RADIUS_DICTIONARY before starting the server}"

exec "$ROOT_DIR/venv/bin/python" app.py
