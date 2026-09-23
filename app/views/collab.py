"""Live co-editing endpoints (/api/collab/...), shared by the owner and share-link guests.

Owner: WP-A (editor + collab). Contract: docs/CONTRACTS.md §5. A guest reaches only the session
of the note their share covers; a view share may follow along but not send ops or save."""

import logging
import os

from flask import Blueprint, abort, g, render_template, request

from .. import auth, collab, notes
from ..db import get_db

log = logging.getLogger(__name__)

bp = Blueprint("collab", __name__)

MAX_BODY = 500_000  # characters; Python-Markdown is quadratic on one giant block, and notes are small


def _json_object():
    """Request body as a JSON object; anything else is a client error, not a 500."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(400)
    return body


def _client(body):
    """The editor's random id: one browser tab. The same person may have two tabs open."""
    client = str(body.get("client", ""))[:64]
    if not client:
        abort(400)
    return client


def _int(value):
    if isinstance(value, bool):
        abort(400)
    try:
        value = int(value)
    except (TypeError, ValueError):
        abort(400)
    if not 0 <= value <= 2**53:
        abort(400)
    return value


def _cursor(value):
    """A caret to show the others: {path: [..], start, end}; any other shape is dropped."""
    if not isinstance(value, dict) or not isinstance(value.get("path"), list) or len(value["path"]) > 32:
        return None
    path = [p[:64] if isinstance(p, str) else p for p in value["path"] if isinstance(p, (int, str)) and not isinstance(p, bool)]
    try:
        return {"path": path, "start": int(value["start"]), "end": int(value["end"])}
    except (KeyError, TypeError, ValueError):
        return None


def _session(session_id, mode):
    row = collab.get(get_db(), session_id)
    if row is None:
        abort(404)
    auth.require_note(row["note_id"], mode)
    return row


@bp.post("/api/collab/join")
@auth.guest("view")
def join():
    body = _json_object()
    client = _client(body)
    conn = get_db()
    note = notes.by_slug(conn, str(body.get("slug", "")))
    if note is None:
        return {"error": "Märget ei leitud."}, 404
    auth.require_note(note["id"], "view")
    return collab.join(conn, note["id"], client, g.actor)


@bp.post("/api/collab/<int:session_id>/sync")
@auth.guest("view")
def sync(session_id):
    body = _json_object()
    client = _client(body)
    op = body.get("op")
    if op is not None and not (isinstance(body.get("op_id"), str) and body["op_id"]):
        abort(400)  # an operation without its id would be swallowed as a resend
    _session(session_id, "view" if op is None else "edit")
    base = None if body.get("base") is None else _int(body["base"])
    try:
        return collab.sync(
            get_db(), session_id, client, g.actor, _int(body.get("seen", 0)),
            op, body.get("op_id", "")[:64] if op is not None else "", base, _cursor(body.get("cursor")),
        )
    except collab.OpTooLarge:
        return {"error": "Muudatus on liiga suur."}, 413


@bp.post("/api/collab/<int:session_id>/save")
@auth.guest("edit")
def save(session_id):
    body = _json_object()
    text = body.get("body")
    if not isinstance(text, str):
        abort(400)
    if len(text) > MAX_BODY:
        return {"error": "Märge on liiga pikk."}, 413
    seq = _int(body.get("seq"))
    _session(session_id, "edit")
    if not collab.save(get_db(), session_id, text.replace("\r\n", "\n"), seq, g.actor):
        return {"reset": True}, 409
    return "", 204


@bp.post("/api/collab/<int:session_id>/leave")
@auth.guest("view")
def leave(session_id):
    """Sent as a beacon when the tab goes away. With `error`, the client could not apply an
    operation: the session is closed so every editor rejoins from the saved text."""
    body = _json_object()
    client = _client(body)
    _session(session_id, "view")
    collab.leave(session_id, client)
    if body.get("error"):
        _session(session_id, "edit")
        log.warning("collab session %s closed, client %s failed: %.300s", session_id, client, body["error"])
        collab.close(get_db(), session_id)
    return "", 204


def _test_editor(slug):
    conn = get_db()
    note = notes.by_slug(conn, slug) or notes.create(conn, slug)
    return render_template("_test_editor.html", slug=note["slug"], script="harness.js")


def _test_fuzz():
    return render_template("_test_editor.html", slug="", script="fuzz.js")


@bp.record
def _test_pages(state):
    # Harness pages for the browser tests (tests/visual/editor-*); never in production.
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/editor/<slug>", "collab.test_editor", _test_editor)
        state.app.add_url_rule("/_test/editor-fuzz", "collab.test_fuzz", _test_fuzz)
