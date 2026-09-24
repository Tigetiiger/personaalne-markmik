"""ICS feeds (the school's event calendar, Moodle, anything with an .ics address). Each feed is a
sources row of kind 'ics' with config_json {"url"}. app.ics.fetch_ics (WP-D) returns the feed's
occurrences, already expanded, from ics.WINDOW_PAST_DAYS before today to ics.WINDOW_FUTURE_DAYS
after (docs/CONTRACTS.md §4). Each becomes its own event, uid
ics:<source_id>:<UID>:<recurrence_id>, so the same event in two feeds stays two events, a moved
occurrence keeps its identity, and removing a feed can find its own. Only occurrences inside
that window can be judged gone from the feed."""

import importlib
import ipaddress
import json
import socket
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from .. import connections, events
from ..db import tx
from ..util import TALLINN, local_today
from .base import SourceError, safe_message

# What a feed may set. Status, source and user_edited are ours.
FEED_FIELDS = ("title", "start_at", "end_at", "start_date", "end_date", "all_day", "tzid", "kind", "location",
               "description")


class IcsAdapter:
    kind = "ics"
    structured = True

    def fetch(self, conn, source_row, cursor):
        """-> (occurrences, the day they were fetched: covers() rebuilds the window from it)."""
        found = _fetch_ics(json.loads(source_row["config_json"])["url"])
        return [{"external_id": _external_id(e), "raw": e} for e in found], local_today().isoformat()

    def normalise(self, conn, source_row, raw):
        fields = {k: raw[k] for k in FEED_FIELDS if k in raw}
        fields.update(status="kinnitatud", source="ics", source_ref=raw["uid"])
        events.upsert_by_uid(conn, self.event_uid(source_row, raw), fields)

    def event_uid(self, source_row, raw):
        return f"ics:{source_row['id']}:{_external_id(raw)}"

    def covers(self, cursor, raw):
        ics = importlib.import_module("app.ics")
        fetched = date.fromisoformat(cursor)
        # A day's margin at both ends: fetch_ics and this check may disagree about the edge.
        return (fetched - timedelta(days=ics.WINDOW_PAST_DAYS) < _start_day(raw)
                < fetched + timedelta(days=ics.WINDOW_FUTURE_DAYS))


ADAPTER = IcsAdapter()


def feeds(conn):
    rows = conn.execute("SELECT id, name, config_json, enabled, last_ok_at, last_error FROM sources "
                        "WHERE kind = 'ics' ORDER BY name")
    return [{"id": r["id"], "name": r["name"], "url": json.loads(r["config_json"])["url"],
             "enabled": bool(r["enabled"]), "last_ok_at": r["last_ok_at"], "last_error": r["last_error"]}
            for r in rows]


def add_feed(conn, url, name):
    """-> source id. ValueError (Estonian) for a bad address or a name already in use. Refuses
    anything but a public https host here, to say so while the user is still on the card;
    fetch_ics checks every connection it makes itself (docs/CONTRACTS.md §4)."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("Sisesta kalendrivoo aadress, mis algab https:// või webcal://.")
    if "." not in parts.hostname.strip(".") or not all(_public(a) for a in _addresses(parts.hostname)):
        raise ValueError("Kalendrivoog peab olema avalikus internetis, mitte kohalikus võrgus.")
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
    try:
        return importlib.import_module("app.ics").fetch_ics(url)
    except ValueError as exc:  # fetch_ics's own Estonian message for a feed it cannot use
        raise SourceError(str(exc)) from None


def _addresses(host):
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        raise ValueError("Sellist aadressi ei leitud.") from None


def _public(address):
    ip = ipaddress.ip_address(address.split("%")[0])
    ip = getattr(ip, "ipv4_mapped", None) or ip
    # is_global is False for private, loopback, link-local and 100.64/10 (Tailscale) alike.
    return ip.is_global and not ip.is_multicast


def _external_id(e):
    return f"{e['uid']}:{e['recurrence_id']}"


def _start_day(raw):
    if raw.get("start_date"):
        return date.fromisoformat(raw["start_date"])
    return datetime.fromisoformat(raw["start_at"].replace("Z", "+00:00")).astimezone(TALLINN).date()


connections.register("ics", "Kalendrivood (ICS)", order=60, test=test_connection)
