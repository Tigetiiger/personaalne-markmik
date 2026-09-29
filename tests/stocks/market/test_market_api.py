"""The market package's JSON API: Jälgin, search, the stock page and its chart."""

import threading
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.stocks import market
from app.stocks.providers.fake import synthetic_bars
from app.views import stocks_market

TODAY = date.today()  # the views read the clock; the bars are made to end there


@pytest.fixture(autouse=True)
def _no_cache():
    stocks_market._intraday_cache.clear()


def seed(fake, conn, symbol="AAPL", *, watch=True, days=1300, **kw):
    fake.add(symbol, synthetic_bars(TODAY - timedelta(days=days), TODAY, **kw), name=f"{symbol} Inc.")
    row = market.add(conn, symbol, name=f"{symbol} Inc.", exchange="US", currency="USD", kind="etf" if symbol == "SPY" else "stock")
    market.backfill(conn, row["id"], TODAY)
    if not watch:
        conn.execute("DELETE FROM watchlist WHERE instrument_id = ?", (row["id"],))
    return row["id"]


def join_backfills():
    for t in threading.enumerate():
        if t.name.startswith("stocks-backfill-"):
            t.join(10)


@pytest.mark.parametrize("path", ["/api/stocks/watchlist", "/api/stocks/search?q=a", "/api/stocks/instruments/AAPL",
                                  "/api/stocks/chart/AAPL", "/api/stocks/chart/AAPL/intraday?range=1D"])
def test_owner_only(client, path):
    assert client.get(path).status_code == 401


def test_empty_watchlist(owner):
    assert owner.get("/api/stocks/watchlist").get_json() == []


def test_add_answers_at_once_and_backfills_on_a_thread(owner, conn, fake):
    fake.add("NVDA", synthetic_bars(TODAY - timedelta(days=2000), TODAY), name="NVIDIA Corporation")

    r = owner.post("/api/stocks/watchlist", json={"symbol": "nvda", "name": "NVIDIA Corporation", "exchange": "US",
                                                  "currency": "USD", "kind": "stock"})

    assert r.status_code == 201, r.get_json()
    row = r.get_json()
    assert row["symbol"] == "NVDA" and row["loading"] is True
    join_backfills()
    row = owner.get("/api/stocks/watchlist").get_json()[0]
    assert row["loading"] is False and row["has_bars"] is True
    assert Decimal(row["price"]) > 0 and row["day_change"] is not None and row["ret_1m"] and row["ret_1y"]
    assert 55 <= len(row["spark"]) <= 66 and all(isinstance(v, str) for v in row["spark"])
    assert row["held"] is None and row["verdict"] is None and row["last_analysis"] is None
    assert row["as_of"] == max(b[0] for b in fake.symbols["NVDA"]["bars"]).isoformat()


def test_adding_again_does_not_backfill_again(owner, conn, fake):
    seed(fake, conn)
    fake.calls.clear()
    r = owner.post("/api/stocks/watchlist", json={"symbol": "AAPL"})
    assert r.status_code == 201 and r.get_json()["loading"] is False and fake.calls == []


@pytest.mark.parametrize("body, message", [({}, "Sümbol puudub"), ({"symbol": "a b"}, "Vigane sümbol"),
                                           ({"symbol": "NOPE"}, "ei leitud"), ({"symbol": "X", "kind": "index"}, "Liik")])
def test_add_refuses(owner, fake, body, message):
    r = owner.post("/api/stocks/watchlist", json=body)
    assert r.status_code == 400 and message in r.get_json()["error"]


