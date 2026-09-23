"""iCalendar in and out.

Out: the private feed /ics/<token>.ics that a phone calendar subscribes to (confirmed events
only). The token lives in settings "ics_feed_token" and is made on first use.
In: fetch_ics(url) for ICS sources (WP-E1): recurring events come back expanded, one dict per
occurrence, in events-column form ready for events.upsert_by_uid.
"""

import json
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import recurring_ical_events
from icalendar import Calendar, Event, Timezone, vRecur

from . import connections
from .util import TALLINN, iso, local_today, parse_iso

TOKEN_KEY = "ics_feed_token"

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


def feed_path(conn):
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


def fetch_ics(url, *, days_back=30, days_ahead=365):
    """Download an ICS feed and return its events between days_back ago and days_ahead from
    today: [{uid, title, start_at, end_at, start_date, end_date, all_day, location, description}].
    ValueError (Estonian) when the address or the file is unusable."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("http://", "https://")):
        raise ValueError("ICS-aadress peab algama http:// või https://")
    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    today = local_today()
    return parse_ics(r.content, today - timedelta(days=days_back), today + timedelta(days=days_ahead))


def parse_ics(data, start, end):
    """Events of an iCalendar text between dates start and end, recurring ones expanded."""
    try:
        cal = Calendar.from_ical(data)
        # UIDs that are series: their occurrences get a uid of their own.
        series = {str(c.get("uid")) for c in cal.walk("VEVENT") if c.get("rrule") or c.get("rdate") or c.get("recurrence-id")}
        items = recurring_ical_events.of(cal).between(start, end)
    except Exception as exc:  # icalendar and the expander raise many types for bad input
        raise ValueError("ICS-fail on vigane.") from exc
    out = []
    for c in items:
        uid = str(c.get("uid") or "")
        dtstart = c.get("dtstart").dt
        dtend = c.get("dtend").dt if c.get("dtend") else None
        all_day = not isinstance(dtstart, datetime)
        if all_day:
            key = dtstart.isoformat()
            times = {"start_at": None, "end_at": None, "start_date": key,
                     "end_date": (dtend if dtend and dtend > dtstart else dtstart + timedelta(days=1)).isoformat()}
        else:
            key = iso(_aware(dtstart))
            times = {"start_at": key, "end_at": iso(_aware(dtend)) if dtend else None, "start_date": None, "end_date": None}
        out.append({
            "uid": f"{uid}/{key}" if uid in series else uid,
            "title": str(c.get("summary") or "").strip()[:200] or "(pealkirjata)",
            "all_day": 1 if all_day else 0,
            "location": str(c.get("location") or ""),
            "description": str(c.get("description") or ""),
            **times,
        })
    return out


def _aware(dt):
    # Floating times (no zone) in a feed mean local time here.
    return dt if dt.tzinfo else dt.replace(tzinfo=TALLINN)


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
