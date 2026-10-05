"""Data files for Python cells (docs/FAILID-PLAN.md): upload, fetch by web address, refresh, search
and download. Only the owner adds files (default deny); a share-link guest downloads the files
allowed for its note (failid.allowed), nothing else."""

from urllib.parse import quote

from flask import Blueprint, abort, g, request, send_from_directory

from .. import auth, failid, notes
from ..db import get_db
from ..security import rate_limit
from .images import SVG_CSP

bp = Blueprint("failid", __name__)


def _note_id(conn, slug):
    """(note id or None, error response or None) for an optional `slug`."""
    if not slug:
        return None, None
    note = notes.by_slug(conn, str(slug))
    if note is None:
        return None, ({"error": "Märget ei leitud."}, 404)
    return note["id"], None


@bp.post("/api/failid")
@rate_limit("failid", 60, 60)
def upload():
    """Multipart `file`, `slug?` (the note it goes into: its guests may then download it)."""
    f = request.files.get("file")
    if f is None:
        return {"error": "Faili pole."}, 400
    conn = get_db()
    note_id, error = _note_id(conn, request.form.get("slug"))
    if error:
        return error
    try:
        name = failid.store(conn, f.read(), (f.filename or "")[:200], failid.OWNER, note_id)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return failid.info(conn, name), 201


@bp.post("/api/failid/url")
@rate_limit("failid-url", 20, 60)
def from_url():
    """{url, slug?}: the app downloads the address once and keeps the copy."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("url"), str):
        return {"error": "Vigane päring."}, 400
    conn = get_db()
    note_id, error = _note_id(conn, body.get("slug"))
    if error:
        return error
    try:
        name = failid.fetch_url(conn, body["url"], note_id)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return failid.info(conn, name), 201


@bp.post("/api/failid/<name>/refresh")
@rate_limit("failid-url", 20, 60)
def refresh(name):
    """{slug?}: the address fetched again; new content is a new file (the note's links are moved to
    it by the browser)."""
    body = request.get_json(silent=True)
    conn = get_db()
    if not failid.NAME.match(name) or failid.info(conn, name) is None:
        return {"error": "Faili ei leitud."}, 404
    note_id, error = _note_id(conn, body.get("slug") if isinstance(body, dict) else None)
    if error:
        return error
    try:
        new = failid.refresh(conn, name, note_id)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return failid.info(conn, new)


@bp.get("/api/failid/<name>")
def file_info(name):
    """One stored file's JSON (the note's Failid tab: size, address, when fetched)."""
    found = failid.info(get_db(), name) if failid.NAME.match(name) else None
    if found is None:
        return {"error": "Faili ei leitud."}, 404
    return found


@bp.get("/api/failid")
def search():
    return failid.search(get_db(), request.args.get("q", "").strip())


@bp.get("/failid/<name>")
@auth.guest("view")
def download(name):
    if not failid.NAME.match(name):
        abort(404)
    conn = get_db()
    if not g.owner:
        note_id = g.share["note_id"]
        auth.require_note(note_id, "view")
        if not failid.allowed(conn, note_id, name):
            abort(404)
    row = conn.execute("SELECT original_name FROM files WHERE name = ?", (name,)).fetchone()
    response = send_from_directory(failid.folder(), name, mimetype="application/octet-stream")
    # Always a download, never a page of this origin: a CSV or JSON can hold HTML.
    filename = (row["original_name"] if row else "") or name
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = SVG_CSP
    if not g.owner:
        response.headers["Cache-Control"] = "no-store"  # the link may be revoked: nothing kept
    return response


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) -------------------------------------------

def _test_address():
    """{content}: what every web address "downloads" from now on (the test server is localhost,
    which the real fetch refuses)."""
    content = str((request.get_json(silent=True) or {}).get("content") or "")
    failid._download = lambda url: content.encode()
    return "", 204


@bp.record
def _test_pages(state):
    import os
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/failid/aadress", "failid.test_address", _test_address, methods=["POST"])
