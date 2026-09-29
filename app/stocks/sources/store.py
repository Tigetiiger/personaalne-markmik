"""What the adapters fetched, into the database. Every function is idempotent: storing the same
things twice leaves the database as after the first time. Rewritten on sqlite3 from
stock-oracle's sources/store.py.

`instrument_ids` maps the tickers the adapters used to instrument ids; a ticker not in it (a
co-mentioned company nobody follows) is not linked."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from ...db import tx
from ...util import iso
from ..common import dtext, get_json, set_json
from .models import DocumentIn, EarningsItem

# The Alpha Vantage calls made on a UTC day: settings `stocks:av_calls:<YYYY-MM-DD>`.
BUDGET_KEY = "stocks:av_calls:"


@dataclass
class StoreResult:
    # Ids of rows inserted by this call, in input order.
    inserted: list[int] = field(default_factory=list)
    # Ids of rows that already existed (matched by id, URL or content hash).
    existing: list[int] = field(default_factory=list)


def _find_existing(conn, doc: DocumentIn):
    conditions = ["(source = ? AND external_id = ?)"]
    params: list = [doc.source, doc.external_id]
    if doc.url_hash:
        conditions.append("url_hash = ?")
        params.append(doc.url_hash)
    if doc.content_key:
        conditions.append("content_hash = ?")
        params.append(doc.content_key)
    return conn.execute(f"SELECT id, body FROM market_docs WHERE {' OR '.join(conditions)} ORDER BY id LIMIT 1",
                        params).fetchone()


def _link(conn, document_id: int, tickers: Iterable[str], ids: Mapping[str, int]) -> None:
    conn.executemany("INSERT OR IGNORE INTO market_doc_instruments (document_id, instrument_id) VALUES (?, ?)",
                     [(document_id, ids[t]) for t in dict.fromkeys(tickers) if t in ids])


def store_documents(conn, docs: Iterable[DocumentIn], *, instrument_ids: Mapping[str, int], fetched_at: datetime) -> StoreResult:
    """Insert new documents, link instruments, record Form 4 trades.

    A document that matches a stored row by (source, external_id), URL or content hash is not
    inserted again: its instruments are linked to that row, and a body is filled in when the row
    had none (with the filing's trades, which come with the body)."""
    result = StoreResult()
    for doc in docs:
        with tx(conn):
            existing = _find_existing(conn, doc)
            if existing is not None:
                if existing["body"] is None and doc.body:
                    conn.execute("UPDATE market_docs SET body = ? WHERE id = ?", (doc.body, existing["id"]))
                    if not conn.execute("SELECT 1 FROM insider_trades WHERE document_id = ?", (existing["id"],)).fetchone():
                        _store_trades(conn, existing["id"], doc, instrument_ids)
                _link(conn, existing["id"], doc.instruments, instrument_ids)
                result.existing.append(existing["id"])
                continue
            new_id = conn.execute(
                "INSERT INTO market_docs (source, external_id, url, url_hash, content_hash, title, body, author, language, "
                "published_at, fetched_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc.source, doc.external_id, doc.url, doc.url_hash, doc.content_hash, doc.title, doc.body, doc.author,
                 doc.language, iso(doc.published_at), iso(fetched_at), json.dumps(doc.meta, default=str)),
            ).lastrowid
            _link(conn, new_id, doc.instruments, instrument_ids)
            _store_trades(conn, new_id, doc, instrument_ids)
            result.inserted.append(new_id)
    return result


def _store_trades(conn, document_id: int, doc: DocumentIn, instrument_ids: Mapping[str, int]) -> None:
    # Trades are written only with their document's body, which is what makes this idempotent
    # without a unique key on insider_trades.
    if not doc.insider_trades or not doc.instruments or doc.instruments[0] not in instrument_ids:
        return
    instrument_id = instrument_ids[doc.instruments[0]]
    conn.executemany(
        "INSERT INTO insider_trades (instrument_id, document_id, insider_name, insider_role, trade_date, kind, code, qty, "
        "price, shares_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(instrument_id, document_id, t.insider_name, t.insider_role, t.trade_date.isoformat(), t.kind, t.code,
          dtext(t.shares), dtext(t.price), dtext(t.shares_after)) for t in doc.insider_trades])


def has_body(conn, source: str, external_id: str) -> bool:
    return conn.execute("SELECT 1 FROM market_docs WHERE source = ? AND external_id = ? AND body IS NOT NULL",
                        (source, external_id)).fetchone() is not None


EARNINGS_FIELDS = ("time_of_day", "fiscal_period", "eps_estimate", "eps_actual", "revenue_estimate", "revenue_actual")


def store_earnings(conn, items: Iterable[EarningsItem], *, instrument_ids: Mapping[str, int], now: datetime) -> tuple[int, int]:
    """Upsert by (instrument, date, source): later calls fill in the actuals. (rows, new rows)"""
    rows = {}
    for e in items:
        if e.ticker in instrument_ids:
            rows[(instrument_ids[e.ticker], e.day.isoformat(), e.source)] = (
                e.time_of_day, e.fiscal_period, dtext(e.eps_estimate), dtext(e.eps_actual), dtext(e.revenue_estimate),
                dtext(e.revenue_actual))
    new = 0
    with tx(conn):
        for (iid, day, source), values in rows.items():
            found = conn.execute("SELECT id FROM earnings WHERE instrument_id = ? AND date = ? AND source = ?",
                                 (iid, day, source)).fetchone()
            if found is None:
                conn.execute(
                    f"INSERT INTO earnings (instrument_id, date, source, {', '.join(EARNINGS_FIELDS)}, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (iid, day, source, *values, iso(now)))
                new += 1
            else:
                conn.execute(f"UPDATE earnings SET {', '.join(f'{c} = ?' for c in EARNINGS_FIELDS)}, updated_at = ? WHERE id = ?",
                             (*values, iso(now), found["id"]))
    return len(rows), new


class SettingsBudget:
    """DailyBudget counts in `settings`, so a restart or a second run on the same day continues
    the count. Only the current day's key is kept."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def load(self, source: str, day: date) -> int:
        return int(get_json(self.conn, f"{BUDGET_KEY}{day.isoformat()}", 0) or 0)

    def save(self, source: str, day: date, used: int) -> None:
        key = f"{BUDGET_KEY}{day.isoformat()}"
        with tx(self.conn):
            self.conn.execute("DELETE FROM settings WHERE key LIKE ? AND key != ?", (f"{BUDGET_KEY}%", key))
            set_json(self.conn, key, used)
