"""Image upload and serving (/pildid) and the gallery list.

Owner: WP-A (editor + collab). Contract: docs/CONTRACTS.md §5. A guest sees only the pictures
their shared note shows, and may upload with an edit share."""

from flask import Blueprint, abort, g, request, send_from_directory, session

from .. import auth, images
from ..db import get_db
from ..security import rate_limit

bp = Blueprint("images", __name__)

KEEP_UPLOADS = 20  # a guest's own recent uploads, visible before the note is saved with them


@bp.post("/pildid")
@auth.guest("edit")
@rate_limit("pildid", 60, 60)
def upload():
    if not g.owner:
        auth.require_note(g.share["note_id"], "edit")
    conn = get_db()
    succ, err = {}, []
    for f in request.files.getlist("file[]"):
        original = f.filename or ""
        src = images.store(conn, f.read(), original, g.actor["name"])
        if src is None:
            err.append(original)
            continue
        succ[original] = src
        if not g.owner:
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
        response.headers["Content-Security-Policy"] = "sandbox"
    return response


@bp.get("/api/images")
def listing():
    return images.listing(get_db())
