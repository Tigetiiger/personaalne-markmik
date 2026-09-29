"""The store on SQLite (ported from stock-oracle's integration/sources/test_store.py)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from news_helpers import fixture_bytes

from app.stocks.sources.edgar import form4_summary
from app.stocks.sources.form4 import parse_form4
from app.stocks.sources.models import DocumentIn, EarningsItem
from app.stocks.sources.store import store_documents, store_earnings

FETCHED = datetime(2026, 9, 21, 1, 45, tzinfo=UTC)
PUBLISHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def ids(follow):
    return {"NVDA": follow("NVDA", "NVIDIA"), "INTC": follow("INTC", "Intel")}


def doc(source="finnhub", ext="1", *, url="https://example.com/story", title="Nvidia and Intel partner",
        body="The two companies will co-develop products.", tickers=("NVDA",), **extra):
    return DocumentIn(source=source, external_id=ext, url=url, title=title, body=body, author=None,
                      published_at=PUBLISHED, instruments=tickers, **extra)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def links(conn, document_id):
    return {r[0] for r in conn.execute("SELECT instrument_id FROM market_doc_instruments WHERE document_id = ?", (document_id,))}


def test_store_is_idempotent(conn, ids):
    docs = [doc(meta={"publisher": "Reuters"}), doc(ext="2", url="https://example.com/other", body="x")]
    first = store_documents(conn, docs, instrument_ids=ids, fetched_at=FETCHED)
    second = store_documents(conn, docs, instrument_ids=ids, fetched_at=FETCHED)
    assert len(first.inserted) == 2 and first.existing == []
    assert second.inserted == [] and second.existing == first.inserted
    assert count(conn, "market_docs") == 2

    row = conn.execute("SELECT * FROM market_docs WHERE id = ?", (first.inserted[0],)).fetchone()
    assert row["published_at"] == "2026-09-20T12:00:00Z" and row["fetched_at"] == "2026-09-21T01:45:00Z"
    assert json.loads(row["meta_json"]) == {"publisher": "Reuters"}
    assert row["url_hash"] == docs[0].url_hash and row["content_hash"] == docs[0].content_hash


def test_cross_source_duplicate_links_to_the_first_row(conn, ids):
    (first,) = store_documents(conn, [doc()], instrument_ids=ids, fetched_at=FETCHED).inserted
    # The same story from another source under a decorated URL, about another ticker too.
    again = doc(source="yahoo_rss", ext="y-1", url="http://www.example.com/story/?utm_source=rss",
                tickers=("NVDA", "INTC", "NOT_WATCHED"))
    result = store_documents(conn, [again], instrument_ids=ids, fetched_at=FETCHED)
    assert result.existing == [first] and count(conn, "market_docs") == 1
    assert links(conn, first) == {ids["NVDA"], ids["INTC"]}


def test_content_hash_duplicate_without_shared_url(conn, ids):
    store_documents(conn, [doc()], instrument_ids=ids, fetched_at=FETCHED)
    rewrapped = doc(source="alphavantage", ext="m-1", url="https://other.com/a", title="NVIDIA AND INTEL PARTNER!")
    assert store_documents(conn, [rewrapped], instrument_ids=ids, fetched_at=FETCHED).inserted == []
    assert count(conn, "market_docs") == 1


def test_title_only_documents_are_not_matched_on_text(conn, ids):
    a = doc(source="edgar", ext="acc-1", url=None, title="NVIDIA 8-K: 8-K", body=None)
    b = doc(source="edgar", ext="acc-2", url=None, title="NVIDIA 8-K: 8-K", body=None)
    assert len(store_documents(conn, [a, b], instrument_ids=ids, fetched_at=FETCHED).inserted) == 2


def test_missing_body_is_filled_in_later_with_its_trades(conn, ids):
    empty = doc(source="edgar", ext="acc-1", url="https://sec.gov/x", body=None)
    (row_id,) = store_documents(conn, [empty], instrument_ids=ids, fetched_at=FETCHED).inserted
    form = parse_form4(fixture_bytes("edgar/form4_sale.xml"))
    full = doc(source="edgar", ext="acc-1", url="https://sec.gov/x", body="Item 2.02 results", insider_trades=form.transactions)
    store_documents(conn, [full], instrument_ids=ids, fetched_at=FETCHED)
    store_documents(conn, [full], instrument_ids=ids, fetched_at=FETCHED)
    assert conn.execute("SELECT body FROM market_docs WHERE id = ?", (row_id,)).fetchone()[0] == "Item 2.02 results"
    assert count(conn, "insider_trades") == 2


def test_form4_trades_are_stored_once(conn, ids):
    form = parse_form4(fixture_bytes("edgar/form4a_derivative.xml"))
    filing = doc(source="edgar", ext="0001921094-26-000971", url="https://www.sec.gov/Archives/x.xml", title="NVIDIA 4/A",
                 body=form4_summary(form), insider_trades=form.transactions)
    store_documents(conn, [filing], instrument_ids=ids, fetched_at=FETCHED)
    store_documents(conn, [filing], instrument_ids=ids, fetched_at=FETCHED)
    trades = conn.execute("SELECT * FROM insider_trades ORDER BY id").fetchall()
    assert len(trades) == 5
    buy = next(t for t in trades if t["code"] == "P")
    assert buy["kind"] == "buy" and Decimal(buy["qty"]) == Decimal("500") and buy["price"] == "178.00"
    assert buy["insider_name"] == "Kress Colette" and buy["instrument_id"] == ids["NVDA"]
    assert {t["kind"] for t in trades} == {"buy", "other"}


def test_trades_for_unknown_instrument_are_skipped(conn, ids):
    form = parse_form4(fixture_bytes("edgar/form4_sale.xml"))
    filing = doc(source="edgar", ext="acc-x", tickers=("ZZZZ",), insider_trades=form.transactions)
    store_documents(conn, [filing], instrument_ids=ids, fetched_at=FETCHED)
    assert count(conn, "insider_trades") == 0 and count(conn, "market_docs") == 1


def test_earnings_upsert_updates_actuals(conn, ids):
    upcoming = EarningsItem(ticker="NVDA", day=date(2026, 11, 19), time_of_day=None, fiscal_period="2027Q3",
                            eps_estimate=Decimal("1.12"), eps_actual=None, revenue_estimate=Decimal("56000000000"),
                            revenue_actual=None, source="finnhub")
    assert store_earnings(conn, [upcoming], instrument_ids=ids, now=FETCHED) == (1, 1)
    reported = EarningsItem(**{**upcoming.__dict__, "eps_actual": Decimal("1.20"), "time_of_day": "amc"})
    assert store_earnings(conn, [reported], instrument_ids=ids, now=FETCHED) == (1, 0)
    (row,) = conn.execute("SELECT * FROM earnings").fetchall()
    assert row["eps_actual"] == "1.20" and row["time_of_day"] == "amc" and row["eps_estimate"] == "1.12"
    assert store_earnings(conn, [], instrument_ids=ids, now=FETCHED) == (0, 0)
    assert store_earnings(conn, [EarningsItem(**{**upcoming.__dict__, "ticker": "ZZZ"})], instrument_ids=ids, now=FETCHED) == (0, 0)
