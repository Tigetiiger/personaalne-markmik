"""iCalendar in and out.

Out: the private feed /ics/<token>.ics that a phone calendar subscribes to (confirmed events
only). The token lives in settings "ics_feed_token" and is made on first use.
In: fetch_ics(url) for ICS sources (WP-E1): expanded occurrences in events-column form, each with
the source UID and its original start (docs/CONTRACTS.md, "Connection helpers").
"""

import ipaddress
import json
import secrets
import socket
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import recurring_ical_events
from icalendar import Calendar, Event, Timezone, vRecur

from . import connections
from .util import TALLINN, iso, local_today, parse_iso

TOKEN_KEY = "ics_feed_token"
WINDOW_PAST_DAYS = 30
WINDOW_FUTURE_DAYS = 365
MAX_REDIRECTS = 3

# The standard rule-based definition (what Google and Apple emit): compact, and valid for every
# year since the EU rules of 1996. Clients that know the Olson name use their own data anyway.
TALLINN_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Tallinn
BEGIN:DAYLIGHT
TZOFFSETFROM:+0200
TZOFFSETTO:+0300
TZNAME:EEST
DTSTART:19700329T030000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0300
TZOFFSETTO:+0200
TZNAME:EET
DTSTART:19701025T040000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
"""


def feed_token(conn):
    """The feed's secret token, created on first use."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (TOKEN_KEY,)).fetchone()
    if row:
        return row["value"]
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (TOKEN_KEY, secrets.token_urlsafe(24)))
    return conn.execute("SELECT value FROM settings WHERE key = ?", (TOKEN_KEY,)).fetchone()["value"]


def feed_url(conn):
    """Path of the private feed; the page adds the host it is served on."""
    return f"/ics/{feed_token(conn)}.ics"


def token_ok(conn, token):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (TOKEN_KEY,)).fetchone()
    return row is not None and secrets.compare_digest(token.encode(), row["value"].encode())


def feed(conn):
    """The whole iCalendar feed as bytes: confirmed events, series with RRULE/EXDATE, overridden
    occurrences with RECURRENCE-ID under their series' UID."""
    cal = Calendar()
    cal.add("prodid", "-//Märkmik//markmik//ET")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Märkmik")
    cal.add("x-wr-timezone", "Europe/Tallinn")
    cal.add_component(Timezone.from_ical(TALLINN_VTIMEZONE))
    rows = conn.execute(
        """SELECT e.*, s.uid AS series_uid FROM events e
           LEFT JOIN events s ON s.id = e.series_id
           WHERE e.status = 'kinnitatud' AND (e.series_id IS NULL OR s.status = 'kinnitatud')
           ORDER BY e.id"""
    ).fetchall()
    # A cancelled override (removed occurrence) becomes an EXDATE of its series.
    cancelled = {}
    for r in conn.execute("SELECT series_id, recurrence_id FROM events WHERE series_id IS NOT NULL AND status = 'eemaldatud'"):
        cancelled.setdefault(r["series_id"], []).append(r["recurrence_id"])
    for r in rows:
        cal.add_component(_vevent(r, cancelled.get(r["id"], [])))
    cal.add_missing_timezones(first_date=date(2020, 1, 1), last_date=date(2040, 1, 1))
    return cal.to_ical()


def fetch_ics(url):
    """Download an ICS feed: its occurrences whose start date lies from today - WINDOW_PAST_DAYS
    to today + WINDOW_FUTURE_DAYS, series expanded: [{uid, recurrence_id, title, start_at,
    end_at, start_date, end_date, all_day, location, description}]. recurrence_id is None for a
    single event, else the occurrence's original start ('YYYY-MM-DD' or UTC ISO), so a moved
    occurrence keeps its identity.

    The URL comes from the user and may point anywhere: only https, and no hop (redirects are
    followed by hand) may reach a loopback, private, link-local or shared (100.64/10) address or a
    single-label host. ValueError with an Estonian message that never contains the URL (it may
    carry a token)."""
    today = local_today()
    return parse_ics(_download(url), today - timedelta(days=WINDOW_PAST_DAYS), today + timedelta(days=WINDOW_FUTURE_DAYS))


