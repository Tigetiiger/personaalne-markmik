"""iCalendar in and out.

Out: the private feed /ics/<token>.ics that a phone calendar subscribes to (confirmed events
only). The token lives in settings "ics_feed_token" and is made on first use.
In: fetch_ics(url) for ICS sources (WP-E1): expanded occurrences in events-column form, each with
the source UID and its original start (docs/CONTRACTS.md, "Connection helpers").
"""

import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpcore
import httpx
import recurring_ical_events
from icalendar import Calendar, Event, Timezone, vRecur

from . import connections
from .events import MAX_DESCRIPTION, MAX_LOCATION, SUB_DAY_PARTS, TOO_FREQUENT
from .util import TALLINN, iso, local_today, parse_iso

log = logging.getLogger("markmik.ics")

TOKEN_KEY = "ics_feed_token"
WINDOW_PAST_DAYS = 30
WINDOW_FUTURE_DAYS = 365
MAX_REDIRECTS = 3
MAX_BYTES = 5 * 1024 * 1024        # decoded: a small gzip body can inflate a thousandfold
MAX_OCCURRENCES = 5000
TIME_LIMIT_S = 60                  # download and expansion together; a trickling server is cut off

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
        try:
            ev = _vevent(r, cancelled.get(r["id"], []))
            ev.to_ical()  # icalendar asserts (AssertionError) on a raw line break in a value
            cal.add_component(ev)
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            # One bad row must not empty the phone's calendar.
            log.warning("event %s left out of the feed: %s", r["id"], exc)
    cal.add_missing_timezones(first_date=date(2020, 1, 1), last_date=date(2040, 1, 1))
    return cal.to_ical()


def fetch_ics(url):
    """Download an ICS feed: its occurrences whose start date lies from today - WINDOW_PAST_DAYS
    to today + WINDOW_FUTURE_DAYS, series expanded: [{uid, recurrence_id, title, start_at,
    end_at, start_date, end_date, all_day, location, description}]. recurrence_id is None for a
    single event, else the occurrence's original start ('YYYY-MM-DD' or UTC ISO), so a moved
    occurrence keeps its identity.

    The URL comes from the user and may point anywhere: only https, and every connection
    (redirects are followed by hand) goes to an address checked before connecting - never
    loopback, private, link-local, shared (100.64/10) or a single-label host. At most MAX_BYTES,
    MAX_OCCURRENCES and TIME_LIMIT_S. ValueError with an Estonian message that never contains the
    URL (it may carry a token)."""
    deadline = time.monotonic() + TIME_LIMIT_S
    today = local_today()
    data = _download(url, deadline)
    return parse_ics(data, today - timedelta(days=WINDOW_PAST_DAYS), today + timedelta(days=WINDOW_FUTURE_DAYS), deadline)


def parse_ics(data, first, last, deadline=None):
    """Occurrences of an iCalendar text whose start date is within first..last (inclusive).
    Series repeating more than once a day and items without a start are left out; a VEVENT
    without a UID gets one from its summary and start. A series that cannot be expanded is
    skipped, not fatal."""
    try:
        cal = Calendar.from_ical(data)
        vevents = cal.walk("VEVENT")
        for c in vevents:
            if not c.get("uid"):
                seed = f"{c.get('summary', '')}|{c['dtstart'].to_ical().decode() if c.get('dtstart') else ''}"
                c["uid"] = "nouid-" + hashlib.sha1(seed.encode()).hexdigest()[:20]
        too_often = {str(c["uid"]) for c in vevents if any(_too_often(r) for r in _rrules(c))}
        # Without DTSTART the expander raises for the whole calendar; drop such items instead.
        cal.subcomponents = [c for c in cal.subcomponents
                             if not (c.name == "VEVENT" and (str(c["uid"]) in too_often or not c.get("dtstart")))]
        # UIDs that are series: only their occurrences carry a recurrence_id.
        series = {str(c["uid"]) for c in vevents if c.get("rrule") or c.get("rdate") or c.get("recurrence-id")}
        query = recurring_ical_events.of(cal, skip_bad_series=True)
    except Exception:  # icalendar raises many types for bad input
        raise ValueError("ICS-fail on vigane.") from None
    out = []
    span = (datetime.combine(first, datetime.min.time()), datetime.combine(last + timedelta(days=1), datetime.min.time()))
    for one in query.series:
        if deadline is not None and time.monotonic() > deadline:
            raise ValueError("ICS-voo töötlemine võttis liiga kaua aega.")
        try:
            for occurrence in one.between(*span):
                item = _item(occurrence.as_component(False), series, first, last)
                if item is None:
                    continue
                out.append(item)
                if len(out) > MAX_OCCURRENCES:
                    raise ValueError(f"ICS-voos on liiga palju sündmusi (üle {MAX_OCCURRENCES}).")
        except ValueError as exc:
            if str(exc).startswith("ICS-voos"):
                raise
            log.warning("ics series skipped: %s", exc.__class__.__name__)
        except Exception as exc:  # a broken series of someone else's calendar
            log.warning("ics series skipped: %s", exc.__class__.__name__)
    return out


