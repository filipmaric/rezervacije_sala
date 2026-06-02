"""Main page blueprint for the classroom reservation app."""

from flask import Blueprint, render_template


bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Render the main timetable page."""
    return render_template("index.html")