def parse_ics(data, first, last):
    """Occurrences of an iCalendar text whose start date is within first..last (inclusive)."""
    try:
        cal = Calendar.from_ical(data)
        # UIDs that are series: only their occurrences carry a recurrence_id.
        series = {str(c.get("uid")) for c in cal.walk("VEVENT") if c.get("rrule") or c.get("rdate") or c.get("recurrence-id")}
        items = recurring_ical_events.of(cal).between(first, last + timedelta(days=1))
    except Exception:  # icalendar and the expander raise many types for bad input
        raise ValueError("ICS-fail on vigane.") from None
    out = []
    for c in items:
        uid = str(c.get("uid") or "")
        dtstart = c.get("dtstart").dt
        dtend = c.get("dtend").dt if c.get("dtend") else None
        all_day = not isinstance(dtstart, datetime)
        start_day = dtstart if all_day else _aware(dtstart).astimezone(TALLINN).date()
        if not first <= start_day <= last:
            continue  # began before the window and only overlaps it
        if all_day:
            times = {"start_at": None, "end_at": None, "start_date": dtstart.isoformat(),
                     "end_date": (dtend if dtend and dtend > dtstart else dtstart + timedelta(days=1)).isoformat()}
        else:
            times = {"start_at": iso(_aware(dtstart)), "end_at": iso(_aware(dtend)) if dtend else None,
                     "start_date": None, "end_date": None}
        out.append({
            "uid": uid,
            "recurrence_id": _key(c.get("recurrence-id").dt) if uid in series and c.get("recurrence-id") else None,
            "title": str(c.get("summary") or "").strip()[:200] or "(pealkirjata)",
            "all_day": 1 if all_day else 0,
            "location": str(c.get("location") or ""),
            "description": str(c.get("description") or ""),
            **times,
        })
    return out


def _key(value):
    """An original start as an occurrence key: 'YYYY-MM-DD' or UTC ISO."""
    return iso(_aware(value)) if isinstance(value, datetime) else value.isoformat()


def _aware(dt):
    # Floating times (no zone) in a feed mean local time here.
    return dt if dt.tzinfo else dt.replace(tzinfo=TALLINN)


def _download(url):
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        with _client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                _check_url(url)
                with client.stream("GET", url) as r:
                    # DNS may answer differently the second time: check where we actually are.
                    stream = r.extensions.get("network_stream")
                    peer = stream.get_extra_info("server_addr") if stream else None
                    if not peer:
                        raise ValueError("ICS-voo serveri aadressi ei saanud kontrollida.")
                    _check_ip(peer[0])
                    if r.is_redirect:
                        url = str(r.url.join(r.headers["location"]))
                        continue
                    if r.status_code != 200:
                        raise ValueError(f"ICS-voo server vastas veaga {r.status_code}.")
                    return r.read()
    except (httpx.HTTPError, httpx.InvalidURL):
        # `from None`: httpx messages can include the URL.
        raise ValueError("ICS-voogu ei saanud alla laadida.") from None
    raise ValueError("ICS-voog suunab liiga palju kordi ümber.")


def _client():
    # No proxies from the environment: the peer check must see the real server.
    return httpx.Client(timeout=30, trust_env=False, follow_redirects=False)


def _check_url(url):
    try:
        parts = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError("Vigane ICS-aadress.") from None
    if parts.scheme != "https":
        raise ValueError("ICS-aadress peab algama https:// või webcal://")
    host = parts.host
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if "." not in host.strip("."):
            raise ValueError("ICS-aadressi server pole lubatud.") from None
        addresses = _resolve(host, parts.port or 443)
    for address in addresses:
        _check_ip(address)


def _resolve(host, port):
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError):
        raise ValueError("ICS-aadressi serverit ei leitud.") from None


def _check_ip(address):
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        raise ValueError("ICS-aadress viitab sisevõrku, seda ei laadita.")


def _vevent(r, cancelled):
    ev = Event()
    ev.add("uid", r["series_uid"] or r["uid"])
    ev.add("summary", r["title"])
    ev.add("dtstamp", parse_iso(r["updated_at"]))
    if r["all_day"]:
        ev.add("dtstart", date.fromisoformat(r["start_date"]))
        ev.add("dtend", date.fromisoformat(r["end_date"]))
    else:
        tz = ZoneInfo(r["tzid"])
        ev.add("dtstart", parse_iso(r["start_at"]).astimezone(tz))
        if r["end_at"]:
            ev.add("dtend", parse_iso(r["end_at"]).astimezone(tz))
    if r["rrule"]:
        ev.add("rrule", vRecur.from_ical(r["rrule"]))
        exdates = json.loads(r["exdates_json"] or "[]") + cancelled
        if exdates:
            ev.add("exdate", [_occurrence_value(r, x) for x in exdates])
    if r["series_uid"]:
        ev.add("recurrence-id", _occurrence_value(r, r["recurrence_id"]))
    if r["location"]:
        ev.add("location", r["location"])
    if r["description"]:
        ev.add("description", r["description"])
    ev.add("categories", [r["kind"]])
    return ev


def _occurrence_value(r, key):
    """An occurrence key ('YYYY-MM-DD' or UTC ISO) as a DATE or a zoned DATE-TIME."""
    if len(key) == 10:
        return date.fromisoformat(key)
    return parse_iso(key).astimezone(ZoneInfo(r["tzid"]))


def _test_phone(conn):
    token = feed_token(conn)
    return {"ok": True, "summary": f"Privaatne kalendriaadress: /ics/{'•' * 4}{token[-4:]}.ics", "error": None}


connections.register("phone", "Telefoni kalender", order=70, test=_test_phone)
