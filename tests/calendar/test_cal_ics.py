"""app/ics.py: the private feed (export), its token, and the import helper."""

import gzip
import ipaddress
import json
import time
import zlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpcore
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
    wrong_last = "y" if token.endswith("x") else "x"
    assert client.get(f"/ics/{token[:-1]}{wrong_last}.ics").status_code == 404
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


@pytest.fixture
def web(monkeypatch):
    """A fake web behind httpx's MockTransport, for the request flow (the address check at
    connect time is tested with `net` below): `pages` maps URL -> (status, headers, body)."""
    state = {"pages": {}, "seen": []}

    def handler(request):
        url = str(request.url)
        state["seen"].append(url)
        state["user_agent"] = request.headers.get("user-agent")
        status, headers, body = state["pages"].get(url, (404, {}, b""))
        return httpx.Response(status, headers=headers, content=iter([body]))  # streamed, as from a server
    monkeypatch.setattr(ics, "_client", lambda deadline: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ics, "local_today", lambda: date(2026, 10, 1))
    return state


@pytest.fixture
def net(monkeypatch):
    """The real client and its checking network backend, with fake DNS and fake sockets: each
    connect records the address and replays the canned HTTP bytes for that address."""
    state = {"dns": {"kool.example": ["93.184.216.34"], "sisene.example": ["10.1.2.3"],
                     "segane.example": ["93.184.216.34", "10.0.0.1"]},
             "replies": {}, "connected": []}

    def resolve(host, port, deadline=None):
        if host in state["dns"]:
            return state["dns"][host]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            raise ValueError("ICS-aadressi serverit ei leitud.") from None

    def connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        state["connected"].append(host)
        return httpcore.MockStream(list(state["replies"].get(host, [])))
    monkeypatch.setattr(ics, "_resolve", resolve)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(ics, "local_today", lambda: date(2026, 10, 1))
    return state


def http(status, headers=(), body=b""):
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", *headers, "", ""]
    return ["\r\n".join(lines).encode(), body]


def test_download_can_send_a_user_agent(web):
    web["pages"]["https://uudised.example/a"] = (200, {}, b"<p>leht</p>")
    assert ics.download("https://uudised.example/a", user_agent="Markmik a@b.ee") == b"<p>leht</p>"
    assert web["user_agent"] == "Markmik a@b.ee"
    ics.download("https://uudised.example/a")
    assert web["user_agent"].startswith("python-httpx")


def test_fetch_ics_webcal_and_window(web):
    web["pages"]["https://kool.example/k.ics?token=SALAJANE"] = (200, {}, SAMPLE)
    items = ics.fetch_ics("webcal://kool.example/k.ics?token=SALAJANE")
    assert web["seen"] == ["https://kool.example/k.ics?token=SALAJANE"]
    assert ("kool-5", None) not in {(i["uid"], i["recurrence_id"]) for i in items}  # began 25.08, before 1.10 - 30
    assert (ics.WINDOW_PAST_DAYS, ics.WINDOW_FUTURE_DAYS) == (30, 365)


def test_fetch_ics_follows_up_to_three_redirects(web):
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


def test_fetch_ics_stops_big_and_gzip_bomb_bodies(web):
    web["pages"]["https://kool.example/suur.ics"] = (200, {}, b"x" * (ics.MAX_BYTES + 1))
    web["pages"]["https://kool.example/pomm.ics"] = (200, {"content-encoding": "gzip"}, gzip.compress(b"\0" * 20_000_000))
    for name in ("suur", "pomm"):
        with pytest.raises(ValueError, match="liiga suur"):
            ics.fetch_ics(f"https://kool.example/{name}.ics")


