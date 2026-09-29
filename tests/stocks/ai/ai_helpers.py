"""Helpers for the stocks-ai tests: a scripted fake model that records every call, fake pages
instead of ics.download, and builders for the stocks tables."""

import copy
import json
from datetime import date, datetime, timedelta, timezone

from app.stocks import explore, research, verdict

NOW = datetime(2026, 9, 28, 0, 30, tzinfo=timezone.utc)   # 03:30 in Tallinn, a Monday run date
TODAY = date(2026, 9, 28)
DEADLINE = NOW + timedelta(hours=3)

NAMES = {id(research.EXTRACT_SCHEMA): "extract", id(research.DISCOVER_SCHEMA): "discover",
         id(research.RESEARCH_SCHEMA): "research", id(verdict.SCHEMA): "verdict",
         id(explore.DISCOVER_SCHEMA): "explore_discover", id(explore.SCHEMA): "explore"}


class FakeModel:
    """runner.fake_structured, scripted per call kind: answers[kind] is a dict (copied), a callable
    (prompt -> dict) or an exception to raise. Every call is recorded with the runner's keyword
    arguments (the spy on run_structured adds them)."""

    def __init__(self):
        self.answers = {}
        self.calls = []

    def __call__(self, prompt, schema, model):
        kind = NAMES[id(schema)]
        self.calls[-1].update(kind=kind, model=model)
        answer = self.answers.get(kind)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(prompt)
        if answer is None:
            raise AssertionError(f"no fake answer for {kind}")
        return copy.deepcopy(answer)

    def of(self, kind):
        return [c for c in self.calls if c.get("kind") == kind]


class FakePages:
    """ics.download over a dict of url -> html (or an exception); records (url, user_agent)."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.fetched = []

    def __call__(self, url, time_limit=60, *, user_agent=None):
        self.fetched.append((url, user_agent))
        page = self.pages.get(url)
        if page is None:
            raise ValueError("Lehte ei saanud alla laadida.")
        if isinstance(page, BaseException):
            raise page
        return page if isinstance(page, bytes) else page.encode()


def html(title, *paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><head><title>{title}</title><script>var x = 1;</script></head><body><h1>{title}</h1>{body}</body></html>"


LONG = ("The company reported record data centre revenue and raised its outlook for the coming quarter, "
        "citing strong demand from cloud customers and new product launches. ") * 3


def add_stock(conn, symbol, name, *, kind="stock", watch=True, exchange="NMS", currency="USD"):
    iid = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, ?, ?, ?, ?)",
                       (symbol, exchange, currency, name, kind)).lastrowid
    if watch:
        conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (iid,))
    return iid


def add_bars(conn, iid, days=300, first=100.0, end=TODAY):
    day, price, n = end - timedelta(days=int(days * 1.5)), first, 0
    rows = []
    while n < days:
        day += timedelta(days=1)
        if day.weekday() >= 5 or day > end:
            continue
        price *= 1.001
        rows.append((iid, day.isoformat(), f"{price:.2f}", f"{price:.2f}"))
        n += 1
    conn.executemany("INSERT INTO price_bars (instrument_id, date, close, adj_close, source) VALUES (?, ?, ?, ?, 'yahoo')", rows)


def hold(conn, iid, qty="37", price="101.13", account="LHV Investeerimiskonto"):
    """A real position: one buy in an account, and EUR rates so the EUR figures exist."""
    row = conn.execute("SELECT id FROM broker_accounts WHERE name = ?", (account,)).fetchone()
    aid = row["id"] if row else conn.execute(
        "INSERT INTO broker_accounts (name, broker, kind) VALUES (?, 'LHV', 'investment_account')", (account,)).lastrowid
    conn.execute("INSERT INTO portfolio_tx (account_id, instrument_id, kind, ts, qty, price, currency) "
                 "VALUES (?, ?, 'buy', '2026-06-01T12:00:00Z', ?, ?, 'USD')", (aid, iid, qty, price))
    conn.execute("INSERT OR IGNORE INTO fx_rates (date, base, quote, rate) VALUES ('2026-05-29', 'EUR', 'USD', '1.1000')")


def add_doc(conn, iid, title, body="", *, url=None, source="finnhub", fetched=NOW - timedelta(hours=5),
            published="2026-09-27T10:00:00Z", external_id=None):
    doc = conn.execute(
        "INSERT INTO market_docs (source, external_id, url, url_hash, content_hash, title, body, published_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source, external_id or title, url, _url_hash(url), title, title, body, published,
         fetched.strftime("%Y-%m-%dT%H:%M:%SZ"))).lastrowid
    conn.execute("INSERT INTO market_doc_instruments (document_id, instrument_id) VALUES (?, ?)", (doc, iid))
    return doc


def _url_hash(url):
    from app.stocks.sources.models import normalize_url, sha256
    return sha256(normalize_url(url)) if url else None


def add_signal(conn, doc, iid, *, relevance="0.8", summary="Kokkuvõte.", created="2026-09-27T01:00:00Z"):
    conn.execute("INSERT INTO doc_signals (document_id, instrument_id, sentiment, event_type, novelty, relevance, summary, "
                 "key_facts_json, prompt_version, created_at) VALUES (?, ?, '0.3', 'product', '0.5', ?, ?, '[]', 'extract@x', ?)",
                 (doc, iid, relevance, summary, created))


def research_answer(**over):
    base = {
        "summary": "Andmekeskuste nõudlus kasvab.",
        "changes_since_last": "Esimene märge.",
        "bull": [{"text": "Juhtkond tõstis prognoosi.", "citation": 0}],
        "bear": [{"text": "Klientide kontsentratsioon.", "citation": 1}],
        "catalysts": [{"event": "Q3 tulemused", "date": "2026-11-18"}],
        "risks": ["Ekspordipiirangud"],
        "what_would_change_my_mind": ["Andmekeskuste tellimuste langus"],
        "citations": [{"url": "https://investor.example.com/q2", "title": "Q2 results", "quote": "raised its outlook",
                       "published_at": "2026-08-27"},
                      {"url": "https://news.example.org/risk", "title": "Risk", "quote": "customers", "published_at": None}],
    }
    base.update(over)
    return base


def verdict_answer(**over):
    base = {"verdict": "buy", "conviction": "medium", "summary": "Kasv jätkub.", "reasons_for": ["Tugev nõudlus"],
            "reasons_against": ["Kõrge hind"], "what_would_change": ["Tellimuste langus"]}
    base.update(over)
    return base


def rows(conn, sql, *args):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def loads(value):
    return json.loads(value)


def night(conn, run_date=TODAY):
    """A stock_runs row, as the night package makes one: its id is the run_id."""
    return conn.execute("INSERT INTO stock_runs (run_date, started_at, status) VALUES (?, ?, 'running')",
                        (run_date.isoformat(), NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))).lastrowid
