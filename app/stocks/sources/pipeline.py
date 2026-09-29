"""The night's ingest step, the hourly filings poll, and the document counts behind the attention
alert. Rewritten on sqlite3 from stock-oracle's pipeline/ingest.py.

Every source gets a stock_source_runs row. A source continues from where its last successful run
ended, minus an overlap (the store's deduplication makes re-reading cheap). A failure for one
instrument is recorded and the source moves on; a used-up daily quota ends that source for the
run; one failing source never stops the others. A source without its credential is skipped and
gets no row."""

import json
import logging
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time

import httpx

from ... import ics
from ...db import tx
from ...util import TALLINN, iso, parse_iso
from .. import common, config
from . import alphavantage
from .alphavantage import AlphaVantageSource
from .attention import zscore
from .base import BudgetExhausted, DailyBudget, RateLimitedError, RateLimiter
from .edgar import DEFAULT_FORMS, POLL_FORMS, EdgarSource
from .finnhub import FinnhubSource
from .models import InstrumentRef
from .rss import google_news_rss, ir_rss, yahoo_rss
from .store import SettingsBudget, has_body, store_documents, store_earnings

log = logging.getLogger(__name__)

# In the order they run: the first source to store a story keeps it.
SOURCES = ("edgar", "finnhub", "yahoo_rss", "google_news", "ir_rss", "alphavantage")
EARNINGS = "earnings"

FIRST_RUN_LOOKBACK = timedelta(days=7)
OVERLAP = timedelta(hours=6)
EARNINGS_BACK = timedelta(days=30)
EARNINGS_AHEAD = timedelta(days=120)
ATTENTION_WINDOW = 30
# count_docs recounts the baseline too, so documents that arrive late (or come from the import)
# are counted on their own day.
COUNT_DAYS = ATTENTION_WINDOW + 3
ERROR_CHARS = 4000


def ingest(conn, now, *, run_id=None, only=None, client=None, sleep=time.sleep, clock=time.monotonic,
           download=ics.download):
    """Every configured source for every followed instrument, then Finnhub's earnings calendar.
    -> {source: {status, fetched, new, error}}. `only` limits the sources; `client`, `sleep`,
    `clock` and `download` are for tests."""
    own_client = client is None
    client = client or _client()
    try:
        built, missing = build_sources(conn, client, now, sleep=sleep, clock=clock, download=download)
        rows = common.followed(conn)
        report = {}
        for name in SOURCES:
            if only is not None and name not in only:
                continue
            if name not in built:
                report[name] = {"status": "skipped", "fetched": 0, "new": 0, "error": missing[name]}
                continue
            report[name] = _run_source(conn, name, built[name], rows, _since(conn, name, now), now, run_id)
        if only is None or EARNINGS in only:
            finnhub = built.get("finnhub")
            report[EARNINGS] = (_run_earnings(conn, finnhub, rows, now, run_id) if finnhub else
                                {"status": "skipped", "fetched": 0, "new": 0, "error": missing["finnhub"]})
        return report
    finally:
        if own_client:
            client.close()


def poll_filings(conn, now, *, client=None, sleep=time.sleep, clock=time.monotonic):
    """The hourly poll between nights: EDGAR 8-K and Form 4 only, no model. It reads from the
    night's cursor and does not move it (the night still fetches the 10-Q/10-K between), and
    skips filings already stored with their text. -> {"edgar": {status, fetched, new, error}}"""
    own_client = client is None
    client = client or _client()
    try:
        built, missing = build_sources(conn, client, now, sleep=sleep, clock=clock, forms=POLL_FORMS, only=("edgar",))
        if "edgar" not in built:
            return {"edgar": {"status": "skipped", "fetched": 0, "new": 0, "error": missing["edgar"]}}
        since = max(_since(conn, "edgar", now), now - FIRST_RUN_LOOKBACK)
        return {"edgar": _run_source(conn, "edgar", built["edgar"], common.followed(conn), since, now, None, cursor=False)}
    finally:
        if own_client:
            client.close()


def _client():
    return httpx.Client(timeout=30, follow_redirects=True)


