"""The night package's endpoints (app/views/stocks_night.py): the runs, starting a night now, and
Täna's "Turg" card."""

import json
import threading
from datetime import timedelta


from app.stocks import common, night
from app.util import local_today


def test_owner_only(client):
    for path in ("/api/stocks/night", "/api/stocks/today"):
        assert client.get(path).status_code in (302, 401)
    assert client.post("/api/stocks/night/run").status_code in (302, 401, 403)


def test_today_is_empty_when_nothing_is_followed(owner):
    assert owner.get("/api/stocks/today").get_json() == {"followed": False, "holdings": [], "earnings": [], "verdicts": []}


def test_today(owner, conn, follow, hold, bars):
    today = local_today()
    up, down, flat = follow("UPP", "Üles AS"), follow("DWN", "Alla Corp"), follow("FLT")
    watched, gone = follow("WAT", "Jälgin"), follow("OLD", watch=False)
    for iid in (up, down, flat):
        hold(iid)
    bars(up, [("2026-09-28", "100"), ("2026-09-29", "101.5")])
    bars(down, [("2026-09-28", "100"), ("2026-09-29", "96")])
    conn.execute("INSERT INTO earnings (instrument_id, date, time_of_day, source) VALUES (?, ?, 'amc', 'finnhub')",
                 (watched, today.isoformat()))
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, ?, 'finnhub')", (up, (today + timedelta(days=1)).isoformat()))
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, ?, 'finnhub')", (up, (today + timedelta(days=2)).isoformat()))
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, ?, 'finnhub')", (gone, today.isoformat()))
    for iid, as_of, v in ((watched, today - timedelta(days=1), "hold"), (watched, today, "sell"),
                          (up, today - timedelta(days=1), "buy"), (up, today, "buy"), (down, today, "hold")):
        conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                     "VALUES (?, ?, ?, 'low', 'x', 'v@1', 'sonnet')", (iid, as_of.isoformat(), v))
    data = owner.get("/api/stocks/today").get_json()
    assert data["followed"] is True
    assert [(h["symbol"], h["change"]) for h in data["holdings"]] == [("DWN", "-0.040000"), ("UPP", "0.015000"), ("FLT", None)]
    assert data["holdings"][1] == {"symbol": "UPP", "name": "Üles AS", "currency": "USD", "price": "101.5",
                                   "previous_close": "100", "change": "0.015000", "day": "2026-09-29"}
    assert data["earnings"] == [
        {"symbol": "WAT", "name": "Jälgin", "date": today.isoformat(), "time_of_day": "amc", "held": False},
        {"symbol": "UPP", "name": "Üles AS", "date": (today + timedelta(days=1)).isoformat(), "time_of_day": None, "held": True}]
    assert data["verdicts"] == [{"symbol": "WAT", "name": "Jälgin", "verdict": "sell", "conviction": "low", "previous": "hold"}]


def test_night_status_lists_the_runs(owner, conn):
    steps = {"prices": {"status": "succeeded", "started_at": "a", "finished_at": "b", "detail": {}},
             "ingest": {"status": "partial", "started_at": "b", "finished_at": "c", "detail": {"finnhub": {"status": "failed"}}}}
    conn.execute("INSERT INTO stock_runs (run_date, started_at, finished_at, status, steps_json) VALUES "
                 "('2026-09-30', '2026-09-29T22:30:00Z', '2026-09-30T03:00:00Z', 'partial', ?)", (json.dumps(steps),))
    data = owner.get("/api/stocks/night").get_json()
    assert data["running"] is False
    assert data["runs"][0]["run_date"] == "2026-09-30" and data["runs"][0]["status"] == "partial"
    assert [s["name"] for s in data["runs"][0]["steps"]] == ["prices", "ingest"]
    common.claim(conn, "night")
    assert owner.get("/api/stocks/night").get_json()["running"] is True


def test_run_now_starts_todays_night_on_a_thread(owner, conn, monkeypatch):
    gate, seen = threading.Event(), []

    def slow(c, run_date, **kw):
        seen.append(run_date)
        gate.wait(5)
        return 1
    monkeypatch.setattr(night, "run", slow)
    r = owner.post("/api/stocks/night/run")
    assert r.status_code == 202 and r.get_json() == {"running": True}
    r = owner.post("/api/stocks/night/run")
    assert r.status_code == 409 and r.get_json() == {"error": "Öö juba käib."}
    gate.set()
    for t in threading.enumerate():
        if t.name == "stocks-night":
            t.join(5)
    assert seen == [local_today()]
    assert not common.claimed(conn, "night")


def test_run_now_is_rate_limited(owner, monkeypatch):
    monkeypatch.setattr(night, "start", lambda db_path, run_date: True)
    codes = [owner.post("/api/stocks/night/run").status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_the_seed_route_exists_only_for_browser_tests(owner):
    assert owner.post("/_test/stocks-night/seed", json={}).status_code == 404
