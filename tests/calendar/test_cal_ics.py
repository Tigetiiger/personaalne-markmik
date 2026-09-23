"""app/ics.py: the private feed (export), its token, and the import helper."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import recurring_ical_events
from icalendar import Calendar

from app import connections, events, ics

TALLINN = ZoneInfo("Europe/Tallinn")


def feed_of(conn):
    return Calendar.from_ical(ics.feed(conn))


def by_summary(cal):
    return {str(e["summary"]): e for e in cal.walk("VEVENT")}


def test_feed_parses_back_with_timezone(conn, add):
    add(title="Koosolek", start_at="2026-10-07T07:00:00Z", end_at="2026-10-07T08:30:00Z", location="Aula", description="Tuua kaust")
    add(title="Laager", all_day=1, start_date="2026-10-05", end_date="2026-10-08")
    raw = ics.feed(conn).decode()
    assert "BEGIN:VTIMEZONE" in raw and "TZID:Europe/Tallinn" in raw
    assert "DTSTART;TZID=Europe/Tallinn:20261007T100000" in raw
    assert "DTSTART;VALUE=DATE:20261005" in raw
    cal = Calendar.from_ical(raw)
    assert [str(tz["tzid"]) for tz in cal.walk("VTIMEZONE")] == ["Europe/Tallinn"]
    ev = by_summary(cal)
    meeting = ev["Koosolek"]
    assert meeting["dtstart"].params["TZID"] == "Europe/Tallinn"
    assert meeting["dtstart"].dt == datetime(2026, 10, 7, 10, 0, tzinfo=TALLINN)
    assert meeting["dtend"].dt == datetime(2026, 10, 7, 11, 30, tzinfo=TALLINN)
    assert (str(meeting["location"]), str(meeting["description"])) == ("Aula", "Tuua kaust")
    assert (ev["Laager"]["dtstart"].dt, ev["Laager"]["dtend"].dt) == (date(2026, 10, 5), date(2026, 10, 8))


def test_feed_has_only_confirmed_events(conn, add):
    add(title="kindel")
    add(title="pakutud", status="pakutud")
    removed = add(title="eemaldatud")
    events.remove(conn, removed)
    assert set(by_summary(feed_of(conn))) == {"kindel"}


def test_feed_series_with_exdates_and_overrides(conn, add):
    series = add(title="Mate", kind="tund", start_at="2026-10-19T05:00:00Z", end_at="2026-10-19T05:45:00Z",
                 rrule="FREQ=WEEKLY;BYDAY=MO", exdates_json=json.dumps(["2026-11-02T06:00:00Z"]))
    add(title="Mate asendus", start_at="2026-10-26T08:00:00Z", series_id=series, recurrence_id="2026-10-26T06:00:00Z")
    add(title="Mate", start_at="2026-11-09T06:00:00Z", series_id=series, recurrence_id="2026-11-09T06:00:00Z", status="eemaldatud")
    raw = ics.feed(conn).decode()
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in raw
    assert "RECURRENCE-ID;TZID=Europe/Tallinn:20261026T080000" in raw
    cal = Calendar.from_ical(raw)
    uids = {str(e["uid"]) for e in cal.walk("VEVENT")}
    assert uids == {events.get(conn, series)["uid"]}  # the override travels under the series' UID
    # A phone expanding the feed sees 08:00 local after the DST change, the override at 10:00,
    # and neither the EXDATE nor the cancelled occurrence.
    found = sorted(recurring_ical_events.of(cal).between(date(2026, 10, 19), date(2026, 11, 17)), key=lambda o: o["dtstart"].dt)
    assert [(str(o["summary"]), o["dtstart"].dt.astimezone(TALLINN).strftime("%d.%m %H:%M")) for o in found] == [
        ("Mate", "19.10 08:00"), ("Mate asendus", "26.10 10:00"), ("Mate", "16.11 08:00")]


def test_feed_endpoint_checks_token(client, conn, add):
    add(title="Koosolek")
    assert client.get("/ics/midagi.ics").status_code == 404  # no token made yet
    token = ics.feed_token(conn)
    assert ics.feed_token(conn) == token and len(token) >= 32
    r = client.get(f"/ics/{token}.ics")  # anonymous: the token is the key
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert "SUMMARY:Koosolek" in r.get_data(as_text=True)
    assert client.get(f"/ics/{token[:-1]}x.ics").status_code == 404
    assert client.get(f"/ics/{token}x.ics").status_code == 404


def test_phone_connection_masks_the_token(conn):
    result = connections.run_test(conn, "phone")
    token = ics.feed_token(conn)
    assert result["ok"] and token[-4:] in result["summary"] and token not in result["summary"]
    assert connections.get("phone").order == 70


SAMPLE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kool//x//ET
BEGIN:VEVENT
UID:kool-1
SUMMARY:Lastevanemate koosolek
DTSTART;TZID=Europe/Tallinn:20261008T180000
DTEND;TZID=Europe/Tallinn:20261008T193000
LOCATION:Aula
END:VEVENT
BEGIN:VEVENT
UID:kool-2
SUMMARY:Koolivaheaeg
DTSTART;VALUE=DATE:20261026
DTEND;VALUE=DATE:20261031
END:VEVENT
BEGIN:VEVENT
UID:kool-3
SUMMARY:Ringitund
DTSTART:20261019T150000Z
DTEND:20261019T160000Z
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:kool-4
SUMMARY:Ujuvaeg
DTSTART:20261012T090000
END:VEVENT
BEGIN:VEVENT
UID:kool-5
SUMMARY:Ammu
DTSTART;VALUE=DATE:20250101
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_gives_upsertable_dicts(conn):
    items = ics.parse_ics(SAMPLE, date(2026, 9, 1), date(2027, 1, 1))
    got = {i["uid"]: i for i in items}
    assert set(got) == {"kool-1", "kool-2", "kool-4", "kool-3/2026-10-19T15:00:00Z", "kool-3/2026-10-26T15:00:00Z",
                        "kool-3/2026-11-02T15:00:00Z"}
    assert got["kool-1"] == {"uid": "kool-1", "title": "Lastevanemate koosolek", "all_day": 0,
                             "start_at": "2026-10-08T15:00:00Z", "end_at": "2026-10-08T16:30:00Z",
                             "start_date": None, "end_date": None, "location": "Aula", "description": ""}
    assert (got["kool-2"]["start_date"], got["kool-2"]["end_date"], got["kool-2"]["all_day"]) == ("2026-10-26", "2026-10-31", 1)
    assert got["kool-4"]["start_at"] == "2026-10-12T06:00:00Z"  # floating time = Tallinn
    for uid, fields in got.items():  # every item goes straight into upsert_by_uid
        assert events.upsert_by_uid(conn, uid, {k: v for k, v in fields.items() if k != "uid"})[1]


def test_parse_ics_rejects_garbage():
    with pytest.raises(ValueError, match="ICS-fail on vigane."):
        ics.parse_ics(b"see pole kalender", date(2026, 1, 1), date(2027, 1, 1))


def test_fetch_ics_uses_https_for_webcal(monkeypatch):
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return httpx.Response(200, content=SAMPLE, request=httpx.Request("GET", url))
    monkeypatch.setattr(ics.httpx, "get", fake_get)
    items = ics.fetch_ics("webcal://kool.example/kalender.ics", days_back=3650, days_ahead=3650)
    assert seen == ["https://kool.example/kalender.ics"]
    assert "kool-5" in {i["uid"] for i in items}
    with pytest.raises(ValueError, match="ICS-aadress"):
        ics.fetch_ics("file:///etc/passwd")
