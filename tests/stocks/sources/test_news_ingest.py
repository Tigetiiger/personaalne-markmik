"""The ingest step, the filings poll, the document counts and the attention z-score, on SQLite
with every source behind httpx.MockTransport."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from news_helpers import fixture_bytes

from app import vault
from app.stocks import config, sources
from app.stocks.sources import pipeline
from app.stocks.sources.alphavantage import QUERY_URL
from app.stocks.sources.finnhub import BASE_URL
from app.stocks.sources.store import SettingsBudget

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
AV_KEY = "av-secret-key-0042"
FINNHUB_KEY = "finnhub-secret-0001"
UA = "Markmik test@example.com"
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline"
GOOGLE = "https://news.google.com/rss/search"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0001045810.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1045810"
EMPTY_FEED = httpx.Response(200, content=b'<rss version="2.0"><channel><title>q</title></channel></rss>')


@pytest.fixture
def keys(conn):
    vault.set(conn, "sec_user_agent", UA)
    vault.set(conn, "finnhub_key", FINNHUB_KEY)
    vault.set(conn, "alphavantage_key", AV_KEY)


@pytest.fixture
def nvda(follow):
    return follow("NVDA", "NVIDIA", symbols={"yahoo": "NVDA"})


def routes(http):
    http.bytes(TICKERS, "edgar/company_tickers.json")
    http.bytes(SUBMISSIONS, "edgar/submissions_nvda.json")
    http.bytes(f"{ARCHIVE}/000104581026000078/nvda-20260903.htm", "edgar/8k.htm")
    http.on(f"{ARCHIVE}/000104581026000070/nvda-20260726.htm", httpx.Response(200, text="<p>Quarterly report</p>"))
    http.on(f"{ARCHIVE}/000104581026000060/nvda-20260820.htm", httpx.Response(200, text="<p>Item 5.02</p>"))
    http.bytes(f"{ARCHIVE}/000192109426000970/wk-form4_1757543405.xml", "edgar/form4_sale.xml")
    http.bytes(f"{ARCHIVE}/000192109426000971/wk-form4a_1757543460.xml", "edgar/form4a_derivative.xml")
    http.bytes(f"{BASE_URL}/company-news", "finnhub/company_news.json")
    http.bytes(f"{BASE_URL}/calendar/earnings", "finnhub/earnings_calendar.json")
    http.bytes(YAHOO, "rss/yahoo_nvda.xml")
    http.bytes(GOOGLE, "rss/google_news.xml")
    http.bytes(QUERY_URL, "alphavantage/news_sentiment.json")
    return http


def run(conn, http, clock, now=NOW, **kw):
    with http.client() as c:
        return sources.ingest(conn, now, client=c, sleep=clock.sleep, clock=clock, **kw)


def source_runs(conn, source=None):
    sql = "SELECT * FROM stock_source_runs" + (" WHERE source = ?" if source else "") + " ORDER BY id"
    return conn.execute(sql, (source,) if source else ()).fetchall()


def resume_from(conn, until):
    """A finished earlier run of every source, so the next one reads from `until` less 6 hours."""
    conn.executemany("INSERT INTO stock_source_runs (source, started_at, status, cursor_json) VALUES (?, ?, 'succeeded', ?)",
                     [(name, until, json.dumps({"until": until})) for name in pipeline.SOURCES])


def test_every_source_stores_and_duplicates_across_sources_collapse(conn, keys, nvda, http, clock):
    config.save_settings(conn, {"ir_feeds": {"NVDA": ["https://ir.example.com/feed"]}})
    resume_from(conn, "2026-08-01T06:00:00Z")
    seeded = len(source_runs(conn))
    report = run(conn, routes(http), clock, download=lambda url: fixture_bytes("rss/ir_atom.xml"))

    assert {k: v["status"] for k, v in report.items()} == {
        "edgar": "succeeded", "finnhub": "succeeded", "yahoo_rss": "succeeded", "google_news": "succeeded",
        "ir_rss": "succeeded", "alphavantage": "succeeded", "earnings": "succeeded"}
    assert not http.unmatched
    # Finnhub, Yahoo RSS and Alpha Vantage all carry the Nvidia-Intel story: stored once, by Finnhub
    # (it runs first), linked to the one followed ticker it names.
    partner = conn.execute("SELECT * FROM market_docs WHERE url LIKE '%nvidia-intel-partner-123%'").fetchall()
    assert [r["source"] for r in partner] == ["finnhub"]
    assert report["yahoo_rss"]["fetched"] == 4 and report["yahoo_rss"]["new"] == 3
    assert report["alphavantage"]["new"] == 1
    assert report["edgar"]["new"] == 5 and report["ir_rss"]["new"] == 2
    total = conn.execute("SELECT COUNT(*) FROM market_docs").fetchone()[0]
    assert total == sum(r["new"] for k, r in report.items() if k != "earnings")
    assert conn.execute("SELECT COUNT(*) FROM market_doc_instruments WHERE instrument_id = ?", (nvda,)).fetchone()[0] == total
    assert conn.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM earnings").fetchone()[0] == 2

    rows = source_runs(conn)[seeded:]
    assert [r["source"] for r in rows] == ["edgar", "finnhub", "yahoo_rss", "google_news", "ir_rss", "alphavantage", "earnings"]
    assert all(r["finished_at"] for r in rows)
    assert json.loads(rows[0]["cursor_json"]) == {"since": "2026-08-01T00:00:00Z", "until": "2026-09-21T12:00:00Z"}
    # EDGAR's ticker map set only the sec_cik key.
    symbols = json.loads(conn.execute("SELECT provider_symbols_json FROM instruments WHERE id = ?", (nvda,)).fetchone()[0])
    assert symbols == {"yahoo": "NVDA", "sec_cik": "0001045810"}

    # Again: nothing new, EDGAR bodies are not downloaded twice, the ticker map not at all.
    before = len(http.calls)
    again = run(conn, http, clock)
    assert all(r["new"] == 0 for r in again.values())
    assert not http.called(TICKERS)[1:]
    assert sum(1 for r in http.calls[before:] if "/Archives/" in r.url.path) == 0


def test_a_source_without_its_credential_is_skipped_not_failed(conn, nvda, http, clock):
    report = run(conn, routes(http), clock)
    for name in ("edgar", "finnhub", "alphavantage", "ir_rss", "earnings"):
        assert report[name]["status"] == "skipped"
    assert report["yahoo_rss"]["status"] == "succeeded" and report["google_news"]["status"] == "succeeded"
    assert {r["source"] for r in source_runs(conn)} == {"yahoo_rss", "google_news"}
    assert not [r for r in http.calls if r.url.host in ("www.sec.gov", "data.sec.gov", "finnhub.io", "www.alphavantage.co")]


def test_a_failing_source_does_not_fail_the_others(conn, keys, nvda, http, clock):
    routes(http).on(f"{BASE_URL}/company-news", httpx.Response(500))
    http.on(SUBMISSIONS, ValueError("adapter bug"))
    report = run(conn, http, clock, only=("edgar", "finnhub", "yahoo_rss"))
    assert report["finnhub"]["status"] == "failed" and "HTTP 500" in report["finnhub"]["error"]
    assert report["edgar"]["status"] == "failed" and "adapter bug" in report["edgar"]["error"]
    assert report["yahoo_rss"]["status"] == "succeeded" and report["yahoo_rss"]["new"] == 4
    assert source_runs(conn, "finnhub")[0]["error"].startswith("NVDA: SourceError: GET https://finnhub.io/api/v1/company-news")


def test_partial_when_one_instrument_fails(conn, keys, nvda, follow, http, clock):
    follow("AMD", "Advanced Micro Devices")

    def news(request):
        if request.url.params["symbol"] == "AMD":
            return httpx.Response(403)
        return httpx.Response(200, content=fixture_bytes("finnhub/company_news.json"))
    http.on(f"{BASE_URL}/company-news", news)
    report = run(conn, http, clock, only=("finnhub",))
    assert report["finnhub"]["status"] == "partial" and report["finnhub"]["error"].startswith("AMD: ")
    assert report["finnhub"]["new"] == 2


def test_cursor_resumes_from_the_last_until_minus_six_hours(conn, keys, nvda, http, clock):
    routes(http)
    run(conn, http, clock, only=("finnhub",))
    later = NOW + timedelta(days=1)
    run(conn, http, clock, now=later, only=("finnhub",))
    first, second = source_runs(conn, "finnhub")
    assert json.loads(second["cursor_json"]) == {"since": "2026-09-21T06:00:00Z", "until": "2026-09-22T12:00:00Z"}
    params = http.called(f"{BASE_URL}/company-news")[-1].url.params
    assert params["from"] == "2026-09-21" and params["to"] == "2026-09-22"

    # A failed run does not move the cursor.
    http.on(f"{BASE_URL}/company-news", httpx.Response(500))
    run(conn, http, clock, now=later + timedelta(days=1), only=("finnhub",))
    http.on(f"{BASE_URL}/company-news", httpx.Response(200, json=[]))
    run(conn, http, clock, now=later + timedelta(days=2), only=("finnhub",))
    assert json.loads(source_runs(conn, "finnhub")[-1]["cursor_json"])["since"] == "2026-09-22T06:00:00Z"


def test_alpha_vantage_budget_is_kept_in_settings_and_stops_the_source(conn, keys, nvda, follow, http, clock):
    follow("AMD", "Advanced Micro Devices")
    routes(http)
    SettingsBudget(conn).save("alphavantage", NOW.date(), 24)
    report = run(conn, http, clock, only=("alphavantage",))
    # AMD (first by symbol) gets the last call of the day; NVDA finds the budget spent.
    assert report["alphavantage"]["status"] == "partial"
    assert "BudgetExhausted" in report["alphavantage"]["error"] and report["alphavantage"]["error"].startswith("NVDA")
    assert len(http.called(QUERY_URL)) == 1
    assert SettingsBudget(conn).load("alphavantage", NOW.date()) == 25
    # The next UTC day has a new budget.
    report = run(conn, http, clock, now=NOW + timedelta(days=1), only=("alphavantage",))
    assert report["alphavantage"]["status"] == "succeeded" and len(http.called(QUERY_URL)) == 3


def test_no_key_reaches_a_stored_error_or_the_log(conn, keys, nvda, http, clock, caplog):
    def leaky(request):
        raise RuntimeError(f"connection reset while fetching {request.url}")
    http.on(QUERY_URL, leaky)
    http.on(f"{BASE_URL}/company-news", lambda r: (_ for _ in ()).throw(RuntimeError(f"bad token {r.headers['X-Finnhub-Token']}")))
    caplog.set_level(logging.DEBUG)
    report = run(conn, http, clock, only=("alphavantage", "finnhub"))
    assert report["alphavantage"]["status"] == "failed" and "connection reset" in report["alphavantage"]["error"]
    stored = " ".join(r["error"] or "" for r in source_runs(conn))
    for key in (AV_KEY, FINNHUB_KEY):
        assert key not in stored and key not in caplog.text and key not in json.dumps(report)
    assert "•••" in stored


def test_rate_limited_source_skips_the_rest(conn, keys, nvda, follow, http, clock):
    follow("AMD", "Advanced Micro Devices")
    http.on(f"{BASE_URL}/company-news", httpx.Response(429))
    report = run(conn, http, clock, only=("finnhub",))
    assert report["finnhub"]["status"] == "failed"
    assert "skipped 1 after rate limit" in report["finnhub"]["error"]
    assert len(http.called(f"{BASE_URL}/company-news")) == 5  # one instrument, four retries


def test_pacing_between_calls_of_one_source(conn, keys, nvda, follow, http, clock):
    follow("AMD", "Advanced Micro Devices")
    follow("INTC", "Intel")
    http.on(f"{BASE_URL}/company-news", httpx.Response(200, json=[]))
    run(conn, http, clock, only=("finnhub",))
    stamps = http.stamps
    assert len(stamps) == 3 and [b - a for a, b in zip(stamps, stamps[1:])] == [1.0, 1.0]


def test_non_us_listing_is_not_covered_by_the_us_sources(conn, keys, follow, http, clock):
    vwce = follow("VWCE.DE", "Vanguard FTSE All-World", exchange="GER", currency="EUR", kind="etf")
    routes(http).on(YAHOO, EMPTY_FEED)
    report = run(conn, http, clock, only=("edgar", "finnhub", "alphavantage", "yahoo_rss"))
    assert report["finnhub"]["not_covered"] == ["VWCE.DE"] and report["alphavantage"]["not_covered"] == ["VWCE.DE"]
    # Not in SEC's ticker map: remembered as not covered, so the map is not fetched again for it.
    assert report["edgar"]["not_covered"] == ["VWCE.DE"]
    symbols = json.loads(conn.execute("SELECT provider_symbols_json FROM instruments WHERE id = ?", (vwce,)).fetchone()[0])
    assert symbols == {"sec_cik": None}
    run(conn, http, clock, only=("edgar",))
    assert len(http.called(TICKERS)) == 1
    assert http.called(YAHOO)[-1].url.params["s"] == "VWCE.DE"


def test_poll_filings_reads_8k_and_form4_and_leaves_the_cursor(conn, keys, nvda, http, clock):
    filings = [("0001045810-26-000101", "8-K", "2026-09-20T10:00:00.000Z", "a.htm"),
               ("0001045810-26-000102", "10-Q", "2026-09-20T11:00:00.000Z", "b.htm"),
               ("0001921094-26-000103", "4", "2026-09-20T12:00:00.000Z", "xslF345X05/c.xml")]
    http.json(SUBMISSIONS, {"filings": {"recent": {
        "accessionNumber": [f[0] for f in filings], "form": [f[1] for f in filings],
        "acceptanceDateTime": [f[2] for f in filings], "primaryDocument": [f[3] for f in filings]}}})
    http.bytes(TICKERS, "edgar/company_tickers.json")
    http.on(f"{ARCHIVE}/000104581026000101/a.htm", httpx.Response(200, text="<p>Item 7.01</p>"))
    http.on(f"{ARCHIVE}/000104581026000102/b.htm", httpx.Response(200, text="<p>Quarterly report</p>"))
    http.bytes(f"{ARCHIVE}/000192109426000103/c.xml", "edgar/form4_sale.xml")
    with http.client() as c:
        report = sources.poll_filings(conn, NOW, client=c, sleep=clock.sleep, clock=clock)
    assert report["edgar"]["status"] == "succeeded" and report["edgar"]["new"] == 2
    forms = {json.loads(r["meta_json"])["form"] for r in conn.execute("SELECT meta_json FROM market_docs")}
    assert forms == {"8-K", "4"}
    assert conn.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0] == 2
    (row,) = source_runs(conn, "edgar")
    assert row["cursor_json"] is None
    # A second poll downloads no body again.
    with http.client() as c:
        assert sources.poll_filings(conn, NOW, client=c, sleep=clock.sleep, clock=clock)["edgar"]["new"] == 0
    assert len(http.called(f"{ARCHIVE}/000104581026000101/a.htm")) == 1
    # The night starts from its own cursor (here the first-run lookback) and gets the 10-Q.
    report = run(conn, http, clock, only=("edgar",))
    assert report["edgar"]["new"] == 1
    assert json.loads(source_runs(conn, "edgar")[-1]["cursor_json"])["since"] == "2026-09-14T12:00:00Z"


def test_poll_filings_without_user_agent(conn, nvda, http, clock):
    with http.client() as c:
        assert sources.poll_filings(conn, NOW, client=c)["edgar"]["status"] == "skipped"
    assert not http.calls


def add_doc(conn, iid, source, published_at, n=[0]):
    n[0] += 1
    doc_id = conn.execute("INSERT INTO market_docs (source, external_id, content_hash, published_at) VALUES (?, ?, ?, ?)",
                          (source, f"x{n[0]}", f"h{n[0]}", published_at)).lastrowid
    conn.execute("INSERT INTO market_doc_instruments (document_id, instrument_id) VALUES (?, ?)", (doc_id, iid))


def test_count_docs_by_tallinn_day_and_source(conn, nvda):
    add_doc(conn, nvda, "finnhub", "2026-09-20T21:30:00Z")  # 00:30 on the 21st in Tallinn
    add_doc(conn, nvda, "finnhub", "2026-09-21T08:00:00Z")
    add_doc(conn, nvda, "edgar", "2026-09-21T08:00:00Z")
    add_doc(conn, nvda, "finnhub", "2026-09-19T08:00:00Z")
    add_doc(conn, nvda, "finnhub", "2026-07-01T08:00:00Z")  # outside the recount
    add_doc(conn, nvda, "finnhub", "2026-09-22T08:00:00Z")  # after today
    assert sources.count_docs(conn, date(2026, 9, 21)) == {"rows": 3, "from": "2026-08-19", "to": "2026-09-21"}
    rows = {(r["date"], r["source"]): r["count"] for r in conn.execute("SELECT * FROM doc_counts")}
    assert rows == {("2026-09-21", "finnhub"): 2, ("2026-09-21", "edgar"): 1, ("2026-09-19", "finnhub"): 1}
    # A recount replaces, it does not add.
    conn.execute("DELETE FROM market_docs WHERE published_at = '2026-09-19T08:00:00Z'")
    sources.count_docs(conn, date(2026, 9, 21))
    assert conn.execute("SELECT SUM(count) FROM doc_counts").fetchone()[0] == 3


def test_attention_z_against_the_thirty_days_before(conn, nvda):
    start = date(2026, 8, 1)
    counts = [2, 4] * 15 + [30]  # 30 baseline days, mean 3, sample std ~1.017
    conn.executemany("INSERT INTO doc_counts (instrument_id, date, source, count) VALUES (?, ?, ?, ?)",
                     [(nvda, (start + timedelta(days=i)).isoformat(), "finnhub", c) for i, c in enumerate(counts)])
    # A second source on the spike day adds to it.
    conn.execute("INSERT INTO doc_counts VALUES (?, '2026-08-31', 'edgar', 3)", (nvda,))
    assert sources.attention_z(conn, nvda, date(2026, 8, 31)) == pytest.approx((33 - 3) / 1.0171, rel=1e-3)
    assert sources.attention_z(conn, nvda, date(2026, 8, 5)) is None  # too little history
    assert sources.attention_z(conn, 999, date(2026, 8, 31)) is None


def test_attention_z_counts_quiet_days_as_zero_from_the_first_count(conn, nvda):
    # One document 40 days back, then nothing until two today: the baseline is 30 zero days.
    conn.execute("INSERT INTO doc_counts VALUES (?, '2026-08-01', 'finnhub', 5)", (nvda,))
    conn.execute("INSERT INTO doc_counts VALUES (?, '2026-09-10', 'finnhub', 2)", (nvda,))
    assert sources.attention_z(conn, nvda, date(2026, 9, 10)) is None  # flat baseline
    conn.execute("INSERT INTO doc_counts VALUES (?, '2026-09-01', 'finnhub', 1)", (nvda,))
    baseline = [0.0] * 29 + [1.0]
    mean = sum(baseline) / 30
    std = (sum((x - mean) ** 2 for x in baseline) / 29) ** 0.5
    assert sources.attention_z(conn, nvda, date(2026, 9, 10)) == pytest.approx((2 - mean) / std)


def test_since_for_a_source_that_never_ran():
    assert pipeline.FIRST_RUN_LOOKBACK == timedelta(days=7) and pipeline.OVERLAP == timedelta(hours=6)
