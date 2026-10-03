"""Templates: the defaults for a new note and making it (docs/ARVUTUS-PLAN.md). Owner only."""

from flask import Blueprint, jsonify, request

from .. import mallid, notes
from ..db import get_db
from .notes_api import note_json

bp = Blueprint("mallid", __name__)


def _template(conn, slug):
    note = notes.by_slug(conn, slug) if isinstance(slug, str) else None
    if note is None or not mallid.is_template(note):
        return None
    return note


@bp.get("/api/mallid/<slug>")
def template_defaults(slug):
    """{title, folder}: what the "Tee märge sellest mallist" dialog starts with."""
    template = _template(get_db(), slug)
    if template is None:
        return jsonify({"error": "Malli ei leitud."}), 404
    return jsonify(mallid.defaults(template))


@bp.post("/api/notes/from-template")
def from_template():
    """{template, title?, folder?} -> the new note (201)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Vigane päring."}), 400
    conn = get_db()
    template = _template(conn, data.get("template"))
    if template is None:
        return jsonify({"error": "Malli ei leitud."}), 404
    title, folder = data.get("title"), data.get("folder")
    if not isinstance(title, (str, type(None))) or not isinstance(folder, (str, type(None))):
        return jsonify({"error": "Vigased andmed."}), 400
    try:
        note = mallid.make(conn, template, (title or "")[:200], folder)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(note_json(note)), 201
