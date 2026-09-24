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
DTSTART;TZID=Europe/Tallinn:20261019T170000
DTEND;TZID=Europe/Tallinn:20261019T180000
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:kool-3
RECURRENCE-ID;TZID=Europe/Tallinn:20261026T170000
SUMMARY:Ringitund (teisel ajal)
DTSTART;TZID=Europe/Tallinn:20261027T150000
DTEND;TZID=Europe/Tallinn:20261027T160000
END:VEVENT
BEGIN:VEVENT
UID:kool-4
SUMMARY:Ujuvaeg
DTSTART:20261012T090000
END:VEVENT
BEGIN:VEVENT
UID:kool-5
SUMMARY:Pikk laager
DTSTART;VALUE=DATE:20260825
DTEND;VALUE=DATE:20260910
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_expands_and_keeps_original_starts(conn):
    items = ics.parse_ics(SAMPLE, date(2026, 9, 1), date(2027, 1, 1))
    got = {(i["uid"], i["recurrence_id"]): i for i in items}
    assert set(got) == {("kool-1", None), ("kool-2", None), ("kool-4", None), ("kool-3", "2026-10-19T14:00:00Z"),
                        ("kool-3", "2026-10-26T15:00:00Z"), ("kool-3", "2026-11-02T15:00:00Z")}
    assert got[("kool-1", None)] == {
        "uid": "kool-1", "recurrence_id": None, "title": "Lastevanemate koosolek", "all_day": 0,
        "start_at": "2026-10-08T15:00:00Z", "end_at": "2026-10-08T16:30:00Z", "start_date": None, "end_date": None,
        "location": "Aula", "description": ""}
    # The moved occurrence: its identity is the original Monday, its time the new Tuesday.
    moved = got[("kool-3", "2026-10-26T15:00:00Z")]
    assert (moved["title"], moved["start_at"]) == ("Ringitund (teisel ajal)", "2026-10-27T13:00:00Z")
    assert got[("kool-3", "2026-11-02T15:00:00Z")]["start_at"] == "2026-11-02T15:00:00Z"  # 17:00 EET
    assert (got[("kool-2", None)]["start_date"], got[("kool-2", None)]["end_date"]) == ("2026-10-26", "2026-10-31")
    assert got[("kool-4", None)]["start_at"] == "2026-10-12T06:00:00Z"  # floating time = Tallinn
    assert not any("rrule" in i for i in items)
    for i in items:  # the columns go straight into upsert_by_uid
        uid = f"ics:1:{i['uid']}" + (f":{i['recurrence_id']}" if i["recurrence_id"] else "")
        assert events.upsert_by_uid(conn, uid, {k: v for k, v in i.items() if k not in ("uid", "recurrence_id")})[1]


def test_parse_ics_window_counts_by_start_day():
    # The camp (25.08-9.09) began before the window: out, even though it overlaps it.
    assert sorted(i["uid"] for i in ics.parse_ics(SAMPLE, date(2026, 9, 5), date(2026, 10, 12))) == ["kool-1", "kool-4"]
    # Both edge days are in: 8.10 and 12.10.
    assert sorted(i["uid"] for i in ics.parse_ics(SAMPLE, date(2026, 10, 8), date(2026, 10, 12))) == ["kool-1", "kool-4"]
    assert ics.parse_ics(SAMPLE, date(2026, 10, 9), date(2026, 10, 11)) == []


def test_parse_ics_rejects_garbage():
    with pytest.raises(ValueError, match="ICS-fail on vigane."):
        ics.parse_ics(b"see pole kalender", date(2026, 1, 1), date(2027, 1, 1))


class FakeStream:
    def __init__(self, ip):
        self.ip = ip

    def get_extra_info(self, name):
        return (self.ip, 443) if name == "server_addr" else None


