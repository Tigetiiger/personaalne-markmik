"""The Portfell numbers, holdings() and position() on sqlite, against hand-computed examples.

The main fixture is stock-oracle's portfolio page example. EUR/USD is 1.10 until 2026-09-18, then
1.25 (1 EUR = rate USD).
- deposit 2000 EUR on 2026-01-02
- buy 10 ACME at 110 USD on 2026-01-05: 1100 USD = 1000 EUR
- buy 5 VWCE at 100 EUR on 2026-02-02
- ACME dividend 10 USD gross, 1.50 withheld, on 2026-06-01: net 8.50 USD = 7.73 EUR
- last closes: ACME 125 USD, VWCE 110 EUR

ACME: 1250 USD = 1000 EUR. Local gain 150 USD; EUR gain 0, made of +120 price (150 / 1.25) and
-120 currency (1100 / 1.25 - 1000). VWCE: 550 EUR, gain +50. Cash: 2000 - 1000 - 500 + 7.73 =
507.73. Value 2057.73; contribution balance 2007.73; deferred tax 22 % x 50 = 11.00.
"""

from datetime import date
from decimal import Decimal

import pytest
from portfolio_support import TODAY, add_account, add_bars, add_fx, add_instrument, add_tx

from app.stocks import config
from app.stocks.portfolio import holdings, position, view
from app.stocks.portfolio.repository import DbFx, PortfolioError, load_all


@pytest.fixture
def example(conn):
    add_fx(conn, date(2023, 1, 2), "USD", "1.10")
    add_fx(conn, date(2026, 9, 18), "USD", "1.25")
    acme = add_instrument(conn, "ACME")
    vwce = add_instrument(conn, "VWCE", kind="etf", currency="EUR", exchange="XETRA")
    add_bars(conn, acme, [120, 125])
    add_bars(conn, vwce, [105, 110])
    account = add_account(conn, "LHV IK")
    add_tx(conn, account, "deposit", date(2026, 1, 2), amount=2000)
    add_tx(conn, account, "buy", date(2026, 1, 5), instrument=acme, currency="USD", qty=10, price=110)
    add_tx(conn, account, "buy", date(2026, 2, 2), instrument=vwce, qty=5, price=100)
    add_tx(conn, account, "dividend", date(2026, 6, 1), instrument=acme, currency="USD", amount=10, tax_withheld="1.5")
    return {"acme": acme, "vwce": vwce, "account": account}


D = Decimal


def test_portfolio_numbers(conn, example):
    v = view.build(conn, TODAY)
    assert v["problems"] == []
    s = v["summary"]
    assert (s["value"], s["cash"], s["net_invested"], s["total"]) == (D("2057.73"), D("507.73"), D("2000.00"), D("57.73"))
    assert s["unrealised"] == {"local": D("200.00"), "eur": D("50.00"), "price": D("170.00"), "fx": D("-120.00")}
    assert s["realised"]["eur"] == 0 and s["income"] == D("7.73") and s["fees"] == 0

    acme, vwce = v["positions"]
    assert acme["symbol"] == "ACME" and acme["qty"] == 10 and acme["avg_cost"] == 110 and acme["price"] == 125
    assert acme["value_eur"] == D("1000.00") and acme["market_price"] and acme["price_as_of"] == "2026-09-20"
    assert acme["pnl"] == {"local": D("150.00"), "eur": D("0.00"), "price": D("120.00"), "fx": D("-120.00")}
    assert acme["yield_on_cost"] == D("0.009091")  # 10 USD over 1100 USD cost
    assert vwce["value_eur"] == D("550.00") and vwce["pnl"]["eur"] == D("50.00") and vwce["pnl_pct"] == D("0.1")

    a = v["allocation"]
    assert [(x["label"], x["weight"]) for x in a["instrument"]] == [("ACME", D("0.645161")), ("VWCE", D("0.354839"))]
    assert [x["label"] for x in a["currency"]] == ["USD", "EUR"]
    assert [x["label"] for x in a["kind"]] == ["Aktsiad", "ETF-id"]

    assert v["dividends"]["years"] == [{"year": 2026, "gross": D("9.09"), "withheld": D("1.36"), "net": D("7.73")}]
    (paid,) = v["dividends"]["recent"]
    assert (paid["symbol"], paid["gross"], paid["withheld"], paid["withholding_rate"]) == ("ACME", 10, D("1.50"), D("0.15"))

    ia = v["investment"]
    assert ia["accounts"] == ["LHV IK"] and ia["balance"] == D("2007.73") and ia["missing_years"] == []
    assert ia["deferred"] == {"account_value": D("2057.73"), "contribution_balance": D("2007.73"),
                              "taxable_gain": D("50.00"), "rate": D("0.22"), "tax": D("11.00")}
    (year,) = ia["years"]
    assert (year["year"], year["contributions"], year["closing_balance"], year["taxable_amount"]) == (
        2026, D("2007.73"), D("2007.73"), D("0.00"))
    # 9.09 EUR gross x 22 % - 1.36 EUR withheld
    assert year["dividend_top_up"] == D("0.64")


