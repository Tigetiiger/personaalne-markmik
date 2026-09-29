"""stock-oracle's JSON export into the stocks tables: every table, the tricky values, the refusal
and --replace. The fixture folder `oracle/` holds one small hand-made file per table."""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import connect, init_db
from app.stocks import import_oracle
from app.stocks.import_oracle import ImportProblem, run

FIXTURES = Path(__file__).with_name("oracle")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "markmik.db"
    init_db(path)
    conn = connect(path)
    yield conn
    conn.close()


@pytest.fixture
def export(tmp_path):
    """A copy of the fixtures a test may change."""
    folder = tmp_path / "oracle"
    shutil.copytree(FIXTURES, folder)
    return folder


def rows(conn, sql, *args):
    return [dict(r) for r in conn.execute(sql, args)]


def one(conn, sql, *args):
    return dict(conn.execute(sql, args).fetchone())


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in import_oracle.STOCK_TABLES}


def test_every_table_is_counted(db):
    summary = {s[0]: s[1:] for s in run(db, FIXTURES)}
    assert summary == {
        "instrument": ("instruments", 2, 2, 0),
        "watchlist_item": ("watchlist", 2, 2, 0),
        "account": ("broker_accounts", 1, 1, 0),
        "transaction": ("portfolio_tx", 2, 2, 0),
        "price_bar": ("price_bars", 2, 2, 0),
        "corporate_action": ("corporate_actions", 2, 2, 0),
        "fx_rate": ("fx_rates", 2, 2, 0),
        "document": ("market_docs", 3, 3, 0),
        "document_instrument": ("market_doc_instruments", 4, 4, 0),
        "doc_signal": ("doc_signals", 2, 2, 0),
        "insider_trade": ("insider_trades", 1, 1, 0),
        "earnings_event": ("earnings", 1, 1, 0),
        "daily_metric": ("doc_counts", 4, 2, 2),
        "analysis": ("analyses", 2, 2, 0),
        "verdict": ("verdicts", 1, 1, 0),
        "exploration": ("explorations", 1, 1, 0),
        "source_run": ("stock_source_runs", 2, 2, 0),
        "notification": ("stock_notices", 2, 2, 0),
        "alembic_version": (None, 1, 0, 1),
        "claude_run": (None, 1, 0, 1),
        "prediction": (None, 2, 0, 2),
    }
    c = counts(db)
    assert c["quotes"] == c["stock_runs"] == c["stock_ai_runs"] == 0
    assert c["instruments"] == 2 and c["doc_counts"] == 2 and c["stock_notices"] == 2


def test_decimals_keep_every_digit(db):
    run(db, FIXTURES)
    bar = one(db, "SELECT * FROM price_bars WHERE id = 2")
    assert (bar["open"], bar["close"]) == ("1234567890.123456789", "12345678.12345678")
    # common.dtext is str(Decimal), which writes a value this small in exponent form; it is exact.
    assert bar["low"] == "1E-8" and Decimal(bar["low"]) == Decimal("0.00000001")
    assert bar["high"] is None and bar["adj_close"] is None and bar["volume"] is None
    first = one(db, "SELECT * FROM price_bars WHERE id = 1")
    # Trailing zeros are kept as Postgres printed them; Decimal reads them back equal.
    assert first["close"] == "143.43000000" and Decimal(first["adj_close"]) == Decimal("139.8974")
    assert first["volume"] == 75834000
    buy = one(db, "SELECT * FROM portfolio_tx WHERE id = 8")
    assert (buy["qty"], buy["price"], buy["fee"], buy["tax_withheld"]) == ("1E-8", "12345678.12345678", "1.50", "0")
    assert one(db, "SELECT amount FROM portfolio_tx WHERE id = 7")["amount"] == "1000.00"
    assert [r["rate"] for r in rows(db, "SELECT rate FROM fx_rates ORDER BY quote")] == ["1.0843", "1.1738000000"]
    signal = one(db, "SELECT * FROM doc_signals WHERE id = 1")
    assert (signal["sentiment"], signal["novelty"], signal["relevance"]) == ("-0.2500", "0.9000", "1.0000")
    trade = one(db, "SELECT * FROM insider_trades")
    assert (trade["qty"], trade["price"], trade["shares_after"]) == ("129963", "228.8500", "3280295.5")
    earn = one(db, "SELECT * FROM earnings")
    assert (earn["eps_estimate"], earn["revenue_estimate"], earn["eps_actual"]) == ("1.7700", "101234000000", None)
    split, dividend = rows(db, "SELECT * FROM corporate_actions ORDER BY id")
    assert (split["ratio"], split["amount"], dividend["amount"], dividend["currency"]) == ("4", None, "0.26", "USD")


