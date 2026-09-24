"""Source runner: fetch from eKool, Gmail and ICS feeds into source_items, then normalise.

An adapter (one per sources.kind, found through ADAPTER_MODULES) has
    kind: str
    structured: bool       # True: items become events here; False: app.extract reads them later
    fetch(conn, source_row, cursor) -> (items: list[{"external_id", "raw", "status"?, "error"?}], new_cursor)
    normalise(conn, source_row, raw)   # structured adapters only
    event_uid(source_row, raw), covers(cursor, raw)   # structured: which event an item made, and
                                                      # whether this fetch's window includes it

Unstructured items (e-mail) are handed to app.extract by leaving them in status 'new'; its job
picks them up. Messages never change, so they are stored once; structured items (eKool todos,
ICS events) do change, so a changed raw is stored again and normalised again, and one that is
gone from a fetch whose window covers it takes its event off the calendar (eemaldatud, but not
rejected: it comes back if the source brings it back).

Errors land in sources.last_error in Estonian and never carry a secret. A source whose
credentials are missing is "not connected": it is disabled quietly instead of failing on every
run. NeedsUser (wrong password, revoked token) disables it too, keeping the message, so a
rejected login is not retried 4 times a day; saving new credentials enables it again.
"""

import importlib
import json
import logging
import re
from html import unescape

import httpx
import nh3

from .. import events
from ..db import tx
from ..jobs import job
from ..util import now_iso

log = logging.getLogger("markmik.sources")

ADAPTER_MODULES = {"ekool": "app.sources.ekool", "gmail": "app.sources.gmail", "ics": "app.sources.icsfeed"}
NOT_CONNECTED = "Pole ühendatud."


class SourceError(Exception):
    """A failure whose message is Estonian, safe to show and free of secrets."""


class NeedsUser(SourceError):
    """Only the user can fix this (new password, reconnect); scheduled runs stop until then."""


class NotConnected(Exception):
    """No credentials stored for this source."""


class PartialFetch(SourceError):
    """Some items came and some failed: store what came, keep the cursor where it is safe."""

    def __init__(self, message, items, cursor):
        super().__init__(message)
        self.items, self.cursor = items, cursor


def adapter_for(kind):
    return importlib.import_module(ADAPTER_MODULES[kind]).ADAPTER


def run_source(conn, source_id):
    """Fetch one source, store its items, normalise structured ones. -> {"ok", "items", "error"}"""
    src = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    adapter = adapter_for(src["kind"])
    started = now_iso()
    error = None
    try:
        items, cursor = adapter.fetch(conn, src, src["cursor"])
    except PartialFetch as exc:
        items, cursor, error = exc.items, exc.cursor, str(exc)
        log.warning("source %s/%s partly failed: %s", src["kind"], src["id"], error)
    except NotConnected:
        conn.execute("UPDATE sources SET enabled = 0, last_run_at = ?, last_error = NULL WHERE id = ?",
                     (started, source_id))
        return {"ok": False, "items": 0, "error": NOT_CONNECTED}
    except Exception as exc:
        error = safe_message(exc)
        # No traceback for network errors: a feed's URL may carry its token.
        log.warning("source %s/%s failed: %s", src["kind"], src["id"], error,
                    exc_info=not isinstance(exc, (SourceError, httpx.HTTPError)))
        conn.execute("UPDATE sources SET enabled = ?, last_run_at = ?, last_error = ? WHERE id = ?",
                     (0 if isinstance(exc, NeedsUser) else src["enabled"], started, error, source_id))
        return {"ok": False, "items": 0, "error": error}
    with tx(conn):
        stored = sum(_store(conn, source_id, item, adapter.structured) for item in items)
        # The cursor moves only together with the items it covers: a crash re-reads the range.
        conn.execute("UPDATE sources SET cursor = ?, last_run_at = ?, last_ok_at = COALESCE(?, last_ok_at), "
                     "last_error = ? WHERE id = ?", (cursor, started, None if error else now_iso(), error, source_id))
    if adapter.structured:
        _normalise_pending(conn, adapter, src)
        if error is None:
            _retire_vanished(conn, adapter, src, {str(item["external_id"]) for item in items}, cursor)
    return {"ok": error is None, "items": stored, "error": error}


def run_now(conn, kind=None):
    """Run every enabled source (of one kind). -> {kind: {"ok", "items", "error"}}"""
    sql, args = "SELECT id, kind, name FROM sources WHERE enabled = 1", ()
    if kind:
        sql, args = sql + " AND kind = ?", (kind,)
    out = {}
    for row in conn.execute(sql + " ORDER BY id", args).fetchall():
        result = run_source(conn, row["id"])
        total = out.setdefault(row["kind"], {"ok": True, "items": 0, "error": None})
        total["ok"] = total["ok"] and result["ok"]
        total["items"] += result["items"]
        if result["error"] and not total["error"]:
            total["error"] = f"{row['name']}: {result['error']}"
    if kind and kind not in out:
        out[kind] = {"ok": False, "items": 0, "error": NOT_CONNECTED}
    return out