def test_returns_and_history(conn, example):
    v = view.build(conn, TODAY)
    # One deposit and no other flow: TWR is the value over the deposit.
    value = D(1000) + D(550) + D(2000) - D(1000) - D(500) + D("8.5") / D("1.1")
    assert v["summary"]["twr"] == (value / 2000 - 1).quantize(D("0.000001"))
    days = (TODAY - date(2026, 1, 2)).days
    assert float(v["summary"]["xirr"]) == pytest.approx(float(value / 2000) ** (365 / days) - 1, abs=1e-6)
    first, last = v["history"][0], v["history"][-1]
    assert first == {"date": date(2026, 1, 1), "value": 0, "invested": 0}
    assert last == {"date": TODAY, "value": D("2057.73"), "invested": D("2000.00")}
    steps = [(b["date"] - a["date"]).days for a, b in zip(v["history"], v["history"][1:])]
    assert max(steps) == 7


def test_quote_beats_the_last_bar(conn, example):
    conn.execute("INSERT INTO quotes (instrument_id, price, previous_close, as_of, source) VALUES (?, '112', '110', "
                 "'2026-09-21T14:00:00Z', 'finnhub')", (example["vwce"],))
    v = view.build(conn, TODAY)
    vwce = next(p for p in v["positions"] if p["symbol"] == "VWCE")
    assert vwce["price"] == 112 and vwce["value_eur"] == D("560.00") and vwce["price_as_of"] == "2026-09-21"


def test_settings_rates_and_opening_balance_are_used(conn, example):
    config.save_settings(conn, {"tax_rates": {"2026": "0.20"}, "opening_balance": "100"})
    ia = view.build(conn, TODAY)["investment"]
    assert ia["opening_balance"] == D("100.00") and ia["balance"] == D("2107.73")
    assert ia["deferred"]["rate"] == D("0.20") and ia["deferred"]["taxable_gain"] == 0 and ia["deferred"]["tax"] == 0


def test_missing_tax_rate_and_price_are_reported(conn):
    add_fx(conn, date(2022, 1, 3), "USD", "1.10")
    old = add_instrument(conn, "OLD")  # no bars stored
    account = add_account(conn, "IK")
    add_tx(conn, account, "deposit", date(2023, 1, 2), amount=100)
    add_tx(conn, account, "buy", date(2023, 1, 3), instrument=old, currency="USD", qty=1, price=50)
    add_tx(conn, account, "dividend", date(2023, 6, 1), instrument=old, currency="USD", amount=1)
    v = view.build(conn, TODAY)
    assert v["problems"] == ["OLD: turuhinda pole salvestatud, väärtus on viimase tehingu hinnaga."]
    assert v["investment"]["missing_years"] == [2023]
    assert [y["year"] for y in v["investment"]["years"]] == [2026, 2025, 2024]
    (old_row,) = v["positions"]
    assert not old_row["market_price"] and old_row["price"] == 50


def test_brokerage_only_has_no_investment_panel(conn):
    account = add_account(conn, "Main", "brokerage")
    add_tx(conn, account, "deposit", date(2026, 3, 2), amount=100)
    v = view.build(conn, TODAY)
    assert v["investment"] is None and v["allocation"] == {} and v["dividends"] == {"years": [], "recent": []}
    assert v["summary"]["value"] == D("100.00")


def test_missing_fx_is_reported_not_fatal(conn):
    acme = add_instrument(conn, "ACME")
    add_bars(conn, acme, [10])
    account = add_account(conn, "Main", "brokerage")
    add_tx(conn, account, "deposit", date(2026, 3, 2), amount=100)
    add_tx(conn, account, "buy", date(2026, 3, 3), instrument=acme, currency="USD", qty=1, price=10)
    v = view.build(conn, TODAY)
    assert any(p.startswith("Väärtused eurodes puuduvad: EUR/USD kurssi pole") for p in v["problems"])
    (row,) = v["positions"]
    assert row["value_eur"] is None and row["pnl"] is None and row["qty"] == 1


def test_no_transactions(conn):
    assert view.build(conn, TODAY) == {"as_of": TODAY, "has_transactions": False, "problems": [], "summary": None,
                                       "positions": [], "allocation": {}, "history": [],
                                       "dividends": {"years": [], "recent": []}, "investment": None}