def _rrules(c):
    rules = c.get("rrule")
    return [] if rules is None else rules if isinstance(rules, list) else [rules]


def _too_often(rule):
    return bool(set(rule.get("FREQ", [])) & TOO_FREQUENT or SUB_DAY_PARTS & set(rule))


def _item(c, series, first, last):
    """One expanded occurrence as events columns, or None when it starts outside first..last."""
    uid = str(c["uid"])
    dtstart = c["dtstart"].dt
    dtend = c.get("dtend").dt if c.get("dtend") else None
    all_day = not isinstance(dtstart, datetime)
    if dtend is not None and isinstance(dtend, datetime) == all_day:
        dtend = None  # DATE start with a DATE-TIME end, or the other way round
    start_day = dtstart if all_day else _aware(dtstart).astimezone(TALLINN).date()
    if not first <= start_day <= last:
        return None  # began before the window and only overlaps it
    if all_day:
        times = {"start_at": None, "end_at": None, "start_date": dtstart.isoformat(),
                 "end_date": (dtend if dtend and dtend > dtstart else dtstart + timedelta(days=1)).isoformat()}
    else:
        end_at = iso(_aware(dtend)) if dtend else None
        times = {"start_at": iso(_aware(dtstart)), "end_at": end_at if end_at and end_at >= iso(_aware(dtstart)) else None,
                 "start_date": None, "end_date": None}
    return {
        "uid": uid,
        "recurrence_id": _key(c["recurrence-id"].dt) if uid in series and c.get("recurrence-id") else None,
        "title": str(c.get("summary") or "").strip()[:200] or "(pealkirjata)",
        "all_day": 1 if all_day else 0,
        "location": str(c.get("location") or "")[:MAX_LOCATION],
        "description": str(c.get("description") or "")[:MAX_DESCRIPTION],
        **times,
    }


def _key(value):
    """An original start as an occurrence key: 'YYYY-MM-DD' or UTC ISO."""
    return iso(_aware(value)) if isinstance(value, datetime) else value.isoformat()


def _aware(dt):
    # Floating times (no zone) in a feed mean local time here.
    return dt if dt.tzinfo else dt.replace(tzinfo=TALLINN)


def _download(url, deadline):
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        with _client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                _check_url(url)
                with client.stream("GET", url) as r:
                    if r.is_redirect:
                        url = str(r.url.join(r.headers["location"]))
                        continue
                    if r.status_code != 200:
                        raise ValueError(f"ICS-voo server vastas veaga {r.status_code}.")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise ValueError(f"ICS-voog on liiga suur (üle {MAX_BYTES // 1024 // 1024} MB).")
                        if time.monotonic() > deadline:
                            raise ValueError("ICS-voo allalaadimine võttis liiga kaua aega.")
                    return bytes(body)
    except (httpx.HTTPError, httpx.InvalidURL):
        # `from None`: httpx messages can include the URL.
        raise ValueError("ICS-voogu ei saanud alla laadida.") from None
    raise ValueError("ICS-voog suunab liiga palju kordi ümber.")


class _CheckedBackend(httpcore.SyncBackend):
    """Resolves the host once, checks every address, and connects to a checked one, so nothing
    is sent anywhere unchecked. TLS SNI and the Host header still use the name: httpcore takes
    them from the URL, not from the address connected to."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = _resolve(host, port)
        for address in addresses:
            _check_ip(address)
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


def _client():
    # No proxies from the environment: the connection must go where it was checked to go.
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool._network_backend = _CheckedBackend()  # httpx 0.28 has no public hook for this
    return httpx.Client(transport=transport, timeout=10, trust_env=False, follow_redirects=False)


def _check_url(url):
    try:
        parts = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError("Vigane ICS-aadress.") from None
    if parts.scheme != "https":
        raise ValueError("ICS-aadress peab algama https:// või webcal://")
    try:
        ipaddress.ip_address(parts.host)
    except ValueError:
        if "." not in parts.host.strip("."):
            raise ValueError("ICS-aadressi server pole lubatud.") from None


def _resolve(host, port):
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError):
        raise ValueError("ICS-aadressi serverit ei leitud.") from None


NAT64 = ipaddress.ip_network("64:ff9b::/96")
V4_COMPATIBLE = ipaddress.ip_network("::/96")


def _check_ip(address):
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6:
        if ip.is_site_local:
            raise ValueError("ICS-aadress viitab sisevõrku, seda ei laadita.")
        # IPv6 forms that carry an IPv4 address reach that address: judge the IPv4 one.
        if ip.ipv4_mapped or ip.sixtofour:
            ip = ip.ipv4_mapped or ip.sixtofour
        elif ip in NAT64 or ip in V4_COMPATIBLE:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
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
