"""Meedia (docs/MEEDIA-PLAN.md): the page, search, one file's details and tags, the files and
their thumbnails, and chunked upload into the media folder (call 8). Owner only.

An upload is started with its name and size, sent in pieces of at most CHUNK bytes (each under
Cloudflare's 100 MB request cap and the app's MAX_CONTENT_LENGTH), resumed from what the server
already has, and finished once all bytes are there. Until then it is a .part file in .saabumas,
never in the media folder itself.

Checksums are optional: a piece's `X-SHA256` header and the finished file's `sha256` are checked
when sent. A browser on the plain-http Tailscale address has no crypto.subtle to make them."""

import hashlib
import json
import re
import secrets
import shutil

from flask import Blueprint, abort, current_app, render_template, request, send_file

from ..db import get_db
from ..meedia import index, probe, search, shares, store, thumbs
from ..security import public_base

bp = Blueprint("meedia", __name__)

CHUNK = 8 * 1024 * 1024
DISK_RESERVE = 1024 ** 3  # an upload must leave this much of the disk free
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
PAGE = 120


def root():
    return store.default_root()


def _row(name):
    row = get_db().execute("SELECT * FROM media WHERE name = ?", (name,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.get("/meedia")
def page():
    return render_template("meedia.html")


@bp.get("/api/meedia")
def find():
    a = request.args
    try:
        return search.find(
            get_db(), q=a.get("q", ""), tags=a.getlist("tag"), not_tags=a.getlist("ilma"),
            kind=a.get("liik"), since=a.get("alates") or None, until=a.get("kuni") or None,
            untagged=a.get("sildita") == "1", shared=a.get("jagatud") == "1",
            offset=max(0, a.get("offset", 0, type=int)), limit=min(PAGE, max(1, a.get("limit", PAGE, type=int))))
    except ValueError:
        return {"error": "Vale kuupäev."}, 400


@bp.get("/api/meedia/sildid")
def tags():
    return {"tags": search.known_tags(get_db())}


@bp.get("/api/meedia/ootel")
def waiting():
    """Files in the folder not kept yet: being converted (or waiting for it), or refused."""
    rows = get_db().execute("SELECT name, error FROM media_pending ORDER BY name").fetchall()
    return {"items": [{"name": r["name"], "kind": store.kind_of(r["name"]), "error": r["error"]} for r in rows]}


@bp.get("/api/meedia/fail/<name>")
def detail(name):
    return search.item(get_db(), _row(name), full=True)


@bp.patch("/api/meedia/fail/<name>")
def edit(name):
    row = _row(name)
    body = request.get_json(silent=True) or {}
    conn = get_db()
    changes = {k: str(body[k]).strip()[:2000] for k in ("title", "description") if k in body}
    if changes:
        index.update(conn, root(), name, changes)
    if isinstance(body.get("tags"), list):
        index.set_tags(conn, root(), name, [str(t) for t in body["tags"]][:100])
    return search.item(conn, _row(row["name"]), full=True)


@bp.delete("/api/meedia/fail/<name>")
def delete(name):
    conn = get_db()
    known = conn.execute("SELECT 1 FROM media WHERE name = ? UNION SELECT 1 FROM media_pending WHERE name = ?",
                         (name, name)).fetchone()
    if known is None:
        abort(404)
    index.remove(conn, root(), name)
    return {"ok": True}


@bp.post("/api/meedia/mitu")
def bulk():
    """{names, add: [tag], remove: [tag], delete: bool} for a selection."""
    body = request.get_json(silent=True) or {}
    names = [str(n) for n in body.get("names") or []][:1000]
    add = store.normalize_tags(body.get("add") or [])
    drop = set(store.normalize_tags(body.get("remove") or []))
    conn = get_db()
    done = 0
    for name in names:
        row = conn.execute("SELECT id FROM media WHERE name = ?", (name,)).fetchone()
        if row is None:
            continue
        if body.get("delete"):
            index.remove(conn, root(), name)
        else:
            shown = [r[0] for r in conn.execute("SELECT tag FROM media_tags WHERE media_id = ? ORDER BY auto, tag", (row["id"],))]
            index.set_tags(conn, root(), name, [t for t in shown if t not in drop] + add)
        done += 1
    return {"done": done}


def _share_out(row, token=None):
    out = {k: row[k] for k in ("id", "title", "download", "created_at", "expires_at")}
    out["download"] = bool(out["download"])
    if "files" in row.keys():
        out.update(files=row["files"], first=row["first"])
    if token:  # only when made: the database keeps no way back to the URL
        base = current_app.config.get("MEEDIA_PUBLIC_URL") or public_base()
        out["url"] = f"{base}/s/{token}"
    return out


@bp.get("/api/meedia/jagamised")
def share_list():
    return {"shares": [_share_out(r) for r in shares.list_open(get_db(), request.args.get("name"))]}


@bp.post("/api/meedia/jagamised")
def share_create():
    body = request.get_json(silent=True) or {}
    days = body.get("days")
    try:
        row, token = shares.create(get_db(), [str(n) for n in body.get("names") or []][:shares.MAX_ITEMS + 1],
                                   title=str(body.get("title") or ""), days=days if isinstance(days, int) else None,
                                   download=bool(body.get("download")))
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return _share_out(row, token), 201


@bp.delete("/api/meedia/jagamised/<int:share_id>")
def share_revoke(share_id):
    if not shares.revoke(get_db(), share_id):
        abort(404)
    return {"ok": True}


@bp.get("/meedia/fail/<name>")
def file(name):
    row = _row(name)
    # conditional: Range requests, so a video can be seeked without loading it whole.
    return send_file(root() / row["name"], mimetype=row["mime"], conditional=True,
                     as_attachment=request.args.get("lae") == "1", download_name=row["name"])


@bp.get("/meedia/pisi/<name>")
def thumbnail(name):
    row = _row(name)
    size = request.args.get("s", thumbs.SIZES[0], type=int)
    if size not in thumbs.SIZES:
        abort(404)
    try:
        path = thumbs.get(root(), row, size)
    except probe.Unreadable:
        abort(404)
    response = send_file(path, mimetype="image/jpeg", conditional=True, max_age=86400)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


def _paths(upload_id):
    if not UPLOAD_ID.match(upload_id):
        return None
    folder = root() / store.INCOMING
    part, meta = folder / f"{upload_id}.part", folder / f"{upload_id}.upload.json"
    return (part, meta) if part.exists() and meta.exists() else None


@bp.post("/api/meedia/upload")
def upload_start():
    body = request.get_json(silent=True) or {}
    name = store.clean_name(str(body.get("name") or ""))
    size = body.get("size")
    if not store.kind_of(name):
        return {"error": "Seda failitüüpi ei saa üles laadida."}, 400
    if not isinstance(size, int) or size <= 0:
        return {"error": "Fail on tühi."}, 400
    store.ensure(root())
    if shutil.disk_usage(root()).free - size < DISK_RESERVE:
        return {"error": "Kettal pole selle faili jaoks ruumi."}, 507
    upload_id = secrets.token_hex(16)
    folder = root() / store.INCOMING
    (folder / f"{upload_id}.upload.json").write_text(json.dumps({"name": name, "size": size}))
    (folder / f"{upload_id}.part").touch()
    return {"id": upload_id, "chunk": CHUNK, "received": 0}


@bp.get("/api/meedia/upload/<upload_id>")
def upload_status(upload_id):
    paths = _paths(upload_id)
    if paths is None:
        return {"error": "Üleslaadimist pole."}, 404
    return {"received": paths[0].stat().st_size}


@bp.put("/api/meedia/upload/<upload_id>")
def upload_chunk(upload_id):
    paths = _paths(upload_id)
    if paths is None:
        return {"error": "Üleslaadimist pole."}, 404
    part, meta = paths
    size = json.loads(meta.read_text())["size"]
    received = part.stat().st_size
    if request.args.get("offset", type=int) != received:
        return {"error": "Vale koht failis.", "received": received}, 409
    data = request.get_data(cache=False)
    if len(data) > CHUNK or received + len(data) > size:
        return {"error": "Tükk on liiga suur."}, 400
    sent = request.headers.get("X-SHA256")
    if sent and sent.lower() != hashlib.sha256(data).hexdigest():
        return {"error": "Tükk muutus teel.", "received": received}, 400
    with open(part, "ab") as f:
        f.write(data)
    return {"received": received + len(data)}


@bp.post("/api/meedia/upload/<upload_id>/done")
def upload_done(upload_id):
    paths = _paths(upload_id)
    if paths is None:
        return {"error": "Üleslaadimist pole."}, 404
    part, meta = paths
    want = json.loads(meta.read_text())
    if part.stat().st_size != want["size"]:
        return {"error": "Fail pole veel kõik kohal.", "received": part.stat().st_size}, 409
    sha = store.sha256_of(part)
    sent = (request.get_json(silent=True) or {}).get("sha256")
    if sent and str(sent).lower() != sha:
        _drop(part, meta)
        return {"error": "Fail muutus teel. Laadi see uuesti üles."}, 400
    conn = get_db()
    same = index.find_duplicate(conn, sha)
    if same:
        _drop(part, meta)
        return {"name": same, "status": "ready", "duplicate": True}
    name = want["name"]
    try:
        if store.kind_of(name) == "video" and probe.target_suffix(name, probe.meta(part, "video")):
            # Converting takes as long as the video: the worker's converter does it.
            final = store.free_name(root(), name)
            part.replace(root() / final)
            meta.unlink()
            index.expect(conn, root(), final, source="upload")
            return {"name": final, "status": "processing"}, 202
        final = index.intake(conn, root(), name, source="upload", path=part)
    except probe.Unreadable as exc:
        _drop(part, meta)
        return {"error": str(exc)}, 400
    meta.unlink()
    return {"name": final, "status": "ready"}


def _drop(part, meta):
    part.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)
