"""market.py against a fake market: adding, backfill, the night's refresh, quotes, and the reads."""

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest
from market_helpers import utc

from app import vault
from app.db import tx
from app.stocks import common, market
from app.stocks.providers.fake import synthetic_bars

TODAY = date(2026, 9, 25)  # a Friday
START = date(2021, 3, 1)


def aapl(fake, end=TODAY, **kw):
    fake.add("AAPL", synthetic_bars(START, end, first=100), name="Apple Inc.", **kw)


def spy(fake, end=TODAY):
    fake.add("SPY", synthetic_bars(START, end, first=300, drift=0.0003, seed=2), name="SPDR S&P 500 ETF Trust", type="ETF")


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


# --- add ---------------------------------------------------------------------------------

def test_add_with_every_field_needs_no_network_and_is_idempotent(conn, fake):
    row = market.add(conn, "aapl", name="Apple Inc.", exchange="US", currency="usd", kind="stock")
    again = market.add(conn, "AAPL", name="Apple Inc.", exchange="US", currency="USD", kind="stock")

    assert row["id"] == again["id"] and row["symbol"] == "AAPL" and row["currency"] == "USD"
    assert row["provider_symbols_json"] == "{}"
    assert count(conn, "SELECT COUNT(*) FROM watchlist") == 1
    assert fake.calls == []


def test_add_by_symbol_alone_reads_yahoos_meta(conn, fake):
    fake.add("VWCE.DE", synthetic_bars(START, TODAY), name="Vanguard FTSE All-World", currency="EUR",
             exchange="GER", type="ETF", tz="Europe/Berlin")

    row = market.add(conn, "VWCE.DE")

    assert (row["name"], row["exchange"], row["currency"], row["kind"]) == ("Vanguard FTSE All-World", "XETRA", "EUR", "etf")
    assert row["provider_symbols_json"] == '{"finnhub": null}'
    assert market.add(conn, "VWCE.DE")["id"] == row["id"]
    assert len(fake.calls) == 1  # the second add found the stored instrument


def test_add_when_yahoo_is_down_uses_the_suffix_guesses(conn, fake):
    fake.failure = "down"
    row = market.add(conn, "BANB.SW")
    assert (row["name"], row["exchange"], row["currency"], row["kind"]) == ("BANB.SW", "SIX", "CHF", "stock")


@pytest.mark.parametrize("kw, message", [({"symbol": "a b"}, "Vigane sümbol"), ({"symbol": "NOPE"}, "ei leitud"),
                                         ({"symbol": "X", "kind": "index"}, "Liik"),
                                         ({"symbol": "X", "currency": "euro"}, "valuuta")])
def test_add_refuses(conn, fake, kw, message):
    with pytest.raises(ValueError, match=message):
        market.add(conn, **kw)
    assert count(conn, "SELECT COUNT(*) FROM instruments") == 0


def test_same_symbol_on_two_exchanges(conn, fake):
    a = market.add(conn, "ABC", name="A", exchange="US", currency="USD", kind="stock")
    b = market.add(conn, "ABC", name="A", exchange="XETRA", currency="EUR", kind="stock")
    assert a["id"] != b["id"]


# --- backfill ----------------------------------------------------------------------------

def test_backfill_stores_five_years_with_actions_and_is_idempotent(conn, fake):
    split_day, dividend_day = date(2024, 6, 10), date(2023, 2, 10)
    aapl(fake, dividends={dividend_day: 0.25, date(2020, 1, 10): 1}, splits={split_day: (2, 1)})
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")

    stored = market.backfill(conn, row["id"], TODAY)
    again = market.backfill(conn, row["id"], TODAY)

    first = date(2021, 9, 25)
    assert stored == again == len([b for b in fake.symbols["AAPL"]["bars"] if b[0] >= first])
    assert count(conn, "SELECT COUNT(*) FROM price_bars") == stored
    assert count(conn, "SELECT MIN(date) FROM price_bars") == "2021-09-27"  # the first weekday on or after
    actions = conn.execute("SELECT date, kind, ratio, amount, currency, source FROM corporate_actions ORDER BY date").fetchall()
    # Yahoo's 0.25 was divided by the later 2:1 split; the stored amount is what was paid.
    assert [tuple(a) for a in actions] == [("2023-02-10", "dividend", None, "0.5", "USD", "yahoo"),
                                            ("2024-06-10", "split", "2", None, None, "yahoo")]


def test_backfill_thread_claims_and_releases(app, conn, fake):
    aapl(fake, end=date.today())
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    db = app.config["DATABASE_PATH"]

    assert common.claim(conn, f"backfill:{row['id']}")
    assert not market.start_backfill(db, row["id"])  # one is already running
    common.release(conn, f"backfill:{row['id']}")

    assert market.start_backfill(db, row["id"])
    for t in threading.enumerate():
        if t.name == f"stocks-backfill-{row['id']}":
            t.join(10)
    assert count(conn, "SELECT COUNT(*) FROM price_bars") > 1000
    assert not market.backfill_running(conn, row["id"])


