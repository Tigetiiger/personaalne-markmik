import pytest

from app.stocks.portfolio import (
    Instrument,
    InstrumentKind,
    PortfolioError,
    Slice,
    Transaction,
    by_currency,
    by_instrument,
    by_kind,
    holdings_eur,
    positions,
)
from portfolio_support import D, Fx, at, d

INSTRUMENTS = {
    "AAPL": Instrument("AAPL", "USD", InstrumentKind.STOCK),
    "VWCE": Instrument("VWCE", "EUR", InstrumentKind.ETF),
    "MSFT": Instrument("MSFT", "USD", InstrumentKind.STOCK),
}
VALUES = {"AAPL": D(600), "VWCE": D(300), "MSFT": D(100)}


def test_by_instrument() -> None:
    assert by_instrument(VALUES) == {
        "AAPL": Slice(D(600), D("0.6")),
        "VWCE": Slice(D(300), D("0.3")),
        "MSFT": Slice(D(100), D("0.1")),
    }


def test_by_currency_and_kind() -> None:
    assert by_currency(VALUES, INSTRUMENTS) == {
        "USD": Slice(D(700), D("0.7")),
        "EUR": Slice(D(300), D("0.3")),
    }
    assert by_kind(VALUES, INSTRUMENTS) == {
        "stock": Slice(D(700), D("0.7")),
        "etf": Slice(D(300), D("0.3")),
    }


def test_empty_portfolio_has_no_slices() -> None:
    assert by_instrument({}) == {}
    assert by_instrument({"AAPL": D(0)}) == {}


def test_bad_input() -> None:
    with pytest.raises(PortfolioError, match="unknown instrument"):
        by_kind({"TSLA": D(1)}, INSTRUMENTS)
    with pytest.raises(PortfolioError, match="negative"):
        by_instrument({"AAPL": D(-1)})


def test_holdings_eur_converts_at_as_of() -> None:
    fx = Fx({"USD": {"2026-01-01": "0.5"}})
    txs = [
        Transaction.buy(at("2026-01-05"), "AAPL", D(2), D(100), "USD"),
        Transaction.buy(at("2026-01-05"), "VWCE", D(3), D(100), "EUR"),
    ]
    as_of = d("2026-06-30")
    prices = {"AAPL": D(150), "VWCE": D(110)}
    assert holdings_eur(positions(txs, as_of), prices, as_of, fx) == {
        "AAPL": D(150),
        "VWCE": D(330),
    }
