"""Seotud panel (related brain pages from gbrain) and "sync now" for the brain repo. Owner only.
Owner: WP-H; docs/CONTRACTS.md §5."""

import logging

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
