"""Application factory for the classroom reservation system."""

import logging
import os

from flask import Flask
from logging.handlers import RotatingFileHandler

from config import (
    APPLICATION_ROOT,
    IS_PRODUCTION,
    LOG_FILE,
    SECRET_KEY,
)
from config import (
    SERVICE_API_KEY,
    STATIC_URL_PATH,
    STUDENT_AUTH_BACKEND,
    STUDENT_RADIUS_DICTIONARY,
    STUDENT_RADIUS_SECRET,
    STUDENT_RADIUS_SERVER,
    TEACHER_AUTH_BACKEND,
    TEACHER_RADIUS_DICTIONARY,
    TEACHER_RADIUS_SECRET,
    TEACHER_RADIUS_SERVER,
)
from db import init_app as init_db_app


def create_app():
    """Create and configure the Flask application instance."""
    app = Flask(__name__, static_url_path=STATIC_URL_PATH)
    app.config["APPLICATION_ROOT"] = APPLICATION_ROOT
    app.config["SERVICE_API_KEY"] = SERVICE_API_KEY
    app.secret_key = SECRET_KEY
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    init_db_app(app)

    if IS_PRODUCTION:
        if TEACHER_AUTH_BACKEND != "radius":
            raise RuntimeError("TEACHER_AUTH_BACKEND must be radius when APP_ENV=production")
        if STUDENT_AUTH_BACKEND != "radius":
            raise RuntimeError("STUDENT_AUTH_BACKEND must be radius when APP_ENV=production")
        for name in (
            "TEACHER_RADIUS_SERVER",
            "TEACHER_RADIUS_SECRET",
            "TEACHER_RADIUS_DICTIONARY",
            "STUDENT_RADIUS_SERVER",
            "STUDENT_RADIUS_SECRET",
            "STUDENT_RADIUS_DICTIONARY",
        ):
            if not os.getenv(name):
                raise RuntimeError(f"{name} must be set when APP_ENV=production")

    try:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1000000, backupCount=3)
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    except OSError:
        # Keep the app usable when the log path is not writable locally.
        pass

    return app
