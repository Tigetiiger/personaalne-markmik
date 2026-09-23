"""Pages of the app shell: /, /m/<slug>, /tana.

Owner: WP-C (app shell). Wave 0 serves the bare shell so every other package has somewhere to
mount; see docs/CONTRACTS.md."""

from flask import Blueprint, render_template

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return render_template("shell.html")
