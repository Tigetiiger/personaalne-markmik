"""Pages of the app shell (/, /m/<slug>, /tana), quick capture (/api/capture) and the tag list
the sidebar's Sildid section needs (/api/tags).

Owner: WP-C (app shell); see docs/CONTRACTS.md §5 row C."""

import json

from flask import Blueprint, jsonify, render_template, request

from .. import capture as quick
from .. import notes
from ..db import get_db

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return render_template("note.html", slug="")


@bp.get("/m/<slug>")
def note(slug):
    # Any slug renders: the page itself says "Märget ei leitud" once the API answers 404.
    return render_template("note.html", slug=slug)


@bp.get("/tana")
def today():
    return render_template("today.html")


@bp.post("/api/capture")
def capture():
    """{text, preview?} -> what the text becomes (app/capture.py); preview creates nothing, so the
    palette can show "Lisa: ..." while the user types."""
    data = request.get_json(silent=True)
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return {"error": "Kirjuta midagi."}, 400
    if len(text) > quick.MAX_TEXT:
        return {"error": f"Liiga pikk (üle {quick.MAX_TEXT} märgi)."}, 400
    try:
        return quick.capture(get_db(), text, preview=data.get("preview") is True)
    except notes.NoteBusy:
        return {"error": "Märget muudetakse teises aknas."}, 409
    except ValueError as exc:
        return {"error": str(exc)}, 400


@bp.get("/api/tags")
def tags():
    """[{tag, notes: [{slug, title}]}] by tag name. /api/notes/tree carries no tags."""
    by_tag = {}
    rows = get_db().execute(
        "SELECT slug, title, tags_json FROM notes WHERE deleted_at IS NULL AND tags_json != '[]' "
        "ORDER BY title COLLATE NOCASE")
    for row in rows:
        for tag in json.loads(row["tags_json"]):
            by_tag.setdefault(tag, []).append({"slug": row["slug"], "title": row["title"]})
    return jsonify([{"tag": t, "notes": by_tag[t]} for t in sorted(by_tag, key=str.casefold)])