@job("gmail-sync", every=300)
def sync_gmail(conn):
    run_now(conn, "gmail")


@job("ekool-sync", at=("07:00", "13:00", "17:00", "21:00"))
def sync_ekool(conn):
    run_now(conn, "ekool")


@job("ics-sync", every=3600)
def sync_ics(conn):
    run_now(conn, "ics")


def upsert_source(conn, kind, name, config=None):
    """Create or re-enable a sources row (credentials were just saved). -> id"""
    return conn.execute(
        "INSERT INTO sources (kind, name, config_json) VALUES (?, ?, ?) "
        "ON CONFLICT (kind, name) DO UPDATE SET enabled = 1, last_error = NULL, "
        "config_json = excluded.config_json RETURNING id",
        (kind, name, json.dumps(config or {}, ensure_ascii=False))).fetchone()["id"]


def html_to_text(html):
    """Plain text from untrusted HTML: tags, comments, scripts and styles dropped, block ends kept
    as line breaks, entities decoded."""
    html = re.sub(r"</t[dh]\s*>", " ", html or "", flags=re.I)
    html = re.sub(r"<br\s*/?>|</(?:p|div|tr|li|h[1-6]|table|ul|ol)\s*>", "\n", html, flags=re.I)
    text = unescape(nh3.clean(html, tags=set(), clean_content_tags={"script", "style", "title"}))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _store(conn, source_id, item, structured):
    raw = json.dumps(item["raw"], ensure_ascii=False, sort_keys=True)
    if structured:
        sql = ("INSERT INTO source_items (source_id, external_id, raw_json) VALUES (?, ?, ?) "
               "ON CONFLICT (source_id, external_id) DO UPDATE SET raw_json = excluded.raw_json, "
               "fetched_at = excluded.fetched_at, status = 'new', error = NULL "
               "WHERE source_items.raw_json != excluded.raw_json")
    else:
        # A message is stored once. 'skipped' marks one looked at and passed over, so it is not
        # looked at again. A row whose raw is {"unreadable": ...} holds no mail, only its id: the
        # mail replaces it when it can be read after all.
        sql = ("INSERT INTO source_items (source_id, external_id, raw_json, status, error) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT (source_id, external_id) DO UPDATE SET raw_json = excluded.raw_json, "
               "status = excluded.status, error = excluded.error "
               "WHERE json_extract(source_items.raw_json, '$.unreadable') IS NOT NULL")
        status = item.get("status", "new")
        stored = conn.execute(sql, (source_id, str(item["external_id"]), raw, status, item.get("error"))).rowcount
        return stored if status == "new" else 0  # only mail waiting for extraction counts as an item
    return conn.execute(sql, (source_id, str(item["external_id"]), raw)).rowcount


def _normalise_pending(conn, adapter, src):
    rows = conn.execute("SELECT id, raw_json FROM source_items WHERE source_id = ? AND status IN ('new', 'error') "
                        "ORDER BY id", (src["id"],)).fetchall()
    for row in rows:
        try:
            with tx(conn):
                adapter.normalise(conn, src, json.loads(row["raw_json"]))
                conn.execute("UPDATE source_items SET status = 'done', error = NULL WHERE id = ?", (row["id"],))
        except Exception as exc:
            log.exception("normalising source item %s failed", row["id"])
            conn.execute("UPDATE source_items SET status = 'error', error = ? WHERE id = ?",
                         (safe_message(exc), row["id"]))


def _retire_vanished(conn, adapter, src, seen, cursor):
    rows = conn.execute("SELECT id, external_id, raw_json FROM source_items WHERE source_id = ?", (src["id"],)).fetchall()
    if rows and not seen:
        # A source that suddenly has nothing is more likely broken than emptied. The price: eKool
        # homework deleted while nothing else is due in the six-week window (a holiday) stays on
        # the calendar until something new appears there.
        log.warning("source %s/%s returned nothing; its earlier items are kept", src["kind"], src["id"])
        return
    for row in rows:
        raw = json.loads(row["raw_json"])
        if row["external_id"] in seen or not adapter.covers(cursor, raw):
            continue
        with tx(conn):
            uid = adapter.event_uid(src, raw)
            ev = conn.execute("SELECT user_edited, status FROM events WHERE uid = ?", (uid,)).fetchone()
            if ev is not None and not ev["user_edited"] and ev["status"] != "eemaldatud":
                events.upsert_by_uid(conn, uid, {"status": "eemaldatud"})
            # Forgotten, so that the same item coming back is new again and restores its event.
            conn.execute("DELETE FROM source_items WHERE id = ?", (row["id"],))


def safe_message(exc):
    # Only our own errors are shown as they are; anything else could quote a URL, a form body or
    # a header, so it is reduced to its type.
    if isinstance(exc, SourceError):
        return str(exc)
    if isinstance(exc, httpx.HTTPError):
        return f"Võrguviga ({exc.__class__.__name__}), proovin hiljem uuesti."
    return f"Ootamatu viga ({exc.__class__.__name__}), vaata logi."