def test_rows_carry_held_analysis_and_the_latest_verdict(owner, conn, fake):
    iid = seed(fake, conn)
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) VALUES (?, 'deep', '2026-09-20', 's', 'p', 'm')", (iid,))
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) VALUES (?, 'deep', '2026-09-24', 's', 'p', 'm')", (iid,))
    for day, v in (("2026-09-23", "sell"), ("2026-09-25", "buy"), ("2026-09-24", "hold")):
        conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                     "VALUES (?, ?, ?, 'high', 's', 'p', 'm')", (iid, day, v))
    account = conn.execute("INSERT INTO broker_accounts (name, broker, kind) VALUES ('LHV', 'LHV', 'brokerage')").lastrowid
    conn.execute("INSERT INTO portfolio_tx (account_id, instrument_id, kind, ts, qty, price, currency) "
                 "VALUES (?, ?, 'buy', '2026-09-01T12:00:00Z', '12.5', '100', 'USD')", (account, iid))

    row = owner.get("/api/stocks/watchlist").get_json()[0]

    assert row["held"] == "12.5" and row["last_analysis"] == "2026-09-24"
    assert row["verdict"] == {"verdict": "buy", "conviction": "high", "as_of": "2026-09-25"}


def test_edit_note_and_tags(owner, conn, fake):
    iid = seed(fake, conn)
    r = owner.patch(f"/api/stocks/watchlist/{iid}", json={"note": "  kasvuaktsia  ", "tags": "Tehnoloogia, #USA, usa, "})
    assert r.status_code == 200
    assert (r.get_json()["note"], r.get_json()["tags"]) == ("kasvuaktsia", ["tehnoloogia", "usa"])
    r = owner.patch(f"/api/stocks/watchlist/{iid}", json={"note": "", "tags": []})
    assert (r.get_json()["note"], r.get_json()["tags"]) == ("", [])
    assert owner.patch(f"/api/stocks/watchlist/{iid}", json={"tags": [1]}).status_code == 400
    assert owner.patch(f"/api/stocks/watchlist/{iid}", json={"note": "x" * 2001}).status_code == 400
    assert owner.patch("/api/stocks/watchlist/999", json={"note": "x"}).status_code == 404


def test_remove_keeps_the_instrument_and_its_bars(owner, conn, fake):
    iid = seed(fake, conn)
    assert owner.delete(f"/api/stocks/watchlist/{iid}").status_code == 204
    assert owner.delete(f"/api/stocks/watchlist/{iid}").status_code == 404
    assert owner.get("/api/stocks/watchlist").get_json() == []
    assert conn.execute("SELECT COUNT(*) FROM price_bars WHERE instrument_id = ?", (iid,)).fetchone()[0] > 0
    assert owner.get("/api/stocks/instruments/AAPL").get_json()["watch"] is None


def test_search(owner, fake):
    fake.add("VWCE.DE", [], name="Vanguard FTSE All-World", type="ETF", exchange="GER")
    fake.add("BTC-USD", [], name="Vanguard coin", type="CRYPTOCURRENCY")
    assert owner.get("/api/stocks/search?q=vanguard").get_json() == [
        {"symbol": "VWCE.DE", "name": "Vanguard FTSE All-World", "exchange": "XETRA", "currency": "EUR", "kind": "etf"}]
    assert owner.get("/api/stocks/search?q=%20").get_json() == []
    fake.failure = "throttle"
    r = owner.get("/api/stocks/search?q=vanguard")
    assert r.status_code == 503 and "Proovi minuti pärast" in r.get_json()["error"]


def test_instrument_detail(owner, conn, fake):
    seed(fake, conn, "SPY", watch=False, first=300, seed=2)
    seed(fake, conn)
    d = owner.get("/api/stocks/instruments/aapl").get_json()

    assert d["instrument"]["symbol"] == "AAPL" and d["watch"] == {"note": "", "tags": [], "added_at": d["watch"]["added_at"]}
    assert d["has_bars"] and not d["loading"] and not d["benchmark"]
    assert set(d["stats"]) == {"last_close", "ret_5d", "ret_20d", "ret_60d", "ret_1y", "vol_20d", "vol_60d", "high_52w", "low_52w"}
    assert all(isinstance(v, str) for v in d["stats"].values())
    assert d["trend"]["state"] in ("uptrend", "downtrend", "mixed") and set(d["trend"]["excess"]) == {"20", "60", "252"}
    assert Decimal(d["last"]["price"]) == Decimal(d["stats"]["last_close"])
    assert owner.get("/api/stocks/instruments/SPY").get_json()["benchmark"] is True
    r = owner.get("/api/stocks/instruments/NOPE")
    assert r.status_code == 404 and r.get_json()["error"] == "Tundmatu sümbol."


