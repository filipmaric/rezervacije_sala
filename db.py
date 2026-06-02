"""SQLite database helpers for the classroom reservation app."""

import os
import sqlite3
import sys

from flask import g

import config


def _app_module():
    """Return the loaded app module when available."""
    return sys.modules.get("app") or sys.modules.get("__main__")


def _database_path():
    """Resolve the active database path from the app module or config defaults."""
    core = _app_module()
    if core is not None and hasattr(core, "DATABASE"):
        return core.DATABASE
    return config.DATABASE


def _schema_path():
    """Resolve the active schema path from the app module or config defaults."""
    core = _app_module()
    if core is not None and hasattr(core, "SCHEMA_FILE"):
        return core.SCHEMA_FILE
    return config.SCHEMA_FILE


def _database_has_schema(conn):
    """Check whether the main application schema is already present."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
    ).fetchone()
    return bool(row)


def _ensure_attendance_schema(conn):
    """Create the attendance tables if they are missing."""
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


def init_app(app):
    """Register database teardown hooks on the Flask application."""
    app.teardown_appcontext(close_connection)


def init_db(conn=None):
    """Initialize the database from schema.sql and add attendance tables."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(_database_path(), detect_types=sqlite3.PARSE_DECLTYPES)
        close_conn = True

    try:
        if _database_has_schema(conn):
            return False

        schema_path = _schema_path()
        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path, encoding="utf-8") as f:
            conn.executescript(f.read())
        _ensure_attendance_schema(conn)
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def get_db():
    """Return the current SQLite connection, creating one for this request if needed."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(_database_path(), detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        # enable WAL mode for concurrent access (Gunicorn)
        db.execute("PRAGMA journal_mode=WAL;")
        # ensure foreign keys are enforced
        db.execute("PRAGMA foreign_keys = ON;")
        init_db(db)
        _ensure_attendance_schema(db)
    return db


def close_connection(exception):
    """Close the request-scoped SQLite connection at teardown."""
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    """Run a SELECT query and return either one row or all rows."""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=(), commit=True):
    """Run a write query and optionally commit it immediately."""
    conn = get_db()
    cur = conn.execute(query, args)
    if commit:
        conn.commit()
    return cur.lastrowid
