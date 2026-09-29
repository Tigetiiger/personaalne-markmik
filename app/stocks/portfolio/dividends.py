"""Dividends: gross, withholding, net, and yield on cost."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .ledger import build_book, in_order
from .model import ZERO, FxLookup, Transaction, TxKind, to_eur


@dataclass(frozen=True, slots=True)
class Dividend:
    instrument: str
    paid: date
    currency: str
    gross: Decimal
    withheld: Decimal
    # EUR at the payment date.
    gross_eur: Decimal
    withheld_eur: Decimal

    @property
    def net(self) -> Decimal:
        return self.gross - self.withheld

    @property
    def net_eur(self) -> Decimal:
        return self.gross_eur - self.withheld_eur

    @property
    def withholding_rate(self) -> Decimal:
        return self.withheld / self.gross


def dividends(
    transactions: Iterable[Transaction], as_of: date, fx: FxLookup, since: date | None = None
) -> list[Dividend]:
    """Dividends paid in `[since, as_of]`, oldest first."""
    result = []
    for tx in in_order(transactions, as_of):
        if tx.kind is not TxKind.DIVIDEND or (since is not None and tx.trade_date < since):
            continue
        assert tx.instrument is not None
        result.append(
            Dividend(
                instrument=tx.instrument,
                paid=tx.trade_date,
                currency=tx.currency,
                gross=tx.gross,
                withheld=tx.tax_withheld,
                gross_eur=to_eur(tx.gross, tx.trade_date, tx.currency, fx),
                withheld_eur=to_eur(tx.tax_withheld, tx.trade_date, tx.currency, fx),
            )
        )
    return result


def yield_on_cost(
    transactions: Iterable[Transaction], instrument: str, as_of: date
) -> Decimal | None:
    """Gross dividends of the trailing 365 days over today's cost basis, both in
    the instrument currency. None when the instrument is not held or cost nothing."""
    transactions = list(transactions)
    position = build_book(transactions, as_of).positions.get(instrument)
    if position is None or position.cost <= 0:
        return None
    start = as_of - timedelta(days=364)
    trailing = sum(
        (
            tx.gross
            for tx in in_order(transactions, as_of)
            if tx.kind is TxKind.DIVIDEND and tx.instrument == instrument and tx.trade_date >= start
        ),
        ZERO,
    )
    return trailing / position.cost