def test_backfill_thread_survives_a_failure(app, conn, fake, caplog):
    fake.failure = "throttle"
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    assert market.start_backfill(app.config["DATABASE_PATH"], row["id"])
    for t in threading.enumerate():
        if t.name.startswith("stocks-backfill-"):
            t.join(10)
    assert "backfill of instrument" in caplog.text and not market.backfill_running(conn, row["id"])


# --- the night's refresh -----------------------------------------------------------------

def test_refresh_prices_creates_spy_backfills_new_and_fetches_fx(conn, fake):
    aapl(fake)
    spy(fake)
    fake.fx = {"USD": 1.17}
    market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")

    result = market.refresh_prices(conn, TODAY)

    assert result["failed"] == {}
    assert result["refreshed"]["AAPL"]["backfill"] and result["refreshed"]["SPY"]["backfill"]
    spy_row = common.by_symbol(conn, "SPY")
    assert spy_row["kind"] == "etf" and count(conn, "SELECT COUNT(*) FROM watchlist WHERE instrument_id = ?", spy_row["id"]) == 0
    assert result["fx"] == {"rates": {"USD": count(conn, "SELECT COUNT(*) FROM fx_rates")}, "failed": {}}
    assert count(conn, "SELECT MIN(date) FROM fx_rates") == "2021-09-24"  # the business day before a Saturday


def test_refresh_overlaps_seven_days_and_takes_revisions(conn, fake):
    aapl(fake, end=TODAY - timedelta(days=7))
    spy(fake)
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    market.backfill(conn, row["id"], TODAY - timedelta(days=7))
    # A week later: Yahoo revised one stored bar, and five new days exist.
    aapl(fake)
    bars = fake.symbols["AAPL"]["bars"]
    revised = next(i for i, b in enumerate(bars) if b[0] == date(2026, 9, 15))
    bars[revised] = (*bars[revised][:4], 555.5, 555.5, 1)
    fake.calls.clear()

    result = market.refresh_prices(conn, TODAY)

    assert result["refreshed"]["AAPL"] == {"bars": 11, "backfill": False}  # 2026-09-11 (the last bar - 7 days) .. 25
    assert market.bars(conn, row["id"], start="2026-09-15", end="2026-09-15")[0]["close"] == Decimal("555.5")
    assert count(conn, "SELECT MAX(date) FROM price_bars WHERE instrument_id = ?", row["id"]) == "2026-09-25"


def test_a_gap_longer_than_the_overlap_is_filled(conn, fake):
    aapl(fake, end=date(2026, 8, 1))
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    market.backfill(conn, row["id"], date(2026, 8, 1))
    aapl(fake)
    spy(fake)
    market.refresh_prices(conn, TODAY)
    stored = {b["date"] for b in market.bars(conn, row["id"], start="2026-07-01")}
    assert stored == {b[0].isoformat() for b in fake.symbols["AAPL"]["bars"] if b[0] >= date(2026, 7, 1)}


def test_a_new_split_rebackfills_and_drops_the_old_scale(conn, fake):
    aapl(fake)
    spy(fake)
    row = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    market.backfill(conn, row["id"], TODAY)
    # stock-oracle's imported bars sit next to ours under another source.
    conn.execute("INSERT INTO price_bars (instrument_id, date, close, source) VALUES (?, '2021-09-27', '999', 'yfinance')", (row["id"],))
    # A 4:1 split on 2026-09-22: Yahoo back-adjusts the whole history.
    split = date(2026, 9, 22)
    fake.symbols["AAPL"]["bars"] = [(b[0], *(v / 4 for v in b[1:6]), b[6]) if b[0] < split else b
                                    for b in fake.symbols["AAPL"]["bars"]]
    fake.symbols["AAPL"]["splits"] = {split: (4, 1)}

    result = market.refresh_prices(conn, TODAY)

    assert result["refreshed"]["AAPL"]["backfill"] is True
    old = market.bars(conn, row["id"], start="2021-09-27", end="2021-09-27")[0]
    assert old["close"] < 40 and count(conn, "SELECT COUNT(*) FROM price_bars WHERE source = 'yfinance'") == 0
    # The split is stored now, so the next night is an ordinary refresh.
    assert market.refresh_prices(conn, TODAY)["refreshed"]["AAPL"]["backfill"] is False


