"""ICS feeds (app/sources/icsfeed.py) with app.ics.fetch_ics (WP-D) replaced by a fake module."""

import ipaddress
import sys
import types
from datetime import date

import pytest

from app import connections, events
from app.sources import base, icsfeed

# fetch_ics's output per the contract: expanded occurrences, each with uid and recurrence_id
# (the original start). The second lesson was moved an hour later but keeps its identity.
FEED = [
    {"uid": "lesson@school", "recurrence_id": "2026-10-05T05:00:00Z", "title": "Keemia", "kind": "tund",
     "start_at": "2026-10-05T05:00:00Z", "end_at": "2026-10-05T05:45:00Z", "tzid": "Europe/Tallinn", "all_day": 0,
     "status": "eemaldatud", "user_edited": 1, "series_id": 5},  # a feed cannot set these
    {"uid": "lesson@school", "recurrence_id": "2026-10-12T05:00:00Z", "title": "Keemia (ruum 204)", "kind": "tund",
     "start_at": "2026-10-12T06:00:00Z", "end_at": "2026-10-12T06:45:00Z", "all_day": 0},
    {"uid": "day@school", "recurrence_id": "2026-10-14", "title": "Karjääripäev", "start_date": "2026-10-14",
     "end_date": "2026-10-15", "all_day": 1},
]
LESSON, MOVED, DAY = "lesson@school:2026-10-05T05:00:00Z", "lesson@school:2026-10-12T05:00:00Z", "day@school:2026-10-14"


HOSTS = {"kool.example": {"93.184.216.34"}, "other.example": {"2606:2800:220:1::1"},
         "vm.tail1234.ts.net": {"100.122.97.7"}, "router.example": {"192.168.1.1"},
         "sneaky.example": {"93.184.216.35", "127.0.0.1"}}


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """No DNS lookups in tests: names resolve from HOSTS."""
    def addresses(host):
        try:
            return {str(ipaddress.ip_address(host))}  # a literal address resolves to itself
        except ValueError:
            pass
        if host not in HOSTS:
            raise ValueError("Sellist aadressi ei leitud.")
        return HOSTS[host]
    monkeypatch.setattr(icsfeed, "_addresses", addresses)


@pytest.fixture
def feed(monkeypatch):
    """fetch_ics returns FEED (a copy); the list it returns can be changed through feed.items."""
    class Feed:
        items, calls = [dict(e) for e in FEED], []

    def fetch_ics(url):
        Feed.calls.append(url)
        return [dict(e) for e in Feed.items]
    monkeypatch.setitem(sys.modules, "app.ics", types.SimpleNamespace(
        fetch_ics=fetch_ics, WINDOW_PAST_DAYS=30, WINDOW_FUTURE_DAYS=365))
    monkeypatch.setattr(icsfeed, "local_today", lambda: date(2026, 10, 1))
    Feed.items, Feed.calls = [dict(e) for e in FEED], []
    return Feed


def test_add_list_validate(conn):
    sid = icsfeed.add_feed(conn, "webcal://kool.example/cal.ics", "")
    assert icsfeed.feeds(conn) == [{"id": sid, "name": "kool.example", "url": "https://kool.example/cal.ics",
                                    "enabled": True, "last_ok_at": None, "last_error": None}]
    for bad in ("", "ftp://x/y.ics", "kool.example/cal.ics", "http://kool.example/cal.ics", "https://nowhere.example/"):
        with pytest.raises(ValueError):
            icsfeed.add_feed(conn, bad, "x")
    with pytest.raises(ValueError, match="juba olemas"):
        icsfeed.add_feed(conn, "https://other.example/a.ics", "kool.example")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/cal.ics", "https://10.0.0.5/cal.ics", "https://169.254.169.254/latest",
    "https://[::1]/cal.ics", "https://localhost/cal.ics", "https://intranet/cal.ics",
    "https://vm.tail1234.ts.net/cal.ics", "https://router.example/cal.ics", "https://sneaky.example/cal.ics",
])
def test_add_feed_refuses_local_networks(conn, url):
    with pytest.raises(ValueError):
        icsfeed.add_feed(conn, url, "x")
    assert icsfeed.feeds(conn) == []


