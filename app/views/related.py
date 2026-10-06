"""Seotud panel (related brain pages from gbrain), a note's history from the brain repo (Ajalugu) and
"sync now" for the brain repo. Owner only.
Owner: WP-H; docs/CONTRACTS.md §5."""

import logging
import re

from flask import Blueprint, jsonify

from .. import gbrain, gitsync, notes, security
from ..db import get_db

bp = Blueprint("related", __name__)
log = logging.getLogger("markmik.gitsync")


@bp.get("/api/related/<slug>")
def related(slug):
    conn = get_db()
    note = notes.by_slug(conn, slug)
    if note is None:
        return _error("Märget ei leitud.", 404)
    try:
        return jsonify(gbrain.related(conn, note, raise_errors=True))
    except gbrain.GbrainError as exc:
        log.warning("gbrain related failed: %s", exc)
        return _error("gbrain pole praegu kättesaadav.", 503)


@bp.get("/api/notes/<slug>/ajalugu")
def history(slug):
    """{"versions": [{sha, at, message}]} from the brain repo; [] before the note's first sync."""
    conn = get_db()
    note = notes.by_slug(conn, slug)
    if note is None:
        return _error("Märget ei leitud.", 404)
    try:
        versions = gitsync.history(conn, note)
    except gitsync.NotConnected:
        return _error("Brain repo pole ühendatud (Seaded → Ühendused).", 409)
    except gitsync.GitError as exc:
        log.warning("history failed: %s", exc)
        return _error("Ajalugu ei saanud lugeda. Proovi uuesti.", 503)
    return jsonify({"versions": [{k: v[k] for k in ("sha", "at", "message")} for v in versions]})


@bp.get("/api/notes/<slug>/ajalugu/<sha>")
def version(slug, sha):
    """{"body"}: the note as it was in that commit."""
    conn = get_db()
    note = notes.by_slug(conn, slug)
    if note is None or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return _error("Seda versiooni pole.", 404)
    try:
        body = gitsync.version(conn, note, sha)
    except gitsync.NotConnected:
        return _error("Brain repo pole ühendatud (Seaded → Ühendused).", 409)
    except (gitsync.GitError, UnicodeDecodeError) as exc:
        log.warning("history version failed: %s", exc)
        return _error("Versiooni ei saanud lugeda. Proovi uuesti.", 503)
    if body is None:
        return _error("Seda versiooni pole.", 404)
    return jsonify({"body": body})


@bp.post("/api/sync/now")
@security.rate_limit("sync-now", 6, 60)
def sync_now():
    try:
        return jsonify(gitsync.sync_now(get_db()))
    except gitsync.NotConnected:
        return _error("Brain repo pole ühendatud (Seaded → Ühendused).", 409)
    except gitsync.GitError as exc:
        log.warning("sync now failed: %s", exc)
        return _error("Sünkroonimine ebaõnnestus. Proovi hiljem uuesti.", 502)


def _error(message, status):
    return jsonify({"error": message}), status
