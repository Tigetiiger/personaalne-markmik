"""Estonian investment account (investeerimiskonto), TuMS § 17^2.

Mirrors table 6.5 of the Form A return: contributions and withdrawals in EUR,
in date order, across all of the taxpayer's investment accounts. See
TAX_NOTES.md for the sources and for which rules are verified and which are
assumptions. Pass the transactions of *all* investment accounts together.
"""

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from .ledger import in_order
from .model import (
    ZERO,
    FxLookup,
    PortfolioError,
    Transaction,
    TxKind,
    to_eur,
)
from .pnl import account_value_eur

# Income tax rate by tax year, from EMTA (TAX_NOTES.md, S2). Years not listed
# raise UnknownTaxYear: add them only after checking EMTA's current guidance.
DEFAULT_TAX_RATES: Mapping[int, Decimal] = MappingProxyType(
    {2024: Decimal("0.20"), 2025: Decimal("0.22"), 2026: Decimal("0.22")}
)


class UnknownTaxYear(PortfolioError):
    pass


def tax_rate(year: int, rates: Mapping[int, Decimal] = DEFAULT_TAX_RATES) -> Decimal:
    try:
        return rates[year]
    except KeyError:
        raise UnknownTaxYear(
            f"no income tax rate configured for {year}; check EMTA's guidance and add it"
        ) from None


class EntryKind(enum.StrEnum):
    CONTRIBUTION = "contribution"
    WITHDRAWAL = "withdrawal"


# Buys, sells and splits move money between cash and financial assets inside
# the account and are neither. Standalone fees are withdrawals under the
# literal rule (TAX_NOTES.md, assumptions).
_CLASSIFICATION = {
    TxKind.DEPOSIT: EntryKind.CONTRIBUTION,
    TxKind.DIVIDEND: EntryKind.CONTRIBUTION,
    TxKind.INTEREST: EntryKind.CONTRIBUTION,
    TxKind.WITHDRAWAL: EntryKind.WITHDRAWAL,
    TxKind.FEE: EntryKind.WITHDRAWAL,
}


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of table 6.5. `balance_before` is column A; `amount` is B or C."""

    day: date
    kind: EntryKind
    amount: Decimal
    balance_before: Decimal
    taxable: Decimal
    carried_forward: Decimal


def _entry(day: date, kind: EntryKind, amount: Decimal, before: Decimal) -> Entry:
    contribution = amount if kind is EntryKind.CONTRIBUTION else ZERO
    withdrawal = amount if kind is EntryKind.WITHDRAWAL else ZERO
    # Table 6.5: taxable = C - B - A if positive; carried = -(C - B - A) if negative.
    result = withdrawal - contribution - before
    return Entry(day, kind, amount, before, max(result, ZERO), max(-result, ZERO))


def contribution_ledger(
    transactions: Iterable[Transaction],
    as_of: date,
    fx: FxLookup,
    opening_balance: Decimal = ZERO,
) -> list[Entry]:
    """Table 6.5 lines up to `as_of`. `opening_balance` is the contribution
    balance carried in from history that isn't recorded here."""
    if opening_balance < 0:
        raise PortfolioError("the contribution balance cannot be negative")
    entries = []
    balance = opening_balance
    for tx in in_order(transactions, as_of):
        kind = _CLASSIFICATION.get(tx.kind)
        if kind is None:
            continue
        # Dividends and interest count at the net amount credited.
        amount = to_eur(tx.net, tx.trade_date, tx.currency, fx)
        entry = _entry(tx.trade_date, kind, amount, balance)
        entries.append(entry)
        balance = entry.carried_forward
    return entries


def contribution_balance(
    transactions: Iterable[Transaction],
    as_of: date,
    fx: FxLookup,
    opening_balance: Decimal = ZERO,
) -> Decimal:
    entries = contribution_ledger(transactions, as_of, fx, opening_balance)
    return entries[-1].carried_forward if entries else opening_balance


@dataclass(frozen=True, slots=True)
class YearSummary:
    year: int
    opening_balance: Decimal
    contributions: Decimal
    withdrawals: Decimal
    closing_balance: Decimal
    # Table 6.5 "Kokku": declared for `year`, paid the year after.
    taxable_amount: Decimal
    # None when nothing in the year needed a rate.
    rate: Decimal | None
    tax_due: Decimal
    dividends_gross: Decimal
    dividends_withheld: Decimal
    # Estonian tax on top of foreign withholding: max(0, gross x rate - withheld)
    # per dividend. Assumes every dividend is foreign (TAX_NOTES.md).
    dividend_top_up: Decimal
    entries: tuple[Entry, ...]


def year_summary(
    transactions: Iterable[Transaction],
    year: int,
    fx: FxLookup,
    rates: Mapping[int, Decimal] = DEFAULT_TAX_RATES,
    opening_balance: Decimal = ZERO,
) -> YearSummary:
    transactions = list(transactions)
    all_entries = contribution_ledger(transactions, date(year, 12, 31), fx, opening_balance)
    before = [e for e in all_entries if e.day.year < year]
    entries = tuple(e for e in all_entries if e.day.year == year)
    opening = before[-1].carried_forward if before else opening_balance
    closing = entries[-1].carried_forward if entries else opening

    dividend_txs = [
        tx
        for tx in in_order(transactions, date(year, 12, 31))
        if tx.kind is TxKind.DIVIDEND and tx.trade_date.year == year
    ]
    taxable = sum((e.taxable for e in entries), ZERO)
    rate = tax_rate(year, rates) if taxable > 0 or dividend_txs else None

    gross = withheld = top_up = ZERO
    for tx in dividend_txs:
        tx_gross = to_eur(tx.gross, tx.trade_date, tx.currency, fx)
        tx_withheld = to_eur(tx.tax_withheld, tx.trade_date, tx.currency, fx)
        gross += tx_gross
        withheld += tx_withheld
        assert rate is not None
        top_up += max(tx_gross * rate - tx_withheld, ZERO)

    def total(kind: EntryKind) -> Decimal:
        return sum((e.amount for e in entries if e.kind is kind), ZERO)

    return YearSummary(
        year=year,
        opening_balance=opening,
        contributions=total(EntryKind.CONTRIBUTION),
        withdrawals=total(EntryKind.WITHDRAWAL),
        closing_balance=closing,
        taxable_amount=taxable,
        rate=rate,
        tax_due=taxable * rate if rate is not None else ZERO,
        dividends_gross=gross,
        dividends_withheld=withheld,
        dividend_top_up=top_up,
        entries=entries,
    )


@dataclass(frozen=True, slots=True)
class DeferredTax:
    """Income tax that would be due if the whole account were withdrawn on `as_of`."""

    as_of: date
    account_value: Decimal
    contribution_balance: Decimal
    taxable_gain: Decimal
    rate: Decimal
    tax: Decimal


def deferred_tax(
    transactions: Iterable[Transaction],
    as_of: date,
    prices: Mapping[str, Decimal],
    fx: FxLookup,
    rates: Mapping[int, Decimal] = DEFAULT_TAX_RATES,
    opening_balance: Decimal = ZERO,
) -> DeferredTax:
    transactions = list(transactions)
    value = account_value_eur(transactions, as_of, prices, fx)
    balance = contribution_balance(transactions, as_of, fx, opening_balance)
    gain = max(value - balance, ZERO)
    rate = tax_rate(as_of.year, rates)
    return DeferredTax(as_of, value, balance, gain, rate, gain * rate)
