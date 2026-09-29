from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from app.stocks.portfolio import (
    Account,
    AccountKind,
    InvalidTransaction,
    Lot,
    PortfolioError,
    Position,
    Transaction,
    TxKind,
)
from portfolio_support import D, at, d

TS = at("2026-01-05")


def make(kind: TxKind, **fields: Any) -> Transaction:
    fields.setdefault("currency", "EUR")
    return Transaction(kind=kind, ts=fields.pop("ts", TS), **fields)


@pytest.mark.parametrize(
    ("kind", "fields", "problem"),
    [
        (
            TxKind.BUY,
            {"instrument": "A", "qty": D(1), "price": D(1), "ts": datetime(2026, 1, 5)},  # noqa: DTZ001
            "timezone-aware",
        ),
        (
            TxKind.BUY,
            {"instrument": "A", "qty": D(1), "price": D(1), "currency": "eur"},
            "3-letter",
        ),
        (TxKind.BUY, {"instrument": "A", "qty": 1.5, "price": D(1)}, "finite Decimal"),
        (TxKind.BUY, {"instrument": "A", "qty": D("NaN"), "price": D(1)}, "finite Decimal"),
        (TxKind.BUY, {"qty": D(1), "price": D(1)}, "instrument is required"),
        (TxKind.BUY, {"instrument": "A", "qty": D(0), "price": D(1)}, "qty must be positive"),
        (TxKind.SELL, {"instrument": "A", "qty": D(-1), "price": D(1)}, "qty must be positive"),
        (TxKind.BUY, {"instrument": "A", "qty": D(1)}, "price must be"),
        (TxKind.BUY, {"instrument": "A", "qty": D(1), "price": D(-1)}, "price must be"),
        (
            TxKind.BUY,
            {"instrument": "A", "qty": D(1), "price": D(1), "amount": D(1)},
            "do not apply to trades",
        ),
        (
            TxKind.BUY,
            {"instrument": "A", "qty": D(1), "price": D(1), "fee": D(-1)},
            "must not be negative",
        ),
        (TxKind.DEPOSIT, {"amount": D(0)}, "amount must be positive"),
        (TxKind.DEPOSIT, {}, "amount must be positive"),
        (TxKind.DEPOSIT, {"amount": D(1), "instrument": "A"}, "must not name an instrument"),
        (TxKind.DEPOSIT, {"amount": D(1), "qty": D(1)}, "only apply to buy and sell"),
        (TxKind.DEPOSIT, {"amount": D(1), "fee": D(1)}, "fee only applies"),
        (TxKind.WITHDRAWAL, {"amount": D(1), "tax_withheld": D(1)}, "tax_withheld only"),
        (
            TxKind.DIVIDEND,
            {"instrument": "A", "amount": D(10), "tax_withheld": D(11)},
            "exceeds the gross",
        ),
        (TxKind.SPLIT, {"instrument": "A", "ratio": D(0)}, "ratio must be positive"),
        (TxKind.SPLIT, {"instrument": "A"}, "ratio must be positive"),
        (TxKind.SPLIT, {"instrument": "A", "ratio": D(2), "amount": D(1)}, "amount does not"),
        (TxKind.FEE, {"amount": D(1), "ratio": D(2)}, "ratio only applies"),
    ],
)
def test_invalid_transactions_are_rejected(
    kind: TxKind, fields: dict[str, Any], problem: str
) -> None:
    with pytest.raises(InvalidTransaction, match=problem):
        make(kind, **fields)


def test_trade_date_is_the_tallinn_calendar_date() -> None:
    # 23:30 UTC on 5 January is already 6 January in Tallinn.
    ts = datetime.fromisoformat("2026-01-05T23:30:00+00:00")
    tx = Transaction.deposit(ts, D(100))
    assert tx.trade_date == d("2026-01-06")


def test_gross_and_net() -> None:
    buy = Transaction.buy(TS, "A", D(3), D("2.5"), "EUR", fee=D(1))
    assert buy.gross == D("7.5")
    div = Transaction.dividend(TS, "A", D(100), "USD", tax_withheld=D(15))
    assert (div.gross, div.net) == (D(100), D(85))
    assert Transaction.split(TS, "A", D(4), "EUR").gross == 0


def test_constructors_build_the_right_kinds() -> None:
    assert Transaction.interest(TS, D(1), "EUR").kind is TxKind.INTEREST
    assert Transaction.withdrawal(TS, D(1)).kind is TxKind.WITHDRAWAL
    assert Transaction.account_fee(TS, D(1)).kind is TxKind.FEE
    assert Transaction.sell(TS, "A", D(1), D(1), "EUR").kind is TxKind.SELL


def test_ref_does_not_affect_equality() -> None:
    assert Transaction.deposit(TS, D(1), ref="x") == Transaction.deposit(TS, D(1), ref="y")


def test_only_eur_accounts() -> None:
    assert Account("LHV", AccountKind.INVESTMENT_ACCOUNT).base_currency == "EUR"
    with pytest.raises(PortfolioError, match="EUR"):
        Account("US", AccountKind.REGULAR, base_currency="USD")


def test_position_totals() -> None:
    position = Position(
        "A",
        "EUR",
        (Lot("A", d("2026-01-01"), D(10), D(1005)), Lot("A", d("2026-02-01"), D(5), D(600))),
    )
    assert position.qty == D(15)
    assert position.cost == D(1605)
    assert position.avg_cost == D(107)
    assert position.lots[0].unit_cost == Decimal("100.5")
