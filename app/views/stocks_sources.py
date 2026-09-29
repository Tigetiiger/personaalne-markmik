"""Aktsiad: the sources package's API (docs/CONTRACTS.md, "stocks-sources"). The Allikad tab
(source health, the nightly runs, the model runs), a stock's recent documents for its "Uudised"
section, and reading the sources now. Owner only (default deny)."""

import json
import logging
import os
import threading
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request

from ..db import connect, get_db, tx
from ..security import rate_limit
from ..stocks import common, config, sources
from ..stocks.sources import alphavantage, pipeline
from ..stocks.sources.store import SettingsBudget
from ..util import iso, local_today, now_utc

log = logging.getLogger(__name__)
bp = Blueprint("stocks_sources", __name__)

NIGHTS_SHOWN = 30
AI_RUNS_SHOWN = 50
RUNS_PER_SOURCE = 10
DOCS_SHOWN = 25
# The Allikad tab's sources, in the order they run; other names (stock-oracle's GDELT, Marketaux,
# Bluesky, from the import) are listed after them when they have documents or runs.
NAMES = (*pipeline.SOURCES, pipeline.EARNINGS)
# Replaced by the browser tests' fake (MARKMIK_TEST_PAGES): the real one reads the internet.
INGEST = sources.ingest


def _configured(conn):
    ua = config.secret(conn, "sec_user_agent") or ""
    finnhub = bool(config.secret(conn, "finnhub_key"))
    return {"edgar": "@" in ua, "finnhub": finnhub, "earnings": finnhub, "yahoo_rss": True, "google_news": True,
            "ir_rss": bool(config.settings(conn)["ir_feeds"]), "alphavantage": bool(config.secret(conn, "alphavantage_key"))}


def source_health(conn, now):
    week_ago = iso(now - timedelta(days=7))
    counts = {r["source"]: (r["total"], r["recent"]) for r in conn.execute(
        "SELECT source, COUNT(*) AS total, SUM(fetched_at >= ?) AS recent FROM market_docs GROUP BY source", (week_ago,))}
    runs = {}
    for r in conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC, id DESC) AS n "
            "FROM stock_source_runs) WHERE n <= ? ORDER BY source, n", (RUNS_PER_SOURCE,)):
        runs.setdefault(r["source"], []).append(r)
    configured = _configured(conn)
    names = [*NAMES, *sorted((runs.keys() | counts.keys()) - set(NAMES))]
    out = []
    for name in names:
        history = runs.get(name, [])
        streak = 0
        for run in history:
            if run["status"] != "failed":
                break
            streak += 1
        total, recent = counts.get(name, (0, 0))
        last = history[0] if history else None
        out.append({
            "name": name, "configured": configured.get(name, False), "failing_streak": streak,
            "documents": total, "documents_7d": recent or 0,
            "last": last and {"started_at": last["started_at"], "finished_at": last["finished_at"], "status": last["status"],
                              "fetched": last["items_fetched"], "new": last["items_new"], "error": last["error"]},
        })
        if name == "alphavantage":
            out[-1]["budget"] = {"used": SettingsBudget(conn).load(name, now.date()), "limit": alphavantage.FREE_DAILY_REQUESTS}
    return out


def nights(conn):
    return [{"id": r["id"], "run_date": r["run_date"], "started_at": r["started_at"], "finished_at": r["finished_at"],
             "status": r["status"], "steps": _steps(r["steps_json"])}
            for r in conn.execute("SELECT * FROM stock_runs ORDER BY started_at DESC, id DESC LIMIT ?", (NIGHTS_SHOWN,))]


def _steps(steps_json):
    # A list: the JSON response sorts object keys, and the steps' order is the night's.
    steps = json.loads(steps_json or "{}")
    return [{"name": name, **(step if isinstance(step, dict) else {"status": str(step)})} for name, step in steps.items()]


def ai_runs(conn, now):
    runs = [{"id": r["id"], "started_at": r["started_at"], "purpose": r["purpose"], "symbol": r["symbol"], "model": r["model"],
             "status": r["status"], "duration_ms": r["duration_ms"], "error": r["error"]}
            for r in conn.execute("SELECT a.*, i.symbol FROM stock_ai_runs a LEFT JOIN instruments i ON i.id = a.instrument_id "
                                  "ORDER BY a.started_at DESC, a.id DESC LIMIT ?", (AI_RUNS_SHOWN,))]
    by_status = {r["status"]: r for r in conn.execute(
        "SELECT status, COUNT(*) AS n, SUM(duration_ms) AS ms FROM stock_ai_runs WHERE started_at >= ? GROUP BY status",
        (iso(now - timedelta(days=30)),))}
    totals = {"runs": sum(r["n"] for r in by_status.values()), "duration_ms": sum(r["ms"] or 0 for r in by_status.values()),
              **{s: by_status[s]["n"] if s in by_status else 0 for s in ("success", "failed", "unavailable")}}
    return {"runs": runs, "totals": totals}


@bp.get("/api/stocks/sources")
def overview():
    conn = get_db()
    now = now_utc()
    return jsonify(running=common.claimed(conn, "ingest"), sources=source_health(conn, now), nights=nights(conn),
                   ai=ai_runs(conn, now))


@bp.post("/api/stocks/sources/run")
@rate_limit("stocks-ingest", 3, 3600)
def run_now():
    conn = get_db()
    if common.claimed(conn, "night") or not common.claim(conn, "ingest"):
        return jsonify(error="Allikate lugemine juba käib."), 409
    threading.Thread(target=_background, args=(current_app.config["DATABASE_PATH"],), name="stocks-ingest", daemon=True).start()
    return jsonify(running=True), 202


