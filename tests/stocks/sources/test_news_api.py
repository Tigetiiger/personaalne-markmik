"""/api/stocks/sources and /api/stocks/docs: the Allikad tab and a stock's Uudised section."""

import json
from datetime import timedelta

import pytest

from app import vault
from app.stocks import common
from app.util import iso, now_utc
from app.views import stocks_sources


def add_doc(conn, iid, title, published_at, *, source="finnhub", url=None, signals=()):
    doc_id = conn.execute(
        "INSERT INTO market_docs (source, external_id, url, content_hash, title, published_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, title, url, title, title, published_at, json.dumps({"publisher": "Reuters"}))).lastrowid
    conn.execute("INSERT INTO market_doc_instruments VALUES (?, ?)", (doc_id, iid))
    for version, summary, sentiment, created in signals:
        conn.execute("INSERT INTO doc_signals (document_id, instrument_id, sentiment, event_type, novelty, relevance, summary, "
                     "prompt_version, created_at) VALUES (?, ?, ?, 'earnings', '0.5', '0.9', ?, ?, ?)",
                     (doc_id, iid, sentiment, summary, version, created))
    return doc_id


def add_run(conn, source, started_at, status, error=None):
    conn.execute("INSERT INTO stock_source_runs (source, started_at, finished_at, status, items_fetched, items_new, error) "
                 "VALUES (?, ?, ?, ?, 12, 3, ?)", (source, started_at, started_at, status, error))


def test_owner_only(client):
    assert client.get("/api/stocks/sources").status_code == 401
    assert client.get("/api/stocks/docs/NVDA").status_code == 401
    assert client.post("/api/stocks/sources/run").status_code in (401, 403)


def test_source_health_nights_and_ai_runs(owner, conn, follow):
    nvda = follow("NVDA", "NVIDIA")
    vault.set(conn, "finnhub_key", "finnhub-secret-0001")
    now = now_utc()
    for days, status in ((1, "failed"), (2, "failed"), (3, "succeeded")):
        add_run(conn, "finnhub", iso(now - timedelta(days=days)), status, "NVDA: SourceError: HTTP 401" if status == "failed" else None)
    add_run(conn, "gdelt", iso(now - timedelta(days=40)), "succeeded")
    add_doc(conn, nvda, "old", iso(now - timedelta(days=20)))
    conn.execute("UPDATE market_docs SET fetched_at = ?", (iso(now - timedelta(days=20)),))
    add_doc(conn, nvda, "new", iso(now - timedelta(days=1)))
    conn.execute("INSERT INTO stock_runs (run_date, started_at, finished_at, status, steps_json) VALUES "
                 "('2026-09-21', '2026-09-20T22:30:00Z', '2026-09-20T23:05:00Z', 'partial', ?)",
                 (json.dumps({"prices": {"status": "succeeded"}, "ingest": {"status": "failed", "detail": "boom"}}),))
    conn.execute("INSERT INTO stock_ai_runs (purpose, instrument_id, model, prompt_version, started_at, duration_ms, status) "
                 "VALUES ('research', ?, 'sonnet', 'research@1', ?, 95000, 'success')", (nvda, iso(now - timedelta(hours=2))))
    conn.execute("INSERT INTO stock_ai_runs (purpose, model, prompt_version, started_at, status, error) "
                 "VALUES ('extract', 'haiku', 'extract@1', ?, 'unavailable', 'You''ve hit your limit')", (iso(now - timedelta(hours=1)),))
    conn.execute("INSERT INTO stock_ai_runs (purpose, model, prompt_version, started_at, status) "
                 "VALUES ('verdict', 'sonnet', 'verdict@1', ?, 'success')", (iso(now - timedelta(days=40)),))

    data = owner.get("/api/stocks/sources").get_json()
    assert data["running"] is False
    names = [s["name"] for s in data["sources"]]
    assert names == ["edgar", "finnhub", "yahoo_rss", "google_news", "ir_rss", "alphavantage", "earnings", "gdelt"]
    finnhub = data["sources"][1]
    assert finnhub["configured"] and finnhub["failing_streak"] == 2
    assert finnhub["last"]["status"] == "failed" and finnhub["last"]["error"] == "NVDA: SourceError: HTTP 401"
    assert finnhub["documents"] == 2 and finnhub["documents_7d"] == 1
    edgar = data["sources"][0]
    assert edgar == {"name": "edgar", "configured": False, "failing_streak": 0, "documents": 0, "documents_7d": 0, "last": None}
    assert data["sources"][5]["budget"] == {"used": 0, "limit": 25}

    (night,) = data["nights"]
    assert night["status"] == "partial"
    assert night["steps"] == [{"name": "prices", "status": "succeeded"}, {"name": "ingest", "status": "failed", "detail": "boom"}]
    ai = data["ai"]
    assert [r["purpose"] for r in ai["runs"]] == ["extract", "research", "verdict"]
    assert ai["runs"][1]["symbol"] == "NVDA" and ai["runs"][1]["duration_ms"] == 95000
    assert ai["totals"] == {"runs": 2, "duration_ms": 95000, "success": 1, "failed": 0, "unavailable": 1}


def test_documents_of_one_symbol_with_the_latest_signal(owner, conn, follow):
    nvda = follow("NVDA", "NVIDIA")
    amd = follow("AMD", "AMD")
    add_doc(conn, nvda, "Older", "2026-09-19T08:00:00Z", url="https://example.com/a",
            signals=[("extract@1", "First reading", "0.1", "2026-09-19T09:00:00Z"),
                     ("extract@2", "Second reading", "0.6", "2026-09-20T09:00:00Z")])
    add_doc(conn, nvda, "Newer", "2026-09-20T08:00:00Z", url="javascript:alert(1)")
    add_doc(conn, amd, "Not this one", "2026-09-21T08:00:00Z")

    docs = owner.get("/api/stocks/docs/nvda").get_json()
    assert [d["title"] for d in docs] == ["Newer", "Older"]
    newer, older = docs
    assert newer["url"] is None and newer["summary"] is None and newer["sentiment"] is None
    assert older == {"id": older["id"], "title": "Older", "source": "finnhub", "published_at": "2026-09-19T08:00:00Z",
                     "url": "https://example.com/a", "publisher": "Reuters", "summary": "Second reading", "sentiment": "0.6",
                     "event_type": "earnings"}
    assert len(owner.get("/api/stocks/docs/NVDA?limit=1").get_json()) == 1
    r = owner.get("/api/stocks/docs/ZZZZ")
    assert r.status_code == 404 and r.get_json()["error"]


def test_run_now_claims_runs_on_a_thread_and_is_rate_limited(owner, conn, monkeypatch):
    calls = []
    started = []

    class NoThread:
        def __init__(self, target, args, **kw):
            self.target, self.args = target, args

        def start(self):
            started.append(self)

    monkeypatch.setattr(stocks_sources.threading, "Thread", NoThread)
    monkeypatch.setattr(stocks_sources, "INGEST", lambda conn, now: calls.append(now) or {})
    r = owner.post("/api/stocks/sources/run")
    assert r.status_code == 202 and r.get_json() == {"running": True}
    assert common.claimed(conn, "ingest")
    assert owner.get("/api/stocks/sources").get_json()["running"] is True
    # A second one waits for the first.
    assert owner.post("/api/stocks/sources/run").status_code == 409
    started[0].target(*started[0].args)
    assert len(calls) == 1 and not common.claimed(conn, "ingest")
    assert owner.post("/api/stocks/sources/run").status_code == 202
    assert owner.post("/api/stocks/sources/run").status_code == 429


def test_run_now_waits_for_the_night(owner, conn):
    assert common.claim(conn, "night")
    r = owner.post("/api/stocks/sources/run")
    assert r.status_code == 409 and r.get_json()["error"] == "Allikate lugemine juba käib."


@pytest.mark.parametrize("path", ["/_test/stocks-sources/seed"])
def test_test_pages_are_off_without_the_flag(owner, path):
    assert owner.post(path, json={}).status_code == 404