def test_one_failing_instrument_does_not_stop_the_rest(conn, fake):
    aapl(fake)
    spy(fake)
    fake.fx = {}
    market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    market.add(conn, "GONE", name="Gone", exchange="US", currency="SEK", kind="stock")

    result = market.refresh_prices(conn, TODAY)

    assert "GONE" in result["failed"] and "No data found" in result["failed"]["GONE"]
    assert set(result["refreshed"]) == {"AAPL", "SPY"}
    assert set(result["fx"]["failed"]) == {"SEK", "USD"}


def test_fx_refresh_resumes_with_an_overlap(conn, fake):
    spy(fake)
    fake.fx = {"USD": 1.1}
    market.refresh_prices(conn, TODAY - timedelta(days=14))
    fake.calls.clear()
    market.refresh_prices(conn, TODAY)
    rates = [r for r in fake.calls if r.url.host == "api.frankfurter.dev"]
    assert rates[0].url.params["from"] == "2026-09-04"  # the last stored rate (Fri 11th) minus 7 days


# --- quotes ------------------------------------------------------------------------------

@pytest.mark.parametrize("moment, open_", [
    (utc(2026, 9, 25, 13, 29), False), (utc(2026, 9, 25, 13, 30), True), (utc(2026, 9, 25, 19, 59), True),
    (utc(2026, 9, 25, 20, 0), False), (utc(2026, 9, 26, 15, 0), False),  # Saturday
    # The weeks when only the US has moved its clocks: New York is UTC-4 from 8 March 2026.
    (utc(2026, 3, 10, 13, 30), True), (utc(2026, 3, 6, 13, 30), False), (utc(2026, 3, 6, 14, 30), True),
])
def test_us_market_open(moment, open_):
    assert market.us_market_open(moment) is open_


def test_refresh_quotes_only_in_market_hours(conn, fake):
    aapl(fake, price=123.45)
    market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    assert market.refresh_quotes(conn, utc(2026, 9, 26, 15, 0)) == {}
    assert fake.calls == []

    result = market.refresh_quotes(conn, utc(2026, 9, 25, 15, 0))

    assert result == {"quoted": ["AAPL"], "failed": {}}
    q = conn.execute("SELECT * FROM quotes").fetchone()
    assert (q["price"], q["source"], q["fetched_at"]) == ("123.45", "yahoo", "2026-09-25T15:00:00Z")


def test_quotes_use_finnhub_first_when_keyed_and_yahoo_for_the_rest(conn, fake):
    vault.set(conn, "finnhub_key", "test-key")
    aapl(fake, price=120)
    fake.add("VWCE.DE", synthetic_bars(START, TODAY), currency="EUR", tz="Europe/Berlin", price=150)
    fake.finnhub["AAPL"] = (121.5, 120.0, int(utc(2026, 9, 25, 15, 0).timestamp()))
    a = market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    v = market.add(conn, "VWCE.DE", name="Vanguard", exchange="XETRA", currency="EUR", kind="etf")

    result = market.refresh_quotes(conn, utc(2026, 9, 25, 15, 5))

    assert sorted(result["quoted"]) == ["AAPL", "VWCE.DE"]
    assert market.latest_price(conn, a["id"])["source"] == "finnhub"
    assert market.latest_price(conn, v["id"])["source"] == "yahoo"
    # VWCE.DE is marked as not on Finnhub: no request was spent on it there.
    assert [r.url.params["symbol"] for r in fake.calls if r.url.host == "finnhub.io"] == ["AAPL"]


# --- reads -------------------------------------------------------------------------------

def _instrument(conn, symbol="X", currency="USD"):
    return conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, 'US', ?, ?, 'stock')",
                        (symbol, currency, symbol)).lastrowid


