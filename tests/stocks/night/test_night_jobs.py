"""The stocks jobs (app/stocks/jobs.py) and the night's thread: a missed night before and after
06:00, the claim, the worker going on with other jobs while a night runs, market hours in the
weeks when US and EU daylight saving differ, the filings poll kept out of the night."""

import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from night_helpers import local

from app import jobs as registry
from app import worker
from app.stocks import alerts, common, market, night, sources
from app.stocks import jobs as stock_jobs

DAY = date(2026, 9, 30)


@pytest.fixture
def started(monkeypatch):
    """night.start recorded instead of run."""
    calls = []
    monkeypatch.setattr(night, "start", lambda db_path, run_date: calls.append((db_path, run_date)) or True)
    return calls


def test_the_night_job_starts_tonights_run(conn, follow, started):
    follow("AAA")
    stock_jobs.nightly(conn, local(DAY, 1, 30))
    assert [d for _, d in started] == [DAY]
    assert started[0][0].endswith("test.db")


def test_a_missed_night_before_six_still_runs(conn, follow, started):
    follow("AAA")
    stock_jobs.nightly(conn, local(DAY, 5, 10))  # the VM came up at 05:10
    assert [d for _, d in started] == [DAY]
    assert conn.execute("SELECT COUNT(*) FROM stock_runs").fetchone()[0] == 0


def test_a_missed_night_after_six_is_recorded_as_skipped_and_alerted(conn, follow, started, sent):
    follow("AAA")
    stock_jobs.nightly(conn, local(DAY, 9, 15))
    assert started == []
    row = conn.execute("SELECT * FROM stock_runs").fetchone()
    assert (row["run_date"], row["status"]) == ("2026-09-30", "skipped")
    assert [s[1] for s in sent] == ["Aktsiate öö 30.09: jäi vahele"]
    assert sent[0][0]["topic"] == "stock-oracle"
    # The 07:00 digest names the skipped night once more but does not send the alert twice.
    alerts.send_digest(conn, DAY)
    assert [s[1] for s in sent] == ["Aktsiate öö 30.09: jäi vahele", "Aktsiad 30.09"]


def test_a_worker_started_after_midnight_records_yesterday(conn, follow, started, sent):
    follow("AAA")
    stock_jobs.nightly(conn, local(DAY, 0, 40))
    assert started == []
    assert conn.execute("SELECT run_date, status FROM stock_runs").fetchall()[0][:] == ("2026-09-29", "skipped")


def test_nothing_runs_without_stocks(conn, started, monkeypatch, sent):
    monkeypatch.setattr(market, "refresh_quotes", lambda *a: pytest.fail("quotes without stocks"))
    monkeypatch.setattr(sources, "poll_filings", lambda *a: pytest.fail("filings without stocks"))
    stock_jobs.nightly(conn, local(DAY, 1, 30))
    stock_jobs.nightly(conn, local(DAY, 9, 0))
    stock_jobs.digest(conn, local(DAY, 7, 0))
    stock_jobs.quotes(conn, local(DAY, 17, 0))
    stock_jobs.filings(conn, local(DAY, 12, 0))
    assert started == [] and sent == []
    assert conn.execute("SELECT COUNT(*) FROM stock_runs").fetchone()[0] == 0


def test_the_digest_job_waits_for_seven(conn, follow, monkeypatch):
    follow("AAA")
    days = []
    monkeypatch.setattr(alerts, "send_digest", lambda conn, day: days.append(day))
    stock_jobs.digest(conn, local(DAY, 0, 30))  # a worker back after midnight: not tomorrow's yet
    stock_jobs.digest(conn, local(DAY, 7, 0))
    stock_jobs.digest(conn, local(DAY, 11, 0))
    assert days == [DAY, DAY]


# --- The thread and the claim ---------------------------------------------------------------------

def _wait_for(thread_name, timeout=5):
    for t in threading.enumerate():
        if t.name == thread_name:
            t.join(timeout)
            assert not t.is_alive()


def test_start_claims_runs_on_a_thread_and_releases(conn, app, monkeypatch):
    gate, seen = threading.Event(), []

    def slow(c, run_date, **kw):
        seen.append((run_date, common.claimed(c, "night")))
        gate.wait(5)
        return 1
    monkeypatch.setattr(night, "run", slow)
    db = app.config["DATABASE_PATH"]
    assert night.start(db, DAY) is True
    assert night.start(db, DAY) is False  # one night at a time
    gate.set()
    _wait_for("stocks-night")
    assert seen == [(DAY, True)]
    assert not common.claimed(conn, "night")
    assert night.start(db, DAY) is True
    _wait_for("stocks-night")


