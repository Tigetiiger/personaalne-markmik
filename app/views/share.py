"""Share links: /j/<token> view and edit pages, /api/share/current for the view page, and the
owner's /api/shares.

Owner: WP-G (sharing). Contract: docs/CONTRACTS.md §5 row G, docs/PLAN-OVERVIEW.md §10."""

import html
import re
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, redirect, render_template, request, session

from .. import auth, notes, shares
from ..db import get_db
from ..render import render_markdown
from ..security import public_base, rate_limit

bp = Blueprint("share", __name__)

NOT_FOUND = {"error": "Märget ei leitud."}, 404
# A browser holds one share link at a time: the last one opened.
OTHER_LINK = "Selles brauseris avati vahepeal teine jagatud link. Selle märkme juurde saad tagasi, kui avad selle lingi uuesti."


@bp.after_request
def _private(response):
    # The token is in the page's URL: never sent on as a referrer, never indexed.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


# nh3 writes every attribute double-quoted with '"' and '>' escaped, so these read its tags exactly.
_A_TAG = re.compile(r'<a((?:\s+[^\s"=>/]+(?:="[^"]*")?)*)\s*>')
_HREF = re.compile(r'\s+href="([^"]*)"')


def guest_html(md):
    """The note rendered for a guest: a link into the owner's other notes (/m/<slug>, or any
    link on this site) keeps its text but goes nowhere; the guest may open this note only."""
    def unlink(tag):
        def keep(href):
            url = urlsplit(html.unescape(href.group(1)))
            outside = url.scheme in ("http", "https") and url.netloc != request.host
            return href.group(0) if outside or url.scheme == "mailto" or href.group(1).startswith("#") else ""
        return f"<a{_HREF.sub(keep, tag.group(1))}>"
    return _A_TAG.sub(unlink, render_markdown(md))


def _missing():
    # One answer for unknown, revoked and expired links (and a deleted note): which it was is
    # nobody's business.
    return render_template("share_view.html", note=None), 404


@bp.route("/j/<token>", methods=["GET", "POST"])
@auth.public
@rate_limit("share", 30, 60)
def page(token):
    conn = get_db()
    name = request.form.get("nimi", "") if request.method == "POST" else None
    share = auth.enter_share(conn, token, name)
    if share is None:
        return _missing()
    note = notes.get(conn, share["note_id"])
    auth.load_actor()  # g.share for the rest of this request: its answer is private (no-store)
    if request.method == "POST":
        return redirect(request.path)
    if share["mode"] == "view":
        return render_template("share_view.html", note=note, html=_html(note))
    ask = not g.owner and ("nimi" in request.args or "guest_name" not in session)
    return render_template("share_edit.html", note=note, ask_name=ask, name=session.get("guest_name", ""))


# {note id: (rev, host, html)}: every guest polls, and a long note takes seconds to render.
_rendered = {}


def _html(note):
    cached = _rendered.get(note["id"])
    if cached and cached[:2] == (note["rev"], request.host):
        return cached[2]
    out = guest_html(note["body"])
    _rendered[note["id"]] = (note["rev"], request.host, out)
    return out


@bp.get("/api/share/current")
@auth.guest("view")
@rate_limit("share-current", 30, 60)
def current():
    """The shared note as the view page shows it; polled to follow the owner's edits.
    ?slug: the note the page shows (this browser may have opened another link since);
    ?rev: the rev it has, answered without the HTML while the note is still at it."""
    note = notes.get(get_db(), g.share["note_id"]) if g.share else None
    if note is None:
        return NOT_FOUND
    auth.require_note(note["id"], "view")
    if request.args.get("slug", note["slug"]) != note["slug"]:
        return {"error": OTHER_LINK}, 403
    data = {"note": {"slug": note["slug"], "title": note["title"]}, "rev": note["rev"], "mode": g.share["mode"]}
    if request.args.get("rev") != str(note["rev"]):
        data["html"] = _html(note)
    return data


def _json(row, token=None):
    return {
        "id": row["id"],
        # Only the hash is stored: the URL can be shown once, now.
        "url": f"{public_base()}/j/{token}" if token else None,
        "mode": row["mode"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used_at": row["last_used_at"],
    }


@bp.get("/api/shares")
def list_shares():
    conn = get_db()
    note = notes.by_slug(conn, request.args.get("slug", ""))
    if note is None:
        return NOT_FOUND
    return jsonify([_json(row) for row in shares.list_for_note(conn, note["id"])])


@bp.post("/api/shares")
def create_share():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return {"error": "Vigane päring."}, 400
    conn = get_db()
    note = notes.by_slug(conn, str(body.get("slug", "")))
    if note is None:
        return NOT_FOUND
    try:
        row, token = shares.create(conn, note["id"], body.get("mode"), body.get("expires_at"))
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return _json(row, token), 201


@bp.delete("/api/shares/<int:share_id>")
def revoke_share(share_id):
    conn = get_db()
    if shares.get(conn, share_id) is None:
        return {"error": "Linki ei leitud."}, 404
    shares.revoke(conn, share_id)
    return "", 204