def test_split_adjusted_bars_are_undone_before_the_split(conn):
    """Bars come split-adjusted: before a 2-for-1 on 2026-03-02 the stored close is half the price
    traded then, while the ledger still holds the pre-split quantity."""
    x = add_instrument(conn, "X", currency="EUR")
    conn.execute("INSERT INTO corporate_actions (instrument_id, date, kind, ratio, source) VALUES (?, '2026-03-02', "
                 "'split', '2', 'yahoo')", (x,))
    add_bars(conn, x, [50] * 30, end=date(2026, 3, 1))
    add_bars(conn, x, [55] * 30, end=date(2026, 3, 31), source="yahoo")
    account = add_account(conn, "Main", "brokerage")
    add_tx(conn, account, "deposit", date(2026, 1, 5), amount=1000)
    add_tx(conn, account, "buy", date(2026, 1, 5), instrument=x, qty=10, price=100)
    before = [h for h in view.build(conn, date(2026, 3, 31))["history"] if date(2026, 2, 1) <= h["date"] < date(2026, 3, 2)]
    assert before and all(h["value"] == D("1000.00") for h in before)
    assert view.build(conn, date(2026, 3, 31))["problems"] == [
        "X: 02.03.2026 oli split (suhe 2), aga tehingutes seda pole. Lisa split Tehingute lehel, muidu on kogus vale."]

    add_tx(conn, account, "split", date(2026, 3, 2), instrument=x, ratio=2)
    v = view.build(conn, date(2026, 3, 31))
    assert v["problems"] == []
    assert v["positions"][0]["qty"] == 20 and v["positions"][0]["value_eur"] == D("1100.00")
    assert v["history"][-1]["value"] == D("1100.00")


def test_holdings(conn, example):
    assert holdings(conn) == {example["acme"]: 10, example["vwce"]: 5}
    assert holdings(conn, date(2026, 1, 31)) == {example["acme"]: 10}
    assert holdings(conn, date(2025, 12, 31)) == {}
    add_tx(conn, example["account"], "split", date(2026, 7, 1), instrument=example["acme"], currency="USD", ratio=4)
    add_tx(conn, example["account"], "sell", date(2026, 8, 1), instrument=example["vwce"], qty=5, price=105)
    assert holdings(conn) == {example["acme"]: 40}


def test_position_across_accounts(conn, example):
    other = add_account(conn, "Hoiu", "brokerage")
    add_tx(conn, other, "deposit", date(2026, 3, 1), amount=1000)
    add_tx(conn, other, "buy", date(2026, 3, 2), instrument=example["acme"], currency="USD", qty=10, price=132)
    p = position(conn, example["acme"], TODAY)
    # 10 at 110 and 10 at 132 USD; EUR cost 1000 + 1200 at 1.10; value 20 x 125 / 1.25.
    assert (p["qty"], p["avg_cost"], p["currency"]) == (20, 121, "USD")
    assert (p["cost_eur"], p["value_eur"], p["gain_eur"]) == (D("2200.00"), D("2000.00"), D("-200.00"))
    assert p["unrealised_pct"] == D("-0.090909")
    assert p["accounts"] == [{"name": "Hoiu", "qty": 10}, {"name": "LHV IK", "qty": 10}]
    assert position(conn, example["acme"], date(2026, 1, 4)) is None
    assert position(conn, 999, TODAY) is None


def test_db_fx_uses_the_latest_rate_and_crosses_through_eur(conn):
    add_fx(conn, date(2026, 1, 2), "USD", "1.10")
    add_fx(conn, date(2026, 1, 2), "GBP", "0.88")
    add_fx(conn, date(2026, 1, 5), "USD", "1.20")
    fx = DbFx(conn)
    assert fx(date(2026, 1, 4), "USD", "EUR") == 1 / D("1.10")
    assert fx(date(2026, 1, 9), "EUR", "USD") == D("1.20")
    assert fx(date(2026, 1, 3), "GBP", "USD") == D("1.10") / D("0.88")
    with pytest.raises(PortfolioError):
        fx(date(2026, 1, 1), "USD", "EUR")


def test_transactions_round_trip_as_decimal_text(conn, example):
    txs = load_all(conn)
    assert [t.kind.value for t in txs] == ["deposit", "buy", "buy", "dividend"]
    assert txs[3].tax_withheld == D("1.5") and txs[1].qty == 10 and txs[1].instrument == str(example["acme"])
    assert conn.execute("SELECT tax_withheld FROM portfolio_tx WHERE kind = 'dividend'").fetchone()[0] == "1.5"


def test_buys_without_a_deposit_explain_the_missing_return(conn):
    """No deposit: cash goes negative, so TWR has nothing to start from; the page says why."""
    x = add_instrument(conn, "X", currency="EUR")
    add_bars(conn, x, [0])
    account = add_account(conn, "Main", "brokerage")
    add_tx(conn, account, "buy", date(2026, 3, 3), instrument=x, qty=1, price=10)
    add_tx(conn, account, "withdrawal", date(2026, 3, 4), amount=1)
    v = view.build(conn, TODAY)
    assert v["summary"]["value"] == D("-11.00") and v["summary"]["twr"] is None
    assert v["problems"] == ["Tootlust ei saa arvutada: konto väärtus oli 04.03.2026 negatiivne. Kas mõni sissemakse on lisamata?"]