def test_sync_upserts_events(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    assert base.run_source(conn, sid) == {"ok": True, "items": 3, "error": None}
    assert feed.calls == ["https://kool.example/cal.ics"]
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM events")}
    assert set(rows) == {f"ics:{sid}:{LESSON}", f"ics:{sid}:{MOVED}", f"ics:{sid}:{DAY}"}
    lesson = rows[f"ics:{sid}:{LESSON}"]
    assert (lesson["status"], lesson["source"], lesson["user_edited"], lesson["series_id"], lesson["rrule"],
            lesson["source_ref"]) == ("kinnitatud", "ics", 0, None, None, "lesson@school")
    assert rows[f"ics:{sid}:{MOVED}"]["start_at"] == "2026-10-12T06:00:00Z"
    assert rows[f"ics:{sid}:{DAY}"]["start_date"] == "2026-10-14"
    assert base.run_source(conn, sid)["items"] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_changed_event_updates(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)
    feed.items[2]["title"] = "Karjääripäev 11. klassidele"
    assert base.run_source(conn, sid)["items"] == 1
    assert conn.execute("SELECT title FROM events WHERE uid = ?", (f"ics:{sid}:{DAY}",)).fetchone()[0] == \
        "Karjääripäev 11. klassidele"


def test_event_gone_from_feed_is_removed_and_can_return(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)
    conn.execute("UPDATE events SET user_edited = 1 WHERE uid = ?", (f"ics:{sid}:{LESSON}",))
    everything = feed.items
    feed.items = everything[1:2]  # the first lesson and the career day are gone, the moved lesson stays
    assert base.run_source(conn, sid)["ok"]
    status = dict(conn.execute("SELECT uid, status FROM events").fetchall())
    assert status == {f"ics:{sid}:{LESSON}": "kinnitatud",  # the user's edit stays
                      f"ics:{sid}:{MOVED}": "kinnitatud", f"ics:{sid}:{DAY}": "eemaldatud"}
    assert conn.execute("SELECT COUNT(*) FROM rejected_uids").fetchone()[0] == 0
    feed.items = everything  # back in the feed, unchanged
    base.run_source(conn, sid)
    assert conn.execute("SELECT status FROM events WHERE uid = ?", (f"ics:{sid}:{DAY}",)).fetchone()[0] == "kinnitatud"


def test_only_occurrences_inside_the_window_can_vanish(conn, feed, monkeypatch):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)
    # Two weeks on, fetch_ics's window starts after the first lesson: its absence says nothing.
    monkeypatch.setattr(icsfeed, "local_today", lambda: date(2026, 11, 5))
    feed.items = feed.items[1:]
    assert base.run_source(conn, sid)["ok"]
    assert conn.execute("SELECT status FROM events WHERE uid = ?", (f"ics:{sid}:{LESSON}",)).fetchone()[0] == "kinnitatud"
    feed.items = feed.items[:1]  # the career day, inside the window, is gone
    base.run_source(conn, sid)
    assert conn.execute("SELECT status FROM events WHERE uid = ?", (f"ics:{sid}:{DAY}",)).fetchone()[0] == "eemaldatud"


def test_empty_feed_removes_nothing(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)
    feed.items = []
    assert base.run_source(conn, sid)["ok"]
    assert {r[0] for r in conn.execute("SELECT status FROM events")} == {"kinnitatud"}


def test_failed_fetch_removes_nothing(conn, feed, monkeypatch):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)

    def unusable(url):
        raise ValueError("See pole kalendrifail.")
    monkeypatch.setitem(sys.modules, "app.ics", types.SimpleNamespace(fetch_ics=unusable))
    assert base.run_source(conn, sid)["error"] == "See pole kalendrifail."
    assert {r[0] for r in conn.execute("SELECT status FROM events")} == {"kinnitatud"}


def test_remove_feed_takes_its_events(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    other = events.create(conn, {"uid": "ics:99:x", "title": "Muu", "all_day": 1, "start_date": "2026-10-14"})
    base.run_source(conn, sid)
    conn.execute("UPDATE events SET user_edited = 1 WHERE uid = ?", (f"ics:{sid}:{DAY}",))
    icsfeed.remove_feed(conn, sid)
    status = dict(conn.execute("SELECT uid, status FROM events").fetchall())
    assert status[f"ics:{sid}:{LESSON}"] == "eemaldatud"
    assert status[f"ics:{sid}:{DAY}"] == "kinnitatud"  # the user's version stays
    assert conn.execute("SELECT status FROM events WHERE id = ?", (other,)).fetchone()[0] == "kinnitatud"
    assert icsfeed.feeds(conn) == []


def test_connection_test(conn, feed, monkeypatch):
    assert icsfeed.test_connection(conn)["error"] == "Ühtegi kalendrivoogu pole lisatud."
    icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    assert icsfeed.test_connection(conn) == {"ok": True, "summary": "Vooge: 1, sündmusi: 3", "error": None}

    def broken(url):
        raise OSError(f"cannot fetch {url}?token=secret")
    monkeypatch.setitem(sys.modules, "app.ics", types.SimpleNamespace(fetch_ics=broken))
    result = icsfeed.test_connection(conn)
    assert result["error"] == "Kool: Ootamatu viga (OSError), vaata logi."
    assert connections.get("ics").order == 60