def test_instrument_without_bars(owner, conn, fake):
    market.add(conn, "NEW", name="New", exchange="US", currency="USD", kind="stock")
    d = owner.get("/api/stocks/instruments/NEW").get_json()
    assert d["has_bars"] is False and d["last"] is None and d["trend"] is None
    assert owner.get("/api/stocks/chart/NEW").get_json()["candles"] == []


def test_chart_payload(owner, conn, fake):
    seed(fake, conn, "SPY", watch=False, first=300, seed=2)
    iid = seed(fake, conn)
    first = market.bars(conn, iid)[0]["date"]
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, ?, 't'), (?, '2000-01-01', 't')", (iid, TODAY.isoformat(), iid))
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) VALUES (?, 'deep', ?, 's', 'p', 'm')",
                 (iid, first))

    c = owner.get("/api/stocks/chart/AAPL").get_json()

    n = len(c["candles"])
    assert c["candles"][0]["time"] == first and all(isinstance(x["close"], float) for x in c["candles"])
    assert len(c["volume"]) == n and len(c["sma_50"]) == n - 49 and len(c["sma_200"]) == n - 199
    assert len(c["relative"]) == n  # SPY has the same days
    assert c["markers"] == [{"time": first, "kind": "research", "text": "R"}, {"time": TODAY.isoformat(), "kind": "earnings", "text": "E"}]
    assert 0 < len(c["monthly"]["candles"]) < len(c["weekly"]["candles"]) < n
    assert set(c["weekly"]) == {"candles", "volume", "sma_50", "sma_200", "relative"}
    assert owner.get("/api/stocks/chart/SPY").get_json()["relative"] == []  # no pane for SPY itself


def test_chart_without_spy_bars_has_no_relative_series(owner, conn, fake):
    seed(fake, conn)
    assert owner.get("/api/stocks/chart/AAPL").get_json()["relative"] == []


def test_intraday_in_tallinn_time_and_cached(owner, conn, fake):
    seed(fake, conn)
    fake.calls.clear()

    r = owner.get("/api/stocks/chart/AAPL/intraday?range=1D")

    body = r.get_json()
    assert r.status_code == 200 and (body["interval"], body["period"], body["depth"], body["more"]) == ("5m", "1d", 0, True)
    assert len(body["candles"]) == 78 and len(body["volume"]) == 78
    last_day = fake.symbols["AAPL"]["bars"][-1][0]
    # The chart reads timestamps as UTC, so the session's open is sent as its Tallinn wall time.
    open_ = datetime.combine(last_day, time(9, 30), ZoneInfo("America/New_York")).astimezone(ZoneInfo("Europe/Tallinn"))
    assert body["candles"][0]["time"] == int(open_.replace(tzinfo=ZoneInfo("UTC")).timestamp())
    assert owner.get("/api/stocks/chart/AAPL/intraday?range=1D").get_json() == body
    assert len(fake.calls) == 1  # the second answer came from the cache
    assert owner.get("/api/stocks/chart/AAPL/intraday?range=1M&depth=4").get_json()["more"] is False


@pytest.mark.parametrize("query", ["range=2Y", "range=1D&depth=3", "range=1D&depth=x"])
def test_intraday_refuses(owner, conn, fake, query):
    seed(fake, conn)
    assert owner.get(f"/api/stocks/chart/AAPL/intraday?{query}").status_code == 400


def test_intraday_when_yahoo_fails(owner, conn, fake):
    seed(fake, conn)
    fake.failure = "down"
    r = owner.get("/api/stocks/chart/AAPL/intraday?range=5D")
    assert r.status_code == 503 and r.get_json()["error"] == "Päevasisesed hinnad pole praegu saadaval."
