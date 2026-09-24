"""Seaded: the sections other packages register in the browser (registerSettingsSection), the
link to Ühendused and the owner's password change.

Owner: WP-E2 (Postkast + Ühendused); see docs/CONTRACTS.md."""

from flask import Blueprint, jsonify, render_template, request

from .. import auth
from ..db import get_db

bp = Blueprint("settings", __name__)


@bp.get("/seaded")
def page():
    return render_template("settings.html")


@bp.post("/api/settings/password")
def change_password():
    body = request.get_json(silent=True)
    old = body.get("old") if isinstance(body, dict) else None
    new = body.get("new") if isinstance(body, dict) else None
    if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
        return jsonify(error="Sisesta vana ja uus parool."), 400
    conn = get_db()
    # check_login also counts failures, so this form cannot be used to guess the password.
    if not auth.check_login(conn, old, request.remote_addr or ""):
        return jsonify(error="Vana parool on vale."), 400
    try:
        auth.set_password(conn, new)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return "", 204