def _background(db_path):
    conn = connect(db_path)
    try:
        INGEST(conn, now_utc())
        sources.count_docs(conn, local_today())
    except Exception:
        log.exception("stocks ingest on demand failed")
    finally:
        common.release(conn, "ingest")
        conn.close()


def _safe_url(url):
    # A feed's link becomes an href: only web addresses (never javascript:).
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None


@bp.get("/api/stocks/docs/<symbol>")
def documents(symbol):
    conn = get_db()
    instrument = common.by_symbol(conn, symbol)
    if instrument is None:
        return jsonify(error="Sellist aktsiat ei ole."), 404
    limit = min(max(request.args.get("limit", DOCS_SHOWN, type=int), 1), 100)
    rows = conn.execute(
        "SELECT d.id, d.title, d.source, d.published_at, d.url, d.meta_json, s.summary, s.sentiment, s.event_type "
        "FROM market_docs d JOIN market_doc_instruments l ON l.document_id = d.id AND l.instrument_id = ? "
        "LEFT JOIN (SELECT document_id, summary, sentiment, event_type, ROW_NUMBER() OVER "
        "  (PARTITION BY document_id ORDER BY created_at DESC, id DESC) AS n FROM doc_signals WHERE instrument_id = ?) s "
        "  ON s.document_id = d.id AND s.n = 1 "
        "ORDER BY d.published_at DESC, d.id DESC LIMIT ?", (instrument["id"], instrument["id"], limit)).fetchall()
    return jsonify([{"id": r["id"], "title": r["title"], "source": r["source"], "published_at": r["published_at"],
                     "url": _safe_url(r["url"]), "publisher": json.loads(r["meta_json"] or "{}").get("publisher"),
                     "summary": r["summary"], "sentiment": r["sentiment"], "event_type": r["event_type"]} for r in rows])


def _fake_ingest(conn, now, **kw):
    """The browser tests' ingest: one successful run of each keyless source, no network."""
    for name in ("yahoo_rss", "google_news"):
        conn.execute("INSERT INTO stock_source_runs (source, started_at, finished_at, status, items_fetched, items_new) "
                     "VALUES (?, ?, ?, 'succeeded', 3, 1)", (name, iso(now), iso(now)))
    return {}


def _seed():
    """Browser tests: replace the stocks-sources data with {"instruments": [{symbol, name}], "docs": [{symbol,
    title, source, published_at, url?, publisher?, signal?: {summary, sentiment, event_type}}], "runs": [{source,
    started_at, status, fetched?, new?, error?}], "nights": [{run_date, started_at, finished_at?, status, steps}],
    "ai": [{purpose, symbol?, model, started_at, status, duration_ms?, error?}]}. Also swaps in the fake ingest."""
    global INGEST
    INGEST = _fake_ingest
    data = request.get_json()
    conn = get_db()
    with tx(conn):
        for table in ("doc_signals", "market_doc_instruments", "market_docs", "stock_source_runs", "stock_ai_runs", "stock_runs",
                      "watchlist", "instruments"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM settings WHERE key LIKE 'stocks:running:%'")
        ids = {}
        for i in data.get("instruments", []):
            ids[i["symbol"]] = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, 'NMS', 'USD', ?, 'stock')",
                                            (i["symbol"], i["name"])).lastrowid
            conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (ids[i["symbol"]],))
        for n, d in enumerate(data.get("docs", [])):
            doc_id = conn.execute(
                "INSERT INTO market_docs (source, external_id, url, content_hash, title, published_at, fetched_at, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (d["source"], f"seed-{n}", d.get("url"), f"seed-{n}", d["title"], d["published_at"],
                                                    iso(now_utc()), json.dumps({"publisher": d.get("publisher")}))).lastrowid
            conn.execute("INSERT INTO market_doc_instruments VALUES (?, ?)", (doc_id, ids[d["symbol"]]))
            if s := d.get("signal"):
                conn.execute("INSERT INTO doc_signals (document_id, instrument_id, sentiment, event_type, novelty, relevance, summary, "
                             "prompt_version) VALUES (?, ?, ?, ?, '0.5', '0.8', ?, 'test@1')",
                             (doc_id, ids[d["symbol"]], s["sentiment"], s["event_type"], s["summary"]))
        for r in data.get("runs", []):
            conn.execute("INSERT INTO stock_source_runs (source, started_at, finished_at, status, items_fetched, items_new, error) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (r["source"], r["started_at"], r["started_at"], r["status"],
                                                          r.get("fetched", 0), r.get("new", 0), r.get("error")))
        for r in data.get("nights", []):
            conn.execute("INSERT INTO stock_runs (run_date, started_at, finished_at, status, steps_json) VALUES (?, ?, ?, ?, ?)",
                         (r["run_date"], r["started_at"], r.get("finished_at"), r["status"], json.dumps(r["steps"])))
        for r in data.get("ai", []):
            conn.execute("INSERT INTO stock_ai_runs (purpose, instrument_id, model, prompt_version, started_at, duration_ms, status, error) "
                         "VALUES (?, ?, ?, 'test@1', ?, ?, ?, ?)", (r["purpose"], ids.get(r.get("symbol")), r["model"], r["started_at"],
                                                                   r.get("duration_ms"), r["status"], r.get("error")))
    return "", 204


@bp.record
def _test_pages(state):
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/stocks-sources/seed", "stocks_sources.test_seed", _seed, methods=["POST"])