def test_fetch_ics_takes_one_gzip_or_deflate_layer_only(web):
    def deflate(data):
        return zlib.compress(data)
    web["pages"]["https://kool.example/zlib.ics"] = (200, {"content-encoding": "deflate"}, deflate(SAMPLE))
    web["pages"]["https://kool.example/gz.ics"] = (200, {"content-encoding": "gzip"}, gzip.compress(SAMPLE))
    assert ics.fetch_ics("https://kool.example/zlib.ics") == ics.fetch_ics("https://kool.example/gz.ics") != []
    # Stacked layers multiply a bomb (1 KB -> 1 GB); other codings cannot be inflated in bounds.
    for coding in ("gzip, gzip", "gzip, deflate", "br", "compress"):
        web["pages"]["https://kool.example/x.ics"] = (200, {"content-encoding": coding}, gzip.compress(gzip.compress(SAMPLE)))
        with pytest.raises(ValueError, match="pakkimisviisi ei toetata"):
            ics.fetch_ics("https://kool.example/x.ics")
    web["pages"]["https://kool.example/katki.ics"] = (200, {"content-encoding": "gzip"}, b"not gzip at all")
    with pytest.raises(ValueError, match="ICS-fail on vigane"):
        ics.fetch_ics("https://kool.example/katki.ics")


def test_fetch_ics_has_a_total_time_limit(web, monkeypatch):
    web["pages"]["https://kool.example/k.ics"] = (200, {}, SAMPLE)
    monkeypatch.setattr(ics, "TIME_LIMIT_S", -1)  # already over when the first bytes arrive
    with pytest.raises(ValueError, match="liiga kaua"):
        ics.fetch_ics("https://kool.example/k.ics")


SECRET_URL_PART = "token=SALAJANE"


@pytest.mark.parametrize("url, message", [
    ("http://kool.example/k.ics?token=SALAJANE", "peab algama https://"),
    ("file:///etc/passwd?token=SALAJANE", "peab algama https://"),
    ("https://localhost/k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://localhost./k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://intranet/k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://2130706433/k.ics?token=SALAJANE", "server pole lubatud"),
    ("https://127.0.0.1/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://10.0.0.5/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://192.168.1.10/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://169.254.169.254/latest?token=SALAJANE", "sisevõrku"),
    ("https://100.122.97.7:8090/k.ics?token=SALAJANE", "sisevõrku"),  # the tailnet (100.64/10)
    ("https://[::1]/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://[::ffff:127.0.0.1]/k.ics?token=SALAJANE", "sisevõrku"),
    ("https://[::7f00:1]/k.ics?token=SALAJANE", "sisevõrku"),  # IPv4-compatible
    ("https://[64:ff9b::a00:1]/k.ics?token=SALAJANE", "sisevõrku"),  # NAT64 of 10.0.0.1
    ("https://[2002:7f00:1::]/k.ics?token=SALAJANE", "sisevõrku"),  # 6to4 of 127.0.0.1
    ("https://[fec0::1]/k.ics?token=SALAJANE", "sisevõrku"),  # site-local
    ("https://[::ffff:0:a00:1]/k.ics?token=SALAJANE", "sisevõrku"),  # IPv4-translated 10.0.0.1
    ("https://sisene.example/k.ics?token=SALAJANE", "sisevõrku"),  # DNS points inside
    ("https://segane.example/k.ics?token=SALAJANE", "sisevõrku"),  # one of its addresses is inside
    ("https://puudub.example/k.ics?token=SALAJANE", "serverit ei leitud"),
])
def test_fetch_ics_refuses_inside_addresses_before_connecting(net, url, message):
    with pytest.raises(ValueError, match=message) as err:
        ics.fetch_ics(url)
    assert SECRET_URL_PART not in str(err.value) and net["connected"] == []


def test_fetch_ics_connects_to_the_checked_address(net):
    net["replies"]["93.184.216.34"] = http(200, body=SAMPLE)
    assert ics.fetch_ics("https://kool.example/k.ics")
    assert net["connected"] == ["93.184.216.34"]


def test_fetch_ics_tries_each_checked_address_in_resolver_order(net, monkeypatch):
    net["dns"]["kool.example"] = ["151.101.1.1", "93.184.216.34"]
    net["replies"]["93.184.216.34"] = http(200, body=SAMPLE)
    real_connect = httpcore.SyncBackend.connect_tcp

    def first_is_down(self, host, port, *args, **kwargs):
        if host == "151.101.1.1":
            net["connected"].append(host)
            raise httpcore.ConnectError("down")
        return real_connect(self, host, port, *args, **kwargs)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", first_is_down)
    assert ics.fetch_ics("https://kool.example/k.ics")
    assert net["connected"] == ["151.101.1.1", "93.184.216.34"]