def test_times_become_utc_and_dates_stay(db):
    run(db, FIXTURES)
    assert one(db, "SELECT created_at FROM instruments WHERE id = 1")["created_at"] == "2026-09-21T15:03:56Z"
    # Past midnight in Tallinn is the day before in UTC.
    assert one(db, "SELECT added_at FROM watchlist WHERE instrument_id = 5")["added_at"] == "2026-09-21T21:30:00Z"
    assert one(db, "SELECT ts FROM portfolio_tx WHERE id = 8")["ts"] == "2026-09-22T07:15:30Z"
    assert one(db, "SELECT fetched_at FROM price_bars WHERE id = 2")["fetched_at"] == "2026-09-25T20:00:00Z"
    doc = one(db, "SELECT published_at, fetched_at FROM market_docs WHERE id = 10")
    assert doc == {"published_at": "2026-09-24T19:05:00Z", "fetched_at": "2026-09-24T19:10:00Z"}
    assert one(db, "SELECT published_at FROM market_docs WHERE id = 11")["published_at"] is None
    assert one(db, "SELECT date FROM price_bars WHERE id = 1")["date"] == "2021-09-21"
    assert one(db, "SELECT as_of FROM verdicts")["as_of"] == "2026-09-24"
    assert one(db, "SELECT trade_date FROM insider_trades")["trade_date"] == "2026-09-20"
    unsent = one(db, "SELECT * FROM stock_notices WHERE id = 2")
    assert unsent["sent_at"] is None and unsent["created_at"] == "2026-09-24T19:12:00Z"
    assert one(db, "SELECT sent_at FROM stock_notices WHERE id = 1")["sent_at"] == "2026-09-21T13:56:13Z"


def test_yfinance_becomes_yahoo(db):
    run(db, FIXTURES)
    assert json.loads(one(db, "SELECT provider_symbols_json FROM instruments WHERE id = 5")["provider_symbols_json"]) \
        == {"finnhub": None, "yahoo": "VWCE.DE"}
    assert json.loads(one(db, "SELECT provider_symbols_json FROM instruments WHERE id = 1")["provider_symbols_json"]) \
        == {"finnhub": "AAPL", "sec_cik": "0000320193"}
    assert [r["source"] for r in rows(db, "SELECT source FROM price_bars ORDER BY id")] == ["yahoo", "stooq"]
    assert {r["source"] for r in rows(db, "SELECT source FROM corporate_actions")} == {"yahoo"}


def test_json_columns(db):
    run(db, FIXTURES)
    tags = {r["instrument_id"]: json.loads(r["tags_json"]) for r in rows(db, "SELECT * FROM watchlist")}
    assert tags == {1: [], 5: ["core", "eu"]}
    assert one(db, "SELECT note FROM watchlist WHERE instrument_id = 5")["note"] == "Pikaajaline"
    doc = one(db, "SELECT * FROM market_docs WHERE id = 10")
    assert json.loads(doc["meta_json"]) == {"form": "8-K", "items": ["2.02"], "tone": -1.5}
    assert one(db, "SELECT meta_json FROM market_docs WHERE id = 11")["meta_json"] == "{}"
    assert one(db, "SELECT body FROM market_docs WHERE id = 12")["body"] == "Õnne ja äriühingu tehing."
    s1, s2 = rows(db, "SELECT * FROM doc_signals ORDER BY id")
    assert json.loads(s1["key_facts_json"]) == ["Revenue up 8%", "EPS 1.52"] and s2["key_facts_json"] == "[]"
    assert s1["ai_run_id"] is None
    deep, again = rows(db, "SELECT * FROM analyses ORDER BY id")
    assert json.loads(deep["bull_json"]) == [{"text": "Services grow", "citation": 0}]
    assert json.loads(deep["change_my_mind_json"]) == ["Margins fall"]
    assert json.loads(deep["citations_json"])[0]["url"] == "https://example.com/a"
    assert (deep["kind"], again["kind"], deep["ai_run_id"]) == ("deep", "on_demand", None)
    assert all(again[c] == "[]" for c in ("bull_json", "bear_json", "catalysts_json", "risks_json",
                                          "change_my_mind_json", "citations_json"))
    v = one(db, "SELECT * FROM verdicts")
    assert v["ai_run_id"] is None and v["run_id"] is None
    assert json.loads(v["inputs_json"]) == {"price": {"last_close": 228.85, "ret_5d": -0.0123}, "holding": None}
    assert json.loads(v["reasons_against_json"]) == ["Valuation"]
    assert json.loads(v["what_would_change_json"]) == ["A cheaper price"]
    e = one(db, "SELECT * FROM explorations")
    assert json.loads(e["ideas_json"])[0]["symbol"] == "ASML" and json.loads(e["dropped_json"])[0]["reason"]
    assert e["focus"] is None and e["requested_ideas"] == 3


def test_source_runs_keep_their_cursor(db):
    run(db, FIXTURES)
    edgar, gdelt = rows(db, "SELECT * FROM stock_source_runs ORDER BY id")
    assert edgar["run_id"] is None and edgar["started_at"] == "2026-09-29T09:05:00Z"
    assert json.loads(edgar["cursor_json"]) == {"since": "2026-09-29T02:05:00Z", "until": "2026-09-29T09:05:00Z",
                                                "page": "2"}
    assert (edgar["items_fetched"], edgar["items_new"]) == (2, 1)
    assert gdelt["cursor_json"] is None and gdelt["finished_at"] is None and gdelt["error"] == "HTTP 429"