def build_sources(conn, client, now, *, sleep=time.sleep, clock=time.monotonic, download=ics.download,
                  forms=DEFAULT_FORMS, only=SOURCES):
    """({name: source} for the configured ones, {name: why not} for the rest)."""
    def limiter(interval):
        return RateLimiter(interval, clock=clock, sleep=sleep)

    built, missing = {}, {}
    ua = config.secret(conn, "sec_user_agent")
    if "edgar" in only:
        if ua and "@" in ua:
            # SEC fair access: at most 10 requests a second.
            built["edgar"] = EdgarSource(client, user_agent=ua, limiter=limiter(0.1), sleep=sleep, forms=forms)
        else:
            missing["edgar"] = "no SEC User-Agent"
    if "finnhub" in only:
        if key := config.secret(conn, "finnhub_key"):
            built["finnhub"] = FinnhubSource(client, api_key=key, limiter=limiter(1.0), sleep=sleep)
        else:
            missing["finnhub"] = "no Finnhub key"
    if "yahoo_rss" in only:
        built["yahoo_rss"] = yahoo_rss(client, limiter=limiter(1.0), sleep=sleep)
    if "google_news" in only:
        built["google_news"] = google_news_rss(client, limiter=limiter(1.0), sleep=sleep)
    if "ir_rss" in only:
        if feeds := config.settings(conn)["ir_feeds"]:
            built["ir_rss"] = ir_rss(client, feeds, download=download, limiter=limiter(1.0), sleep=sleep)
        else:
            missing["ir_rss"] = "no IR feeds"
    if "alphavantage" in only:
        if key := config.secret(conn, "alphavantage_key"):
            day = now.astimezone(UTC).date()
            budget = DailyBudget.stored(alphavantage.FREE_DAILY_REQUESTS, source="alphavantage",
                                        store=SettingsBudget(conn), today=lambda: day)
            built["alphavantage"] = AlphaVantageSource(client, api_key=key, budget=budget, limiter=limiter(1.5), sleep=sleep)
        else:
            missing["alphavantage"] = "no Alpha Vantage key"
    return built, missing


def _ref(row):
    symbols = json.loads(row["provider_symbols_json"] or "{}")
    return InstrumentRef(ticker=row["symbol"], name=row["name"], cik=symbols.get("sec_cik") or None,
                         exchange=row["exchange"], currency=row["currency"], symbols=symbols)


def _with_cik(conn, source, row, ref):
    """The ref with its SEC CIK, or None when EDGAR does not cover it. A lookup is remembered in
    provider_symbols_json["sec_cik"] (null = not in SEC's ticker map), the one key this package
    may set there."""
    if "sec_cik" in ref.symbols:
        return ref if ref.cik else None
    cik = source.cik_for(ref.ticker)
    with tx(conn):
        current = conn.execute("SELECT provider_symbols_json FROM instruments WHERE id = ?", (row["id"],)).fetchone()
        symbols = json.loads(current["provider_symbols_json"] or "{}")
        symbols["sec_cik"] = cik
        conn.execute("UPDATE instruments SET provider_symbols_json = ? WHERE id = ?", (json.dumps(symbols), row["id"]))
    return replace(ref, cik=cik) if cik else None


def _since(conn, name, now):
    """Where this source continues: the end of its last run that got anything, less OVERLAP."""
    row = conn.execute(
        "SELECT cursor_json FROM stock_source_runs WHERE source = ? AND status IN ('succeeded', 'partial') "
        "AND cursor_json IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT 1", (name,)).fetchone()
    until = json.loads(row["cursor_json"]).get("until") if row else None
    return min(parse_iso(until) - OVERLAP, now) if until else now - FIRST_RUN_LOOKBACK