def test_resolve_keeps_the_resolver_order(monkeypatch):
    answers = [(2, 1, 6, "", ("151.101.1.1", 443)), (2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("151.101.1.1", 443))]
    monkeypatch.setattr(ics.socket, "getaddrinfo", lambda *a, **k: answers)
    assert ics._resolve("kool.example", 443) == ["151.101.1.1", "93.184.216.34"]


def test_the_deadline_covers_connecting_and_every_read(net, monkeypatch):
    monkeypatch.setattr(ics, "TIME_LIMIT_S", -1)
    with pytest.raises(ValueError, match="liiga kaua"):
        ics.fetch_ics("https://kool.example/k.ics")
    assert net["connected"] == []  # not even connected

    class Recorder:
        def __init__(self):
            self.timeouts = []

        def read(self, max_bytes, timeout=None):
            self.timeouts.append(timeout)
            return b"X"  # a header trickling in one byte at a time
    raw = Recorder()
    stream = ics._DeadlineStream(raw, time.monotonic() + 3)
    stream.read(1, timeout=10)
    assert 2 < raw.timeouts[0] <= 3  # never longer than what is left
    stream = ics._DeadlineStream(raw, time.monotonic() - 0.1)
    with pytest.raises(ValueError, match="liiga kaua"):
        stream.read(1, timeout=10)


def test_fetch_ics_checks_every_redirect_hop(net):
    net["replies"]["93.184.216.34"] = http(302, ["Location: https://sisene.example/?token=SALAJANE"])
    with pytest.raises(ValueError, match="sisevõrku") as err:
        ics.fetch_ics("https://kool.example/a")
    assert net["connected"] == ["93.184.216.34"] and SECRET_URL_PART not in str(err.value)


def test_fetch_ics_errors_never_show_the_url(web, monkeypatch):
    with pytest.raises(ValueError, match="vastas veaga 404") as err:
        ics.fetch_ics("https://kool.example/puudub?token=SALAJANE")
    assert SECRET_URL_PART not in str(err.value)

    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")
    monkeypatch.setattr(ics, "_client", lambda deadline: httpx.Client(transport=httpx.MockTransport(broken)))
    with pytest.raises(ValueError, match="ei saanud alla laadida") as err:
        ics.fetch_ics("https://kool.example/k.ics?token=SALAJANE")
    assert SECRET_URL_PART not in str(err.value) and err.value.__cause__ is None and err.value.__suppress_context__


def cal(*vevents):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + "".join(
        "BEGIN:VEVENT\r\n" + v + "END:VEVENT\r\n" for v in vevents) + "END:VCALENDAR\r\n").encode()


def test_parse_ics_leaves_out_sub_daily_series_quickly():
    data = cal("UID:m\r\nSUMMARY:x\r\nDTSTART:20260901T000000Z\r\nRRULE:FREQ=MINUTELY\r\n",
               "UID:h\r\nSUMMARY:x\r\nDTSTART:20260901T000000Z\r\nRRULE:FREQ=DAILY;BYHOUR=8,9,10\r\n",
               "UID:ok\r\nSUMMARY:x\r\nDTSTART:20261001T080000Z\r\n")
    started = time.monotonic()
    assert [i["uid"] for i in ics.parse_ics(data, date(2026, 8, 25), date(2027, 9, 24))] == ["ok"]
    assert time.monotonic() - started < 2


def test_parse_ics_refuses_too_many_occurrences(monkeypatch):
    monkeypatch.setattr(ics, "MAX_OCCURRENCES", 3)
    data = cal("UID:d\r\nSUMMARY:x\r\nDTSTART;VALUE=DATE:20261001\r\nRRULE:FREQ=DAILY\r\n")
    with pytest.raises(ValueError, match="liiga palju sündmusi"):
        ics.parse_ics(data, date(2026, 10, 1), date(2026, 12, 1))
    assert len(ics.parse_ics(data, date(2026, 10, 1), date(2026, 10, 3))) == 3