def test_only_mentions_become_doc_counts(db):
    run(db, FIXTURES)
    assert rows(db, "SELECT * FROM doc_counts ORDER BY instrument_id") == [
        {"instrument_id": 1, "date": "2026-09-20", "source": "gdelt", "count": 7},
        {"instrument_id": 5, "date": "2026-09-21", "source": "finnhub", "count": 3},
    ]


def test_refuses_when_the_stocks_tables_hold_data(db, tmp_path):
    run(db, FIXTURES)
    before = counts(db)
    with pytest.raises(ImportProblem, match="already hold data"):
        run(db, FIXTURES)
    assert counts(db) == before
    with pytest.raises(SystemExit) as exit_:
        import_oracle.main([str(FIXTURES), "--db", str(tmp_path / "markmik.db")])
    assert "already hold data" in str(exit_.value.code)


def test_refuses_when_only_markmiks_own_table_holds_data(db):
    db.execute("INSERT INTO stock_runs (run_date, started_at, status) VALUES ('2026-09-28', '2026-09-27T22:30:00Z', 'succeeded')")
    with pytest.raises(ImportProblem, match="stock_runs 1"):
        run(db, FIXTURES)
    assert counts(db)["instruments"] == 0


def test_replace_empties_what_it_fills(db, export):
    run(db, FIXTURES)
    db.execute("INSERT INTO settings (key, value) VALUES ('keep', 'me')")
    db.execute("INSERT INTO stock_runs (id, run_date, started_at, status) VALUES (9, '2026-09-28', '2026-09-27T22:30:00Z', 'succeeded')")
    db.execute("INSERT INTO stock_ai_runs (run_id, purpose, instrument_id, model, prompt_version, started_at, status) "
               "VALUES (9, 'verdict', 5, 'opus', 'v1', '2026-09-27T23:00:00Z', 'success')")
    db.execute("INSERT INTO quotes (instrument_id, price, as_of, source) VALUES (5, '101.5', '2026-09-28T14:00:00Z', 'yahoo')")
    bars = json.loads((export / "price_bar.json").read_text())
    (export / "price_bar.json").write_text(json.dumps(bars[:1]))
    notices = json.loads((export / "notification.json").read_text())
    notices[0]["title"] = "Uus pealkiri"
    (export / "notification.json").write_text(json.dumps(notices))

    summary = {s[0]: s[1:] for s in run(db, export, replace=True)}
    assert summary["price_bar"] == ("price_bars", 1, 1, 0)
    assert [r["id"] for r in rows(db, "SELECT id FROM price_bars")] == [1]
    assert one(db, "SELECT title FROM stock_notices WHERE id = 1")["title"] == "Uus pealkiri"
    c = counts(db)
    assert c["instruments"] == 2 and c["stock_notices"] == 2 and c["market_docs"] == 3
    # Märkmik's own run history stays; the quote cache went with its instrument.
    assert c["stock_runs"] == 1 and c["stock_ai_runs"] == 1 and c["quotes"] == 0
    assert one(db, "SELECT instrument_id FROM stock_ai_runs")["instrument_id"] is None
    assert one(db, "SELECT value FROM settings WHERE key = 'keep'")["value"] == "me"


def test_unknown_files_are_reported_not_imported(db, export):
    (export / "weird_table.json").write_text('[{"id": 1}]')
    (export / "README.txt").write_text("hi")
    summary = run(db, export)
    assert ("README.txt", "?", 0, 0, 0) in summary and ("weird_table.json", "?", 0, 0, 0) in summary
    text = import_oracle.format_summary(summary)
    assert "weird_table.json" in text and "unknown file, not imported" in text
    assert "(skipped on purpose)" in text


def test_a_missing_table_file_refuses(db, export):
    (export / "account.json").unlink()
    with pytest.raises(ImportProblem, match="account.json"):
        run(db, export)
    assert counts(db)["instruments"] == 0


def test_a_broken_file_refuses(db, export):
    (export / "verdict.json").write_text('[{"id": 1,')
    with pytest.raises(ImportProblem, match="verdict.json: not valid JSON"):
        run(db, export)


def test_a_bad_row_rolls_everything_back(db, export):
    docs = json.loads((export / "insider_trade.json").read_text())
    docs[0]["kind"] = "gift"
    (export / "insider_trade.json").write_text(json.dumps(docs))
    with pytest.raises(ImportProblem, match="insider_trade id 1 -> insider_trades"):
        run(db, export)
    assert all(n == 0 for n in counts(db).values())


def test_a_timestamp_without_a_zone_refuses(db, export):
    (export / "account.json").write_text('[{"id":3,"name":"LHV","broker":"LHV","base_currency":"EUR",'
                                         '"kind":"brokerage","created_at":"2026-09-21T10:00:00"}]')
    with pytest.raises(ImportProblem, match="without a zone"):
        run(db, export)


def test_cli_imports_into_database_path(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli" / "markmik.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    import_oracle.main([str(FIXTURES)])
    out = capsys.readouterr().out
    assert "price_bar" in out and "price_bars" in out and "prediction" in out
    conn = connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM market_docs").fetchone()[0] == 3
    finally:
        conn.close()
