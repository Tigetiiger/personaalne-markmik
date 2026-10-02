"""Meedia: chunked upload into the media folder (docs/MEEDIA-PLAN.md, call 8). Owner only.

An upload is started with its name and size, sent in pieces of at most CHUNK bytes (each under
Cloudflare's 100 MB request cap and the app's MAX_CONTENT_LENGTH), resumed from what the server
already has, and finished with the whole file's sha256. Until then it is a .part file in
.saabumas, never in the media folder itself."""

import json
import re
import secrets
import shutil

from flask import Blueprint, request

from ..db import get_db
from ..meedia import index, probe, store

bp = Blueprint("meedia", __name__)

CHUNK = 8 * 1024 * 1024
DISK_RESERVE = 1024 ** 3  # an upload must leave this much of the disk free
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


def root():
    return store.default_root()


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
    if sha != str((request.get_json(silent=True) or {}).get("sha256", "")).lower():
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
