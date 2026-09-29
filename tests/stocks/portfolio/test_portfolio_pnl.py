"""P&L for a USD stock in a EUR account, worked by hand.

EUR per USD: 0.90 on 5 Jan, 0.80 on 2 Mar, 0.85 on 30 Jun.
Buy 10 @ 100 USD on 5 Jan: cost 1000 USD = 900 EUR.
Sell 4 @ 120 on 2 Mar: proceeds 480 USD = 384 EUR, cost 400 USD = 360 EUR.
  local +80; EUR +24 = price +64 (80 x 0.80) + FX -40 (400 x 0.80 - 360).
Hold 6 at 150 on 30 Jun: value 900 USD = 765 EUR, cost 600 USD = 540 EUR.
  local +300; EUR +225 = price +255 (300 x 0.85) + FX -30 (600 x 0.85 - 540).
"""

import pytest

from app.stocks.portfolio import (
    PnL,
    PortfolioError,
    Transaction,
    account_value_eur,
    build_book,
    cash_eur,
    pnl_summary,
    realised_pnl,
    unrealised_pnl,
)
from portfolio_support import D, Fx, at, d, no_fx

FX = Fx({"USD": {"2026-01-05": "0.90", "2026-03-02": "0.80", "2026-06-30": "0.85"}})
AS_OF = d("2026-06-30")
TXS = [
    Transaction.deposit(at("2026-01-05", "09:00"), D(1000)),
    Transaction.buy(at("2026-01-05"), "AAPL", D(10), D(100), "USD"),
    Transaction.sell(at("2026-03-02"), "AAPL", D(4), D(120), "USD"),
    # Paid on 1 Jun, so converted at the 2 Mar rate (latest before): 5.10 x 0.80.
    Transaction.dividend(at("2026-06-01"), "AAPL", D(6), "USD", tax_withheld=D("0.90")),
]


def test_realised_usd_sale() -> None:
    [sale] = build_book(TXS, AS_OF).realisations
    assert realised_pnl(sale, FX) == PnL(
        local=D(80), eur=D(24), price_effect_eur=D(64), fx_effect_eur=D(-40)
    )


def test_unrealised_usd_position() -> None:
    position = build_book(TXS, AS_OF).positions["AAPL"]
    assert unrealised_pnl(position, D(150), AS_OF, FX) == PnL(
        local=D(300), eur=D(225), price_effect_eur=D(255), fx_effect_eur=D(-30)
    )


def test_summary_adds_up() -> None:
    summary = pnl_summary(TXS, AS_OF, {"AAPL": D(150)}, FX)
    assert summary.realised.eur == D(24)
    assert summary.unrealised.eur == D(225)
    assert summary.income == D("4.08")
    assert summary.fees == 0
    assert summary.net_invested == D(1000)
    assert summary.cash == D("488.08")  # 1000 - 900 + 384 + 4.08
    assert summary.holdings_value == D(765)
    assert summary.value == D("1253.08")
    assert summary.total == D("253.08")
    assert summary.total == (
        summary.realised.eur + summary.unrealised.eur + summary.income - summary.fees
    )


def test_eur_instruments_need_no_fx() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(2000)),
        Transaction.buy(at("2026-01-05"), "VWCE", D(10), D(100), "EUR", fee=D(1)),
        Transaction.sell(at("2026-02-05"), "VWCE", D(5), D(110), "EUR", fee=D(1)),
        Transaction.account_fee(at("2026-02-06"), D(2)),
        Transaction.withdrawal(at("2026-02-07"), D(100)),
        Transaction.interest(at("2026-02-08"), D(3), "EUR"),
    ]
    summary = pnl_summary(txs, AS_OF, {"VWCE": D(120)}, no_fx)
    # Sold half of a 1001 lot for 549: +48.5. Remaining 5 at 120 vs 500.5: +99.5.
    assert summary.realised == PnL(D("48.5"), D("48.5"), D("48.5"), D(0))
    assert summary.unrealised == PnL(D("99.5"), D("99.5"), D("99.5"), D(0))
    assert (summary.fees, summary.income, summary.net_invested) == (D(2), D(3), D(1900))
    # 2000 - 1001 + 549 - 2 - 100 + 3
    assert summary.cash == D(1449)
    assert summary.total == D("149")  # 48.5 + 99.5 + 3 - 2


def test_account_value_and_cash() -> None:
    assert cash_eur(TXS, AS_OF, FX) == D("488.08")
    assert account_value_eur(TXS, AS_OF, {"AAPL": D(150)}, FX) == D("1253.08")


def test_missing_or_negative_price_is_an_error() -> None:
    with pytest.raises(PortfolioError, match="no price for AAPL"):
        pnl_summary(TXS, AS_OF, {}, FX)
    with pytest.raises(PortfolioError, match="negative price"):
        account_value_eur(TXS, AS_OF, {"AAPL": D(-1)}, FX)