def _bars(conn, iid, rows, source="yahoo"):
    with tx(conn):
        conn.executemany("INSERT INTO price_bars (instrument_id, date, open, high, low, close, adj_close, volume, source) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(iid, d, c, c, c, c, adj, 100, source) for d, c, adj in rows])


def test_bars_one_per_date_first_source_wins(conn):
    iid = _instrument(conn)
    _bars(conn, iid, [("2026-09-01", "10", "9"), ("2026-09-02", "11", None)], source="yfinance")
    _bars(conn, iid, [("2026-09-02", "12", "12"), ("2026-09-03", "13", "13")])

    rows = market.bars(conn, iid)

    assert [(r["date"], r["close"]) for r in rows] == [("2026-09-01", Decimal(10)), ("2026-09-02", Decimal(12)),
                                                       ("2026-09-03", Decimal(13))]
    assert rows[0]["adj_close"] == Decimal(9) and isinstance(rows[0]["volume"], int)
    assert [r["date"] for r in market.bars(conn, iid, start=date(2026, 9, 2), end="2026-09-02")] == ["2026-09-02"]


def test_latest_price_prefers_a_fresh_quote(conn):
    iid = _instrument(conn)
    assert market.latest_price(conn, iid) is None
    _bars(conn, iid, [("2026-09-24", "10", "10"), ("2026-09-25", "11", "11")])
    assert market.latest_price(conn, iid) == {"price": Decimal(11), "previous_close": Decimal(10), "as_of": "2026-09-25", "source": "yahoo"}

    conn.execute("INSERT INTO quotes (instrument_id, price, previous_close, as_of, source) VALUES (?, '11.5', '11', '2026-09-25T15:00:00Z', 'finnhub')", (iid,))
    assert market.latest_price(conn, iid)["price"] == Decimal("11.5")

    # The night brought a newer bar than the last quote.
    _bars(conn, iid, [("2026-09-28", "12", "12")])
    assert market.latest_price(conn, iid)["price"] == Decimal(12)


def test_fx_through_eur(conn):
    conn.executemany("INSERT INTO fx_rates (date, base, quote, rate) VALUES (?, 'EUR', ?, ?)",
                     [("2026-09-24", "USD", "1.10"), ("2026-09-25", "USD", "1.20"), ("2026-09-25", "SEK", "11.40")])
    assert market.fx(conn, date(2026, 9, 27), "EUR", "USD") == Decimal("1.20")  # the Friday rate on a Sunday
    assert market.fx(conn, "2026-09-24", "USD", "EUR") == 1 / Decimal("1.10")
    assert market.fx(conn, date(2026, 9, 25), "USD", "SEK") == Decimal("11.40") / Decimal("1.20")
    assert market.fx(conn, date(2026, 9, 25), "GBP", "EUR") is None
    assert market.fx(conn, date(2026, 9, 23), "EUR", "USD") is None  # nothing on or before
    assert market.fx(conn, date(2026, 9, 25), "NOK", "NOK") == 1


def test_price_stats(conn):
    iid = _instrument(conn)
    days = [date(2025, 6, 2) + timedelta(days=i) for i in range(300)]
    # Adjusted closes grow 1 % a day; the plain close is half of it (a dividend-free fiction).
    _bars(conn, iid, [(d.isoformat(), str(Decimal(50) * Decimal("1.01") ** i), str(Decimal(100) * Decimal("1.01") ** i))
                      for i, d in enumerate(days)])

    s = market.price_stats(conn, iid, days[-1])

    assert s["ret_5d"] == (Decimal("1.01") ** 5 - 1).quantize(Decimal("0.0001"))
    assert s["ret_1y"] == (Decimal("1.01") ** 252 - 1).quantize(Decimal("0.0001"))
    assert s["vol_20d"] == 0 and s["vol_60d"] == 0
    assert s["last_close"] == Decimal(50) * Decimal("1.01") ** 299
    assert s["high_52w"] == Decimal(100) * Decimal("1.01") ** 299 and s["low_52w"] == Decimal(100) * Decimal("1.01") ** 47
    # Only what exists up to `today`.
    assert market.price_stats(conn, iid, days[3])["ret_5d"] is None
    assert market.price_stats(conn, iid, date(2020, 1, 1)) == dict.fromkeys(s)


def test_volatility_is_annualised(conn):
    iid = _instrument(conn)
    closes = [100, 101, 100, 101] * 6  # +1 %, -0.99 % alternating
    _bars(conn, iid, [((date(2026, 1, 1) + timedelta(days=i)).isoformat(), str(c), str(c)) for i, c in enumerate(closes)])
    vol = market.price_stats(conn, iid, date(2026, 12, 31))["vol_20d"]
    assert Decimal("0.15") < vol < Decimal("0.17")  # ~1 % a day is ~16 % a year


def test_trend_summary_against_spy(conn, fake):
    aapl(fake)
    spy(fake)
    market.add(conn, "AAPL", name="Apple", exchange="US", currency="USD", kind="stock")
    market.refresh_prices(conn, TODAY)
    a, s = common.by_symbol(conn, "AAPL")["id"], common.by_symbol(conn, "SPY")["id"]

    t = market.trend_summary(conn, a, TODAY)

    assert t["state"] in ("uptrend", "downtrend", "mixed")
    assert set(t["excess"]) == {"20", "60", "252"} and all(isinstance(v, float) for v in t["excess"].values())
    assert t["sma_200"] is not None and t["last_cross"] is None or t["last_cross"]["kind"] in ("golden", "death")
    assert 0 <= t["range_position"] <= 1 and t["drawdown"] <= 0
    assert market.trend_summary(conn, s, TODAY)["excess"] == {}  # SPY against itself: nothing
    assert market.trend_summary(conn, a, date(2000, 1, 1)) is None


def test_change_since():
    series = [("2026-08-25", Decimal(100)), ("2026-09-24", Decimal(105)), ("2026-09-25", Decimal(110))]
    assert market.change_since(series, 30) == Decimal("0.1")
    assert market.change_since(series, 365) is None
    assert market.change_since([], 30) is None
