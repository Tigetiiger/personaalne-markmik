from app.stocks.portfolio import Transaction, dividends, yield_on_cost
from portfolio_support import D, Fx, at, d

FX = Fx({"USD": {"2025-01-01": "0.90"}})


def test_us_dividend_with_15_percent_withholding() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "KO", D(100), D(60), "USD"),
        Transaction.dividend(at("2026-04-01"), "KO", D(100), "USD", tax_withheld=D(15)),
    ]
    [div] = dividends(txs, d("2026-12-31"), FX)
    assert (div.gross, div.withheld, div.net) == (D(100), D(15), D(85))
    assert div.withholding_rate == D("0.15")
    assert (div.gross_eur, div.withheld_eur, div.net_eur) == (D(90), D("13.5"), D("76.5"))


def test_dividends_window() -> None:
    txs = [
        Transaction.dividend(at("2026-01-10"), "KO", D(1), "EUR"),
        Transaction.dividend(at("2026-04-10"), "KO", D(2), "EUR"),
        Transaction.dividend(at("2026-07-10"), "KO", D(3), "EUR"),
        Transaction.deposit(at("2026-04-11"), D(3)),
    ]
    got = dividends(txs, d("2026-06-30"), FX, since=d("2026-02-01"))
    assert [div.gross for div in got] == [D(2)]


def test_yield_on_cost_uses_trailing_year_over_current_cost() -> None:
    txs = [
        Transaction.buy(at("2024-06-03"), "KO", D(100), D(20), "USD"),  # cost 2000
        # Outside the trailing year:
        Transaction.dividend(at("2025-06-30"), "KO", D(50), "USD", tax_withheld=D("7.5")),
        *(
            Transaction.dividend(at(day), "KO", D(25), "USD", tax_withheld=D("3.75"))
            for day in ("2025-07-01", "2025-10-01", "2026-01-02", "2026-04-01")
        ),
    ]
    # 4 x 25 gross over 2000.
    assert yield_on_cost(txs, "KO", d("2026-06-30")) == D("0.05")


def test_yield_on_cost_when_not_held() -> None:
    txs = [Transaction.buy(at("2026-01-05"), "KO", D(1), D(0), "USD")]
    assert yield_on_cost(txs, "PEP", d("2026-06-30")) is None
    # Held but cost nothing (e.g. a spin-off entered at price 0).
    assert yield_on_cost(txs, "KO", d("2026-06-30")) is None