def test_parse_ics_has_a_time_limit():
    data = cal("UID:ok\r\nSUMMARY:x\r\nDTSTART:20261001T080000Z\r\n")
    with pytest.raises(ValueError, match="liiga kaua"):
        ics.parse_ics(data, date(2026, 9, 1), date(2027, 1, 1), deadline=time.monotonic() - 1)


def test_parse_ics_odd_items():
    data = cal("SUMMARY:Ilma UID-ta\r\nDTSTART:20261001T100000Z\r\n",
               "UID:puudub-algus\r\nSUMMARY:x\r\n",
               "UID:pikk\r\nSUMMARY:x\r\nDTSTART:20261002T100000Z\r\nLOCATION:" + "L" * 900 + "\r\nDESCRIPTION:" + "A" * 2_000_000 + "\r\n",
               "UID:segatud\r\nSUMMARY:x\r\nDTSTART;VALUE=DATE:20261003\r\nDTEND:20261004T100000Z\r\n")
    first = ics.parse_ics(data, date(2026, 9, 1), date(2027, 1, 1))
    got = {i["uid"]: i for i in first}
    no_uid = [u for u in got if u.startswith("nouid-")]
    assert len(no_uid) == 1 and no_uid == [i["uid"] for i in ics.parse_ics(data, date(2026, 9, 1), date(2027, 1, 1)) if i["uid"].startswith("nouid-")]
    assert "puudub-algus" not in got  # the broken one is skipped, the rest still come
    assert (len(got["pikk"]["location"]), len(got["pikk"]["description"])) == (500, 5000)
    # A DATE start with a DATE-TIME end comes through as a timed item, not an error.
    assert got["segatud"]["start_at"] <= got["segatud"]["end_at"]


def test_feed_url(conn):
    assert ics.feed_url(conn) == f"/ics/{ics.feed_token(conn)}.ics"


def test_parse_ics_leaves_out_rules_the_expander_could_spin_on():
    impossible = "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30"
    data = cal("UID:kaks\r\nSUMMARY:x\r\nDTSTART:00010101T100000Z\r\n" + "".join(f"RRULE:{impossible};INTERVAL={i}\r\n" for i in range(1, 9)),
               "UID:ex\r\nSUMMARY:x\r\nDTSTART:20261001T100000Z\r\nRRULE:FREQ=DAILY\r\nEXRULE:FREQ=WEEKLY\r\n",
               "UID:pos\r\nSUMMARY:x\r\nDTSTART:20260101T100000Z\r\nRRULE:" + impossible + ";BYSETPOS=" + ",".join(map(str, range(1, 41))) + "\r\n",
               "UID:lihavotted\r\nSUMMARY:x\r\nDTSTART:20260101T100000Z\r\nRRULE:FREQ=YEARLY;BYEASTER=0\r\n",
               "UID:ok\r\nSUMMARY:x\r\nDTSTART:20261001T100000Z\r\nRRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1,-1\r\n")
    started = time.monotonic()
    assert {i["uid"] for i in ics.parse_ics(data, date(2026, 8, 25), date(2027, 9, 24))} == {"ok"}
    assert time.monotonic() - started < 2


def test_fetch_ics_stops_reading_at_the_end_of_the_gzip_stream(web, monkeypatch):
    sent = []

    def body():
        sent.append(1)
        yield gzip.compress(SAMPLE)
        for _ in range(200):  # 200 MB of junk after the gzip end
            sent.append(1)
            yield b"\0" * (1024 * 1024)

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=body())
    monkeypatch.setattr(ics, "_client", lambda deadline: httpx.Client(transport=httpx.MockTransport(handler)))
    assert ics.fetch_ics("https://kool.example/k.ics")
    assert len(sent) <= 2


def test_slow_dns_cannot_outlast_the_deadline(monkeypatch):
    monkeypatch.setattr(ics.socket, "getaddrinfo", lambda *a, **k: time.sleep(2) or [])
    started = time.monotonic()
    with pytest.raises(ValueError, match="liiga kaua"):
        ics._resolve("kool.example", 443, time.monotonic() + 0.3)
    assert time.monotonic() - started < 1
