"""Login, logout and static delivery of the editor build. Owner: lead."""

import json
import mimetypes
import os
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_from_directory, session, url_for

from .. import auth
from ..db import get_db
from ..security import rate_limit

bp = Blueprint("core", __name__)

SITE_DIR = Path(__file__).resolve().parent.parent.parent / "site"
DIST_DIR = SITE_DIR / "dist"
IMMUTABLE = "public, max-age=31536000, immutable"
# Pages that open the editor at once: its modules are preloaded with them.
EDITOR_PAGES = ("notes", "share-edit")


def muya_version():
    """Build hash written by tools/build-editor.mjs; part of every editor URL."""
    path = SITE_DIR / "muya" / "VERSION"
    return path.read_text().strip() if path.is_file() else "dev"


def built():
    """The manifest tools/build-app.mjs wrote, or {} before that build: the sources are then
    served as they are."""
    path = DIST_DIR / "manifest.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def asset_url(name):
    """The built, content-hashed copy of a file under /static/dist, cached for good. Unbuilt, the
    source at /static/<name>, revalidated on every use."""
    file = built().get("files", {}).get(name)
    return f"/static/dist/{file}" if file else f"/static/{name}"


def preloads(page):
    """URLs for <link rel="modulepreload">: every module the page will load at once, fetched in
    parallel from the start instead of one import level per round trip."""
    preload = built().get("preload", {})
    urls = [f"/static/dist/{file}" for file in preload.get("*", []) + preload.get(page, [])]
    if page in EDITOR_PAGES:
        # The editor imports /static/app.js (static_file below), the one URL that is revalidated.
        path = SITE_DIR / "muya" / "PRELOAD"
        editor = path.read_text().split() if path.is_file() else []
        urls += ["/static/app.js"] + [f"/muya/{muya_version()}/{file}" for file in editor]
    return urls


def prefetches(page):
    """URLs for <link rel="prefetch">: the other pages' lazy chunks (the calendar's, the graph's),
    fetched while the browser is idle, so that the first visit there after a deploy finds them
    cached as well."""
    manifest = built()
    now = set(manifest.get("preload", {}).get(page, []))
    return [f"/static/dist/{file}" for file in manifest.get("lazy", []) if file not in now]


@bp.app_context_processor
def inject_assets():
    return {"asset_url": asset_url, "preloads": preloads, "prefetches": prefetches, "muya_url": f"/muya/{muya_version()}"}


def _safe_next(target):
    # Only paths on this site: an attacker's link must not bounce the owner elsewhere after login.
    # Browsers drop tabs and line breaks and read "\" as "/", so "/<tab>/evil" is "//evil".
    if not target or any(c == "\\" or ord(c) < 32 or ord(c) == 127 for c in target):
        return "/"
    return target if target.startswith("/") and not target.startswith("//") else "/"


@bp.route("/login", methods=["GET", "POST"])
@auth.public
# Browser test runs log in many times a minute from one address; they raise the limit.
@rate_limit("login", int(os.environ.get("LOGIN_RATE_LIMIT", "20")), 60)
def login():
    error = None
    if request.method == "POST":
        if auth.check_login(get_db(), request.form.get("parool", ""), request.remote_addr or ""):
            share_id, guest_name = session.get("share_id"), session.get("guest_name")
            session.clear()
            session.permanent = True
            session["owner"] = True
            session["epoch"] = auth.owner_epoch(get_db())
            if share_id:
                session["share_id"], session["guest_name"] = share_id, guest_name
            return redirect(_safe_next(request.args.get("next")))
        error = "Vale parool."
    return render_template("login.html", error=error), (403 if error else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("core.login"))


def _send_built(folder, name, cache_control):
    """A file from a build, as the .gz copy the build wrote next to every text file when the
    browser accepts gzip."""
    gz = folder / f"{name}.gz"
    if "gzip" in request.headers.get("Accept-Encoding", "") and gz.is_file():
        response = send_from_directory(folder, f"{name}.gz", max_age=0)
        response.headers["Content-Encoding"] = "gzip"
        guessed = mimetypes.guess_type(name)[0]
        if name.endswith((".js", ".mjs")):
            guessed = "text/javascript"
        response.mimetype = guessed or "application/octet-stream"
    else:
        if not (folder / name).is_file():
            abort(404)
        response = send_from_directory(folder, name, max_age=0)
        if name.endswith(".mjs"):
            response.mimetype = "text/javascript"
    if gz.is_file():
        response.vary.add("Accept-Encoding")  # either answer: a cache must not hand one to the other
    response.headers["Cache-Control"] = cache_control
    return response


@bp.get("/muya/<version>/<path:name>")
@auth.public
def muya(version, name):
    """Editor files. The URL carries the build hash, so they are cached for good."""
    return _send_built(SITE_DIR / "muya", name, IMMUTABLE if version == muya_version() else "no-cache")


def static_file(filename):
    """/static/<filename>, the app's own browser files (the view of Flask's "static" endpoint,
    app/__init__.py). Built ones (dist/) carry their content hash in the name, so they are cached
    for good. A module the bundle holds (manifest "modules") is answered with a re-export of its
    built copy: the editor and the tests import it by its /static URL and get the bundle's one
    instance of it (named exports only). The rest as it is, revalidated."""
    manifest = built()
    if filename.startswith("dist/"):
        # Only what the build wrote under a content hash: not the manifest, not a stray file.
        name = filename.removeprefix("dist/")
        if name not in manifest.get("outputs", ()):
            abort(404)
        return _send_built(DIST_DIR, name, IMMUTABLE)
    if filename in manifest.get("modules", ()):
        response = current_app.response_class(f'export * from "/static/dist/{manifest["files"][filename]}";\n', mimetype="text/javascript")
        response.headers["Cache-Control"] = "no-cache"
        return response
    return send_from_directory(SITE_DIR, filename)


@bp.get("/manifest.webmanifest")
@auth.public
def manifest():
    return send_from_directory(SITE_DIR, "manifest.webmanifest", mimetype="application/manifest+json")
