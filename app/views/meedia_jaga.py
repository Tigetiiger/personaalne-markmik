"""Meedia's public share pages (docs/MEEDIA-PLAN.md, calls 19-23): /s/<token> and its files.
Served only on MEEDIA_PUBLIC_URL's host when that is set (security.check_host). No session, no
script: a small page with Open Graph tags, so Discord shows the picture or plays the video.

An unknown, revoked or expired token and a file outside the link all get the same 404."""

from urllib.parse import quote

from flask import Blueprint, abort, current_app, render_template, request, send_file

from .. import auth
from ..db import get_db
from ..meedia import probe, shares, store, thumbs
from ..security import MEDIA_SHARES, rate_limit

bp = Blueprint(MEDIA_SHARES, __name__)

# A video plays as many Range requests (every seek is one), so the limit is generous.
LIMIT = (600, 60)
# A revoked link stops working within this long even where Cloudflare or a browser kept a copy.
CACHE = "private, max-age=300"


def _share(token):
    share = shares.by_token(get_db(), token)
    if share is None:
        abort(404)
    return share


def _item(share, name):
    row = shares.item(get_db(), share["id"], name)
    if row is None:
        abort(404)
    return row


def _base():
    return current_app.config.get("MEEDIA_PUBLIC_URL") or request.host_url.rstrip("/")


@bp.get("/s/<token>")
@auth.public
@rate_limit("meedia-jaga", *LIMIT)
def page(token):
    share = _share(token)
    rows = shares.items(get_db(), share["id"])
    if not rows:
        abort(404)
    root = f"{_base()}/s/{token}"
    files = [{
        "name": r["name"], "kind": r["kind"], "mime": r["mime"], "width": r["width"], "height": r["height"],
        "title": r["title"], "description": r["description"],
        "file": f"{root}/f/{quote(r['name'])}", "preview": f"{root}/p/{quote(r['name'])}?s=1600",
        "download": f"{root}/f/{quote(r['name'])}?lae=1" if share["download"] else None,
    } for r in rows]
    first = files[0]
    title = share["title"] or (first["title"] or first["name"] if len(files) == 1 else f"{len(files)} faili")
    response = current_app.make_response(render_template(
        "meedia_jaga.html", title=title, files=files, first=first, url=root,
        description=first["description"] if len(files) == 1 else ""))
    response.headers["Cache-Control"] = CACHE
    return response


@bp.get("/s/<token>/f/<name>")
@auth.public
@rate_limit("meedia-jaga", *LIMIT)
def file(token, name):
    share = _share(token)
    row = _item(share, name)
    # Without "download" a picture is seen as its preview only; a video has to be streamed whole.
    if row["kind"] == "image" and not share["download"]:
        abort(404)
    response = send_file(store.default_root() / row["name"], mimetype=row["mime"], conditional=True,
                         as_attachment=bool(share["download"]) and request.args.get("lae") == "1",
                         download_name=row["name"])
    response.headers["Cache-Control"] = CACHE
    return response


@bp.get("/s/<token>/p/<name>")
@auth.public
@rate_limit("meedia-jaga", *LIMIT)
def preview(token, name):
    row = _item(_share(token), name)
    size = request.args.get("s", thumbs.SIZES[0], type=int)
    if size not in thumbs.SIZES:
        abort(404)
    try:
        path = thumbs.get(store.default_root(), row, size)
    except probe.Unreadable:
        abort(404)
    response = send_file(path, mimetype="image/jpeg", conditional=True)
    response.headers["Cache-Control"] = CACHE
    return response
