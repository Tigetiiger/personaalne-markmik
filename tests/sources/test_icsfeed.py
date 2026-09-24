"""ICS feeds (app/sources/icsfeed.py) with app.ics.fetch_ics (WP-D) replaced by a fake module."""

import sys
import types

import pytest

from app import connections, events
from app.sources import base, icsfeed

FEED = [
    {"uid": "lesson@school", "title": "Keemia", "kind": "tund", "start_at": "2026-10-05T05:00:00Z",
     "end_at": "2026-10-05T05:45:00Z", "tzid": "Europe/Tallinn", "all_day": 0, "rrule": "FREQ=WEEKLY;COUNT=10",
     "status": "eemaldatud", "user_edited": 1},  # a feed cannot set these two
    {"uid": "lesson@school", "recurrence_id": "2026-10-12T05:00:00Z", "title": "Keemia (ruum 204)", "kind": "tund",
     "start_at": "2026-10-12T06:00:00Z", "end_at": "2026-10-12T06:45:00Z", "all_day": 0},
    {"uid": "day@school", "title": "Karjääripäev", "start_date": "2026-10-14", "end_date": "2026-10-15", "all_day": 1},
]


@pytest.fixture
def feed(monkeypatch):
    calls = []

    def fetch_ics(url):
        calls.append(url)
        return [dict(e) for e in FEED]
    monkeypatch.setitem(sys.modules, "app.ics", types.SimpleNamespace(fetch_ics=fetch_ics))
    return calls


def test_add_list_validate(conn):
    sid = icsfeed.add_feed(conn, "webcal://kool.example/cal.ics", "")
    assert icsfeed.feeds(conn) == [{"id": sid, "name": "kool.example", "url": "https://kool.example/cal.ics",
                                    "enabled": True, "last_ok_at": None, "last_error": None}]
    for bad in ("", "ftp://x/y.ics", "kool.example/cal.ics"):
        with pytest.raises(ValueError):
            icsfeed.add_feed(conn, bad, "x")
    with pytest.raises(ValueError, match="juba olemas"):
        icsfeed.add_feed(conn, "https://other.example/a.ics", "kool.example")


def test_sync_upserts_events(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    assert base.run_source(conn, sid) == {"ok": True, "items": 3, "error": None}
    assert feed == ["https://kool.example/cal.ics"]
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM events")}
    assert set(rows) == {f"ics:{sid}:lesson@school", f"ics:{sid}:lesson@school:2026-10-12T05:00:00Z",
                         f"ics:{sid}:day@school"}
    series = rows[f"ics:{sid}:lesson@school"]
    assert (series["status"], series["source"], series["user_edited"], series["rrule"], series["source_ref"]) == (
        "kinnitatud", "ics", 0, "FREQ=WEEKLY;COUNT=10", "lesson@school")
    override = rows[f"ics:{sid}:lesson@school:2026-10-12T05:00:00Z"]
    assert (override["series_id"], override["recurrence_id"]) == (series["id"], "2026-10-12T05:00:00Z")
    assert rows[f"ics:{sid}:day@school"]["start_date"] == "2026-10-14"
    assert base.run_source(conn, sid)["items"] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_changed_event_updates(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    base.run_source(conn, sid)
    FEED[2]["title"], title = "Karjääripäev 11. klassidele", FEED[2]["title"]
    try:
        assert base.run_source(conn, sid)["items"] == 1
    finally:
        FEED[2]["title"] = title
    assert conn.execute("SELECT title FROM events WHERE uid = ?", (f"ics:{sid}:day@school",)).fetchone()[0] == \
        "Karjääripäev 11. klassidele"


def test_remove_feed_takes_its_events(conn, feed):
    sid = icsfeed.add_feed(conn, "https://kool.example/cal.ics", "Kool")
    other = events.create(conn, {"uid": "ics:99:x", "title": "Muu", "all_day": 1, "start_date": "2026-10-14"})
    base.run_source(conn, sid)
    conn.execute("UPDATE events SET user_edited = 1 WHERE uid = ?", (f"ics:{sid}:day@school",))
    icsfeed.remove_feed(conn, sid)
    status = dict(conn.execute("SELECT uid, status FROM events").fetchall())
    assert status[f"ics:{sid}:lesson@school"] == "eemaldatud"
    assert status[f"ics:{sid}:day@school"] == "kinnitatud"  # the user's version stays
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
