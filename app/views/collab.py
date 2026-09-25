"""Live co-editing endpoints (/api/collab/...), shared by the owner and share-link guests.

Owner: WP-A (editor + collab). Contract: docs/CONTRACTS.md §5. A guest reaches only the session
of the note their share covers; a view share may follow along but not send ops or save."""

import hashlib
import json
import logging
import os

from flask import Blueprint, abort, g, render_template, request, session

from .. import auth, collab, notes
from ..db import get_db
from ..security import rate_limit

log = logging.getLogger(__name__)

bp = Blueprint("collab", __name__)

MAX_BODY = 500_000  # characters; Python-Markdown is quadratic on one giant block, and notes are small
MAX_OFFSET = 10_000_000  # a caret offset beyond any note


def _not_json_constant(name):
    raise ValueError(f"{name} is not JSON")


def _json_object():
    """Request body as a JSON object of plain JSON: NaN and Infinity (which Python's json takes)
    are refused, or a stored op would be answered to everyone as invalid JSON."""
    raw = request.get_data(cache=True)
    if not raw:
        return {}
    try:
        body = json.loads(raw, parse_constant=_not_json_constant)
    except (ValueError, RecursionError):
        abort(400)
    if not isinstance(body, dict):
        abort(400)
    return body


def _client(body):
    """The editor's id: one browser tab, as "<browser key>:<tab id>". The key comes from this
    browser's CSRF token (every POST here has passed the check against it), so a tab can only
    be spoken for (synced, left) by the browser that has it, and nothing is written into the
    session: a concurrent response would put back a cookie without it. The same person may have
    two tabs open."""
    tab = str(body.get("client", ""))[:64]
    if not tab:
        abort(400)
    key = hashlib.sha256(session["csrf"].encode()).hexdigest()[:16]
    return f"{key}:{tab}", tab


def _actor():
    """Name for participants, carets and op authors. A guest picks their own name: marked, so
    nobody can pass for the owner."""
    if g.owner:
        return {**g.actor, "role": "owner", "share": None}
    return {**g.actor, "name": f"{g.actor['name']} (külaline)", "role": g.share["mode"], "share": g.share["id"]}


def _int(value):
    if isinstance(value, bool):
        abort(400)
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
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
        start, end = int(value["start"]), int(value["end"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (0 <= start <= MAX_OFFSET and 0 <= end <= MAX_OFFSET):
        return None
    return {"path": path, "start": start, "end": end}


def _session(session_id, mode):
    row = collab.get(get_db(), session_id)
    if row is None:
        abort(404)
    auth.require_note(row["note_id"], mode)
    return row


def _mine(data, client, tab):
    """Ops name their sender only to the sender itself (that is how it knows its op was taken)."""
    for op in data.get("ops", []):
        op["client"] = tab if op["client"] == client else ""
    return data


TOO_MANY = {"error": "Liiga palju avatud aknaid."}, 429


@bp.post("/api/collab/join")
@auth.guest("view")
@rate_limit("collab-join", 60, 60)
def join():
    body = _json_object()
    client, tab = _client(body)
    conn = get_db()
    note = notes.by_slug(conn, str(body.get("slug", "")))
    # A guest learns nothing about notes outside their share, not even whether they exist.
    if note is None or (not g.owner and g.share["note_id"] != note["id"]):
        return {"error": "Märget ei leitud."}, 404
    auth.require_note(note["id"], "view")
    extra = {"guest": not g.owner, "note": note["id"]}
    kept = body.get("copies")  # changes earlier tabs of this browser kept (collab.copy_state)
    if isinstance(kept, list):
        browser = client.split(":", 1)[0]
        extra["copies"] = [
            collab.copy_state(conn, note["id"], f"{browser}:{c['tab'][:64]}", c["op_id"][:64])
            if isinstance(c, dict) and isinstance(c.get("tab"), str) and isinstance(c.get("op_id"), str) and c["op_id"]
            else {"first": False, "all": False}
            for c in kept[:20]
        ]
    try:
        data = collab.join(conn, note["id"], client, _actor())
    except collab.TooManyClients:
        return TOO_MANY
    return {**_mine(data, client, tab), **extra}


@bp.post("/api/collab/<int:session_id>/sync")
@auth.guest("view")
@rate_limit("collab-sync", 1200, 60)
def sync(session_id):
    body = _json_object()
    client, tab = _client(body)
    op = body.get("op")
    if op is not None and not (isinstance(body.get("op_id"), str) and body["op_id"]):
        abort(400)  # an operation without its id would be swallowed as a resend
    _session(session_id, "view" if op is None else "edit")
    base = None if body.get("base") is None else _int(body["base"])
    # A view-only guest has no caret to show, and must not fill everyone's answer with one.
    can_edit = g.owner or g.share["mode"] == "edit"
    cursor = _cursor(body.get("cursor")) if can_edit else None
    try:
        data = collab.sync(
            get_db(), session_id, client, _actor(), _int(body.get("seen", 0)),
            op, body.get("op_id", "")[:64] if op is not None else "", base, cursor,
        )
    except collab.BadOp:
        abort(400)
    except collab.OpTooLarge:
        return {"error": "Muudatus on liiga suur."}, 413
    except collab.TooManyClients:
        return TOO_MANY
    return _mine(data, client, tab)


@bp.post("/api/collab/<int:session_id>/save")
@auth.guest("edit")
@rate_limit("collab-save", 120, 60)
def save(session_id):
    body = _json_object()
    text = body.get("body")
    if not isinstance(text, str):
        abort(400)
    if len(text) > MAX_BODY:
        return {"error": "Märge on liiga pikk."}, 413
    seq = _int(body.get("seq"))
    _session(session_id, "edit")
    # The last save of a tab going away may carry its op still on the way (collab.save).
    op, op_id, client = body.get("op"), "", None
    if op is not None:
        if not (isinstance(body.get("op_id"), str) and body["op_id"]) or seq < 1:
            abort(400)
        op_id, client = body["op_id"][:64], _client(body)[0]
    try:
        ok = collab.save(get_db(), session_id, text.replace("\r\n", "\n"), seq, _actor(),
                         client=client, op=op, op_id=op_id, final=body.get("final") is True)
    except collab.BadOp:
        abort(400)
    except collab.OpTooLarge:
        return {"error": "Muudatus on liiga suur."}, 413
    if not ok:
        return {"reset": True}, 409
    return "", 204


@bp.post("/api/collab/<int:session_id>/leave")
@auth.guest("view")
def leave(session_id):
    """Sent as a beacon when the tab goes away. With `error`, the client could not apply an
    operation: the session is closed so every editor rejoins from the saved text."""
    body = _json_object()
    client, _ = _client(body)
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


@auth.public
def _test_guest(token):
    # Stands in for /j/<token> (WP-G): this browser becomes a guest of that share.
    conn = get_db()
    share = auth.enter_share(conn, token, "Mari")
    if share is None:
        abort(404)
    auth.load_actor()
    return render_template("_test_editor.html", slug=notes.get(conn, share["note_id"])["slug"], script="harness.js")


@bp.record
def _test_pages(state):
    # Harness pages for the browser tests (tests/visual/editor-*); never in production.
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/editor/<slug>", "collab.test_editor", _test_editor)
        state.app.add_url_rule("/_test/editor-fuzz", "collab.test_fuzz", _test_fuzz)
        state.app.add_url_rule("/_test/guest/<token>", "collab.test_guest", _test_guest)