@pytest.fixture
def web(monkeypatch):
    """A fake internet: DNS answers from `dns`, `pages` maps URL -> (status, headers, body), and
    `peer` can make the connection land somewhere other than DNS said."""
    state = {"dns": {"kool.example": ["93.184.216.34"], "cdn.example": ["151.101.1.1"], "sisene.example": ["10.1.2.3"]},
             "pages": {}, "peer": None, "seen": []}

    def handler(request):
        url = str(request.url)
        state["seen"].append(url)
        status, headers, body = state["pages"].get(url, (404, {}, b""))
        ip = state["peer"] or state["dns"].get(request.url.host, ["93.184.216.34"])[0]
        return httpx.Response(status, headers=headers, content=body, extensions={"network_stream": FakeStream(ip)})
    monkeypatch.setattr(ics, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    def resolve(host, port):
        if host not in state["dns"]:
            raise ValueError("ICS-aadressi serverit ei leitud.")
        return state["dns"][host]
    monkeypatch.setattr(ics, "_resolve", resolve)
    monkeypatch.setattr(ics, "local_today", lambda: date(2026, 10, 1))
    return state


def test_fetch_ics_webcal_and_window(web):
    web["pages"]["https://kool.example/k.ics?token=SALAJANE"] = (200, {}, SAMPLE)
    items = ics.fetch_ics("webcal://kool.example/k.ics?token=SALAJANE")
    assert web["seen"] == ["https://kool.example/k.ics?token=SALAJANE"]
    assert ("kool-5", None) not in {(i["uid"], i["recurrence_id"]) for i in items}  # began 25.08, before 1.10 - 30
    assert (ics.WINDOW_PAST_DAYS, ics.WINDOW_FUTURE_DAYS) == (30, 365)


def test_fetch_ics_follows_up_to_three_checked_redirects(web):
    web["pages"].update({
        "https://kool.example/a": (302, {"location": "/b"}, b""),
        "https://kool.example/b": (301, {"location": "https://cdn.example/c"}, b""),
        "https://cdn.example/c": (307, {"location": "https://cdn.example/d"}, b""),
        "https://cdn.example/d": (200, {}, SAMPLE),
    })
    assert ics.fetch_ics("https://kool.example/a")
    web["pages"]["https://cdn.example/d"] = (302, {"location": "https://cdn.example/e"}, b"")
    with pytest.raises(ValueError, match="liiga palju kordi"):
        ics.fetch_ics("https://kool.example/a")


SECRET_URL_PART = "token=SALAJANE"


@pytest.mark.parametrize("url, message", [
    ("http://kool.example/k.ics?token=SALAJANE", "peab algama https://"),
    ("file:///etc/passwd?token=SALAJANE", "peab algama https://"),
    ("https://localhost/k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://intranet/k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://127.0.0.1/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://10.0.0.5/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://192.168.1.10/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://169.254.169.254/latest?token=SALAJANE", "sisevõrku"),
    ("https://100.122.97.7:8090/k.ics?token=SALAJANE", "sisevõrku"),  # the tailnet (100.64/10)
    ("https://[::1]/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://[::ffff:127.0.0.1]/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://sisene.example/k.ics?token=SALAJANE", "sisevõrku"),  # DNS points inside
    ("https://puudub.example/k.ics?token=SALAJANE", "serverit ei leitud"),
])
def test_fetch_ics_refuses_inside_addresses(web, url, message):
    with pytest.raises(ValueError, match=message) as err:
        ics.fetch_ics(url)
    assert SECRET_URL_PART not in str(err.value) and web["seen"] == []


def test_fetch_ics_refuses_a_redirect_inside(web):
    web["pages"]["https://kool.example/a"] = (302, {"location": "https://sisene.example/?token=SALAJANE"}, b"")
    with pytest.raises(ValueError, match="sisevõrku") as err:
        ics.fetch_ics("https://kool.example/a")
    assert web["seen"] == ["https://kool.example/a"] and SECRET_URL_PART not in str(err.value)


def test_fetch_ics_checks_the_connected_address(web):
    # DNS said public, the connection landed inside (rebinding): nothing is read.
    web["pages"]["https://kool.example/k.ics"] = (200, {}, SAMPLE)
    web["peer"] = "127.0.0.1"
    with pytest.raises(ValueError, match="sisevõrku"):
        ics.fetch_ics("https://kool.example/k.ics")


def test_fetch_ics_errors_never_show_the_url(web, monkeypatch):
    with pytest.raises(ValueError, match="vastas veaga 404") as err:
        ics.fetch_ics("https://kool.example/puudub?token=SALAJANE")
    assert SECRET_URL_PART not in str(err.value)

    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")
    monkeypatch.setattr(ics, "_client", lambda: httpx.Client(transport=httpx.MockTransport(broken)))
    with pytest.raises(ValueError, match="ei saanud alla laadida") as err:
        ics.fetch_ics("https://kool.example/k.ics?token=SALAJANE")
    assert SECRET_URL_PART not in str(err.value) and err.value.__cause__ is None and err.value.__suppress_context__


def test_feed_url(conn):
    assert ics.feed_url(conn) == f"/ics/{ics.feed_token(conn)}.ics"