class _Run:
    """One stock_source_runs row, written as it goes so the Allikad tab sees progress."""

    def __init__(self, conn, source, run_id, cursor):
        self.conn = conn
        self.id = conn.execute(
            "INSERT INTO stock_source_runs (run_id, source, started_at, status, cursor_json) VALUES (?, ?, ?, 'running', ?)",
            (run_id, source, iso(datetime.now(UTC)), json.dumps(cursor) if cursor else None)).lastrowid
        self.ok = 0
        self.errors = {}
        self.not_covered = []
        self.skipped = []
        self.secrets = [s for s in (config.secret(conn, n) for n in config.SECRETS) if s and len(s) >= 6]

    def fail(self, symbol, exc):
        self.errors[symbol] = self.scrub(f"{type(exc).__name__}: {exc}")[:500]

    def scrub(self, text):
        # Keys never reach an error text on purpose (headers; redacted query strings); this makes
        # sure an exception that quotes a request cannot store one either.
        for secret in self.secrets:
            text = text.replace(secret, "•••")
        return text

    def finish(self, fetched, new):
        error = None
        if not self.errors:
            status = "succeeded"
        else:
            status = "partial" if self.ok else "failed"
            error = "; ".join(f"{k}: {v}" for k, v in self.errors.items())
            if self.skipped:
                error += f"; skipped {len(self.skipped)} after rate limit"
            error = error[:ERROR_CHARS]
        self.conn.execute(
            "UPDATE stock_source_runs SET status = ?, items_fetched = ?, items_new = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, fetched, new, error, iso(datetime.now(UTC)), self.id))
        out = {"status": status, "fetched": fetched, "new": new, "error": error}
        if self.not_covered:
            out["not_covered"] = self.not_covered
        return out


def _run_source(conn, name, source, rows, since, now, run_id, *, cursor=True):
    run = _Run(conn, name, run_id, {"since": iso(since), "until": iso(now)} if cursor else None)
    ids = {r["symbol"]: r["id"] for r in rows}
    fetched = new = 0
    limited = False
    for row in rows:
        if limited:
            run.skipped.append(row["symbol"])
            continue
        try:
            ref = _ref(row)
            if isinstance(source, EdgarSource):
                ref = _with_cik(conn, source, row, ref)
            if ref is None or not getattr(source, "covers", lambda r: True)(ref):
                run.not_covered.append(row["symbol"])
                continue
            if isinstance(source, EdgarSource):
                docs = source.fetch(ref, since, now, known=lambda accession: has_body(conn, "edgar", accession))
            else:
                docs = source.fetch(ref, since, now)
            stored = store_documents(conn, docs, instrument_ids=ids, fetched_at=now)
        except BudgetExhausted as exc:
            run.fail(row["symbol"], exc)
            break
        except RateLimitedError as exc:
            run.fail(row["symbol"], exc)
            limited = True
        except Exception as exc:
            # Adapter bugs included: one source must never end the night.
            run.fail(row["symbol"], exc)
            log.warning("ingest %s %s: %s", name, row["symbol"], run.errors[row["symbol"]])
        else:
            run.ok += 1
            fetched += len(docs)
            new += len(stored.inserted)
    return run.finish(fetched, new)


def _run_earnings(conn, finnhub, rows, now, run_id):
    run = _Run(conn, EARNINGS, run_id, None)
    ids = {r["symbol"]: r["id"] for r in rows}
    today = now.astimezone(TALLINN).date()
    fetched = new = 0
    for row in rows:
        if row["kind"] != "stock":
            continue
        ref = _ref(row)
        if not finnhub.covers(ref):
            run.not_covered.append(row["symbol"])
            continue
        try:
            items = finnhub.earnings_calendar(ref, today - EARNINGS_BACK, today + EARNINGS_AHEAD)
            count, added = store_earnings(conn, items, instrument_ids=ids, now=now)
        except Exception as exc:
            run.fail(row["symbol"], exc)
            continue
        run.ok += 1
        fetched += count
        new += added
    return run.finish(fetched, new)


def count_docs(conn, today):
    """doc_counts (documents per instrument, Tallinn day and source) for the last COUNT_DAYS days
    up to `today`, recounted from market_docs. -> {"rows", "from", "to"}"""
    start = today - timedelta(days=COUNT_DAYS)
    start_utc = datetime.combine(start, clock_time(), TALLINN).astimezone(UTC)
    counts = Counter()
    for r in conn.execute(
            "SELECT d.published_at, d.source, l.instrument_id FROM market_docs d "
            "JOIN market_doc_instruments l ON l.document_id = d.id WHERE d.published_at >= ?", (iso(start_utc),)):
        day = parse_iso(r["published_at"]).astimezone(TALLINN).date()
        if day <= today:
            counts[(r["instrument_id"], day.isoformat(), r["source"])] += 1
    with tx(conn):
        conn.execute("DELETE FROM doc_counts WHERE date >= ? AND date <= ?", (start.isoformat(), today.isoformat()))
        conn.executemany("INSERT INTO doc_counts (instrument_id, date, source, count) VALUES (?, ?, ?, ?)",
                         [(*key, n) for key, n in counts.items()])
    return {"rows": len(counts), "from": start.isoformat(), "to": today.isoformat()}


def attention_z(conn, instrument_id, day):
    """The day's documents (all sources together) against the 30 days before it, as a z-score;
    None without 7 days of history or when the history is flat. The sources are summed because
    the store keeps each story once, under the source that brought it first."""
    first = conn.execute("SELECT MIN(date) AS d FROM doc_counts WHERE instrument_id = ?", (instrument_id,)).fetchone()["d"]
    if first is None:
        return None
    rows = conn.execute(
        "SELECT date, SUM(count) AS n FROM doc_counts WHERE instrument_id = ? AND date >= ? AND date <= ? GROUP BY date",
        (instrument_id, (day - timedelta(days=ATTENTION_WINDOW)).isoformat(), day.isoformat())).fetchall()
    by_day = {date.fromisoformat(r["date"]): float(r["n"]) for r in rows}
    return zscore(by_day, day, window=ATTENTION_WINDOW, first=date.fromisoformat(first))
