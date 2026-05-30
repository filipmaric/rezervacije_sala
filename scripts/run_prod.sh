#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Production-style defaults for deployment under /rezervacije.
export APPLICATION_ROOT="${APPLICATION_ROOT:-/rezervacije}"
export STATIC_URL_PATH="${STATIC_URL_PATH:-/rezervacije/static}"
export AUTH_BACKEND="${AUTH_BACKEND:-radius}"

exec "$ROOT_DIR/venv/bin/python" app.py
