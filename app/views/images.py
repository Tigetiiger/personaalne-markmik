"""Image upload and serving (/pildid) and the gallery list.

Owner: WP-A (editor + collab). Contract: docs/CONTRACTS.md §5. A guest sees only the pictures
their shared note shows, and may upload with an edit share."""

from flask import Blueprint, abort, g, request, send_from_directory, session

from .. import auth, images
from ..db import get_db, tx
from ..security import rate_limit

bp = Blueprint("images", __name__)

KEEP_UPLOADS = 20  # a guest's own recent uploads, visible before the note is saved with them
SHARE_QUOTA = 50 * 1024 * 1024  # bytes one share link may upload in all
# Opened directly, an SVG is its own document: no scripts, nothing fetched, only its own styles.
SVG_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"


@bp.post("/pildid")
@auth.guest("edit")
@rate_limit("pildid", 60, 60)
def upload():
    files = request.files.getlist("file[]")
    conn = get_db()
    guest = not g.owner
    if guest:
        auth.require_note(g.share["note_id"], "edit")
        if len(files) != 1:
            return {"error": "Laadi üles üks pilt korraga."}, 400
    # A guest's uploads are counted in settings ("upload_bytes:share:<id>"), so a reload or a new
    # browser does not reset them; only a picture actually stored counts.
    key = f"upload_bytes:share:{g.share['id']}" if guest else None
    succ, err = {}, []
    for f in files:
        original = (f.filename or "")[:200]
        data = f.read()
        with tx(conn):
            if guest:
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                used = int(row["value"]) if row else 0
                if used + len(data) > SHARE_QUOTA:
                    return {"error": "Selle lingi kaudu ei saa rohkem pilte lisada."}, 413
            src = images.store(conn, data, original, g.actor["name"])
            if src is not None and guest:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, str(used + len(data))),
                )
        if src is None:
            err.append(original)
            continue
        succ[original] = src
        if guest:
            session["uploads"] = ([src] + [u for u in session.get("uploads", []) if u != src])[:KEEP_UPLOADS]
    # Muya's (Vditor's) upload response shape; tools/editor/editor.js reads succMap.
    return {"code": 0, "msg": "", "data": {"errFiles": err, "succMap": succ}}


@bp.get("/pildid/<name>")
@auth.guest("view")
def image(name):
    if not images.NAME.match(name):
        abort(404)
    if not g.owner:
        note_id = g.share["note_id"]
        auth.require_note(note_id, "view")
        if images.url(name) not in session.get("uploads", []) and not images.referenced(get_db(), note_id, name):
            abort(403)
    response = send_from_directory(images.folder(), name)
    if name.endswith(".svg"):
        # Inside <img> nothing runs anyway; opened as a page it is an opaque origin.
        response.headers["Content-Security-Policy"] = SVG_CSP
    return response


@bp.get("/api/images")
def listing():
    return images.listing(get_db())
