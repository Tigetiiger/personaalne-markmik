"""ICS feeds (the school's event calendar, Moodle, anything with an .ics address). Each feed is a
sources row of kind 'ics' with config_json {"url"}. app.ics.fetch_ics (WP-D) parses a feed into
events fields; they are upserted as uid ics:<source_id>:<UID>[:<recurrence id>], so the same
event in two feeds stays two events and removing a feed can find its own."""

import importlib
import json
import sqlite3
from urllib.parse import urlsplit

from .. import connections, events
from ..db import tx
from .base import safe_message

# What a feed may set. Status, source, series_id and user_edited are ours.
FEED_FIELDS = ("title", "start_at", "end_at", "start_date", "end_date", "all_day", "tzid", "kind", "location",
               "description", "rrule", "exdates_json", "recurrence_id")


class IcsAdapter:
    kind = "ics"
    structured = True

    def fetch(self, conn, source_row, cursor):
        found = _fetch_ics(json.loads(source_row["config_json"])["url"])
        # A series before its overridden occurrences, so that an override finds its series.
        found = sorted(found, key=lambda e: bool(e.get("recurrence_id")))
        return [{"external_id": _external_id(e), "raw": e} for e in found], None

    def normalise(self, conn, source_row, raw):
        fields = {k: raw[k] for k in FEED_FIELDS if k in raw}
        fields.update(status="kinnitatud", source="ics", source_ref=raw["uid"])
        if raw.get("recurrence_id"):
            series = conn.execute("SELECT id FROM events WHERE uid = ?",
                                  (f"ics:{source_row['id']}:{raw['uid']}",)).fetchone()
            fields["series_id"] = series["id"] if series else None
        events.upsert_by_uid(conn, f"ics:{source_row['id']}:{_external_id(raw)}", fields)


ADAPTER = IcsAdapter()


def feeds(conn):
    rows = conn.execute("SELECT id, name, config_json, enabled, last_ok_at, last_error FROM sources "
                        "WHERE kind = 'ics' ORDER BY name")
    return [{"id": r["id"], "name": r["name"], "url": json.loads(r["config_json"])["url"],
             "enabled": bool(r["enabled"]), "last_ok_at": r["last_ok_at"], "last_error": r["last_error"]}
            for r in rows]


def add_feed(conn, url, name):
    """-> source id. ValueError (Estonian) for a bad address or a name already in use."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Sisesta kalendrivoo aadress, mis algab https:// või webcal://.")
    try:
        return conn.execute("INSERT INTO sources (kind, name, config_json) VALUES ('ics', ?, ?)",
                            ((name or "").strip() or parts.hostname, json.dumps({"url": url}))).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("Sellise nimega kalendrivoog on juba olemas.") from None


def remove_feed(conn, source_id):
    """Drop the feed and take its events off the calendar, except those the user edited."""
    with tx(conn):
        for row in conn.execute("SELECT id FROM events WHERE uid LIKE ? AND user_edited = 0 AND status != 'eemaldatud'",
                                (f"ics:{int(source_id)}:%",)).fetchall():
            events.remove(conn, row["id"])
        conn.execute("DELETE FROM sources WHERE id = ? AND kind = 'ics'", (source_id,))


def test_connection(conn):
    listed = feeds(conn)
    if not listed:
        return {"ok": False, "summary": "", "error": "Ühtegi kalendrivoogu pole lisatud."}
    total, errors = 0, []
    for feed in listed:
        try:
            total += len(_fetch_ics(feed["url"]))
        except Exception as exc:  # one broken feed is a result to show, the others still count
            errors.append(f"{feed['name']}: {safe_message(exc)}")
    return {"ok": not errors, "summary": f"Vooge: {len(listed)}, sündmusi: {total}", "error": "; ".join(errors) or None}


def _fetch_ics(url):
    # app.ics is WP-D's and may not be merged yet: imported when used, so this module loads without it.
    return importlib.import_module("app.ics").fetch_ics(url)


def _external_id(e):
    return f"{e['uid']}:{e['recurrence_id']}" if e.get("recurrence_id") else e["uid"]


connections.register("ics", "Kalendrivood (ICS)", order=60, test=test_connection)