def test_the_claim_is_released_when_the_night_raises(conn, app, monkeypatch):
    def broken(c, run_date, **kw):
        raise RuntimeError("katki")
    monkeypatch.setattr(night, "run", broken)
    assert night.start(app.config["DATABASE_PATH"], DAY) is True
    _wait_for("stocks-night")
    assert not common.claimed(conn, "night")


def test_the_heartbeat_keeps_the_claim_fresh(conn, app, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(night, "HEARTBEAT", 0.05)
    monkeypatch.setattr(night, "run", lambda c, d, **kw: gate.wait(5))
    conn.execute("INSERT INTO settings (key, value) VALUES ('stocks:running:night', '2026-01-01T00:00:00Z')")
    assert night.start(app.config["DATABASE_PATH"], DAY) is True  # a stale claim is taken over
    old = conn.execute("SELECT value FROM settings WHERE key = 'stocks:running:night'").fetchone()["value"]
    time.sleep(1.2)
    new = conn.execute("SELECT value FROM settings WHERE key = 'stocks:running:night'").fetchone()["value"]
    assert new > old
    gate.set()
    _wait_for("stocks-night")


def test_the_worker_keeps_running_other_jobs_while_a_night_runs(conn, app, follow, monkeypatch):
    """Finding 2 of the plan: run_due must come back at once while the night goes on its thread."""
    follow("AAA")
    gate, ran = threading.Event(), []
    monkeypatch.setattr(night, "run", lambda c, d, **kw: gate.wait(10))
    one_thirty = local(DAY, 1, 30)
    monkeypatch.setattr(stock_jobs, "now_utc", lambda: one_thirty)
    monkeypatch.setattr(registry, "REGISTRY", {
        "stocks-night": registry.REGISTRY["stocks-night"],
        "reminders": registry.Job("reminders", lambda c: ran.append("reminders"), 60, (), 0),
    })
    for name in ("stocks-night", "reminders"):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (f"job:{name}:next", "2026-09-29T22:29:00Z"))
    try:
        began = time.monotonic()
        worker.run_due(conn, one_thirty)
        assert time.monotonic() - began < 2
        assert ran == ["reminders"]
        assert common.claimed(conn, "night")
        # A minute later the night still runs and reminders go again.
        worker.run_due(conn, one_thirty + timedelta(minutes=2))
        assert ran == ["reminders", "reminders"]
        assert common.claimed(conn, "night")
    finally:
        gate.set()
        _wait_for("stocks-night")
    assert not common.claimed(conn, "night")


# --- Daytime jobs ---------------------------------------------------------------------------------

@pytest.fixture
def quotes_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(market, "refresh_quotes", lambda conn, now: calls.append(("quotes", now)) or {"quoted": [], "failed": {}})
    monkeypatch.setattr(alerts, "check", lambda conn, now: calls.append(("check", now)) or 0)
    return calls


@pytest.mark.parametrize(("utc", "open_"), [
    # 26.10.2026: Europe is back on winter time, New York not until 1.11. The market opens at
    # 09:30 New York = 13:30Z = 15:30 in Tallinn, an hour earlier on Tallinn's clock than usual.
    ("2026-10-26T13:25:00", False),
    ("2026-10-26T13:35:00", True),
    ("2026-10-26T19:55:00", True),   # 15:55 in New York
    ("2026-10-26T20:05:00", False),
    # 3.11.2026, both on winter time: 09:30 New York = 14:30Z.
    ("2026-11-03T13:35:00", False),
    ("2026-11-03T14:35:00", True),
    # 16.03.2026: New York on summer time since 8.3, Europe not until 29.3.
    ("2026-03-16T13:35:00", True),
    ("2026-03-16T13:25:00", False),
    # A Saturday.
    ("2026-10-31T15:00:00", False),
])
def test_quotes_only_in_us_market_hours(conn, follow, quotes_calls, utc, open_):
    follow("AAA")
    now = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc)
    stock_jobs.quotes(conn, now)
    assert [c[0] for c in quotes_calls] == (["quotes", "check"] if open_ else [])


def test_filings_poll_stays_out_of_the_night(conn, follow, monkeypatch):
    follow("AAA")
    polled = []
    monkeypatch.setattr(sources, "poll_filings", lambda conn, now: polled.append(now) or {})
    monkeypatch.setattr(alerts, "check", lambda conn, now: 0)
    for hh in (1, 3, 5):
        stock_jobs.filings(conn, local(DAY, hh, 45))
    assert polled == []
    stock_jobs.filings(conn, local(DAY, 6, 5))
    stock_jobs.filings(conn, local(DAY, 1, 20))
    assert polled == [local(DAY, 6, 5), local(DAY, 1, 20)]
    common.claim(conn, "night")  # a night started by hand at noon
    stock_jobs.filings(conn, local(DAY, 12, 0))
    assert len(polled) == 2
