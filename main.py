"""Main page blueprint for the classroom reservation app."""

from flask import Blueprint, jsonify, render_template


bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Render the main timetable page."""
    return render_template("index.html")


@bp.route("/healthz")
def healthz():
    """Expose a lightweight health check for the web frontend."""
    return jsonify({"ok": True})
