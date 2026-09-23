"""Login, logout and static delivery of the editor build. Owner: lead."""

import hashlib
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_from_directory, session, url_for

from .. import auth
from ..db import get_db
from ..security import rate_limit

bp = Blueprint("core", __name__)

SITE_DIR = Path(__file__).resolve().parent.parent.parent / "site"


def muya_version():
    """Build hash written by tools/build-editor.mjs; part of every editor URL."""
    path = SITE_DIR / "muya" / "VERSION"
    return path.read_text().strip() if path.is_file() else "dev"


def asset_url(name):
    """/static/<name>?v=<content hash>, so a changed file is never served stale from a cache."""
    path = SITE_DIR / name
    if not path.is_file():
        return f"/static/{name}"
    return f"/static/{name}?v={hashlib.sha1(path.read_bytes()).hexdigest()[:10]}"


@bp.app_context_processor
def inject_assets():
    return {"asset_url": asset_url, "muya_url": f"/muya/{muya_version()}"}


def _safe_next(target):
    # Only paths on this site: an attacker's link must not bounce the owner elsewhere after login.
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


@bp.route("/login", methods=["GET", "POST"])
@auth.public
@rate_limit("login", 20, 60)
def login():
    error = None
    if request.method == "POST":
        if auth.check_login(get_db(), request.form.get("parool", ""), request.remote_addr or ""):
            share_id, guest_name = session.get("share_id"), session.get("guest_name")
            session.clear()
            session.permanent = True
            session["owner"] = True
            if share_id:
                session["share_id"], session["guest_name"] = share_id, guest_name
            return redirect(_safe_next(request.args.get("next")))
        error = "Vale parool."
    return render_template("login.html", error=error), (403 if error else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("core.login"))


@bp.get("/muya/<version>/<path:name>")
@auth.public
def muya(version, name):
    """Editor files. The URL carries the build hash, so they are cached for good; the build also
    wrote a .gz copy of every text file, sent when the browser accepts gzip."""
    folder = SITE_DIR / "muya"
    current = version == muya_version()
    gz = folder / f"{name}.gz"
    if "gzip" in request.headers.get("Accept-Encoding", "") and gz.is_file():
        response = send_from_directory(folder, f"{name}.gz", max_age=0)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
        guessed = __import__("mimetypes").guess_type(name)[0]
        if name.endswith((".js", ".mjs")):
            guessed = "text/javascript"
        response.mimetype = guessed or "application/octet-stream"
    else:
        if not (folder / name).is_file():
            abort(404)
        response = send_from_directory(folder, name, max_age=0)
        if name.endswith(".mjs"):
            response.mimetype = "text/javascript"
    if current:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/manifest.webmanifest")
@auth.public
def manifest():
    return send_from_directory(SITE_DIR, "manifest.webmanifest", mimetype="application/manifest+json")
