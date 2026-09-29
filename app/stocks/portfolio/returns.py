"""Time-weighted (TWR) and money-weighted (XIRR) returns."""

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise

from .ledger import build_book, in_order
from .model import (
    ZERO,
    FxLookup,
    PortfolioError,
    Transaction,
    TxKind,
    to_eur,
)
from .pnl import cash_eur, market_value_eur

Dated = tuple[date, Decimal]


def external_flows(transactions: Iterable[Transaction], as_of: date, fx: FxLookup) -> list[Dated]:
    """Deposits (+) and withdrawals (-) in EUR, one entry per day."""
    by_day: dict[date, Decimal] = {}
    for tx in in_order(transactions, as_of):
        if tx.kind in {TxKind.DEPOSIT, TxKind.WITHDRAWAL}:
            sign = 1 if tx.kind is TxKind.DEPOSIT else -1
            amount = to_eur(tx.gross, tx.trade_date, tx.currency, fx) * sign
            by_day[tx.trade_date] = by_day.get(tx.trade_date, ZERO) + amount
    return sorted(by_day.items())


def valuations(
    transactions: Iterable[Transaction],
    dates: Iterable[date],
    price_on: Callable[[date, str], Decimal],
    fx: FxLookup,
) -> list[Dated]:
    """End-of-day account value in EUR on each date."""
    transactions = list(transactions)
    result = []
    for day in sorted(set(dates)):
        holdings = sum(
            (
                market_value_eur(position, price_on(day, key), day, fx)
                for key, position in build_book(transactions, day).positions.items()
            ),
            ZERO,
        )
        result.append((day, cash_eur(transactions, day, fx) + holdings))
    return result


def twr(values: Sequence[Dated], flows: Sequence[Dated]) -> Decimal:
    """Chain-linked time-weighted return over the span of `values`.

    `values` are end-of-day valuations; a flow on day t is taken to arrive at
    the start of day t, so each sub-period returns V_t / (V_{t-1} + F_t) - 1.
    Every flow must fall on a valuation date after the first one.
    """
    if len(values) < 2:
        raise PortfolioError("TWR needs at least two valuations")
    days = [day for day, _ in values]
    if any(b <= a for a, b in pairwise(days)):
        raise PortfolioError("valuation dates must be strictly increasing")
    flow_on: dict[date, Decimal] = {}
    for day, amount in flows:
        if day not in days[1:]:
            raise PortfolioError(f"flow on {day} has no valuation after the first date")
        flow_on[day] = flow_on.get(day, ZERO) + amount

    growth = Decimal(1)
    for (_, before), (day, after) in pairwise(values):
        start = before + flow_on.get(day, ZERO)
        if start == 0:
            if after != 0:
                raise PortfolioError(f"value appeared from nothing on {day}")
            continue
        if start < 0:
            raise PortfolioError(f"negative starting value on {day}")
        growth *= after / start
    return growth - 1


def xirr(cashflows: Sequence[Dated]) -> Decimal:
    """Annualised money-weighted return (actual/365).

    Sign convention: money put in is negative, money taken out (and the final
    value) positive. When several rates solve the equation, the one closest to
    zero is returned.
    """
    flows = [(day, float(amount)) for day, amount in cashflows if amount != 0]
    if not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        raise PortfolioError("XIRR needs at least one positive and one negative cash flow")
    first = min(day for day, _ in flows)
    timed = [((day - first).days / 365.0, amount) for day, amount in flows]

    def npv(rate: float) -> float:
        return sum(amount * math.exp(-t * math.log1p(rate)) for t, amount in timed)

    # Scan a wide grid for sign changes, then bisect the bracket nearest zero.
    # Bisection is slow but cannot diverge, unlike Newton on flat NPV curves.
    grid = sorted(
        {-0.999999, -0.9999, -0.999, -0.99, -0.95, -0.9}
        | {x / 100 for x in range(-85, 101, 5)}
        | {1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0, 100.0, 1000.0, 1e4, 1e5, 1e6}
    )
    points = []
    for rate in grid:
        try:
            points.append((rate, npv(rate)))
        except OverflowError:
            continue
    for rate, value in points:
        if value == 0:
            return _round(rate)
    brackets = [(lo, hi) for (lo, f_lo), (hi, f_hi) in pairwise(points) if (f_lo < 0) != (f_hi < 0)]
    if not brackets:
        raise PortfolioError("XIRR did not find a rate that solves the cash flows")
    lo, hi = min(brackets, key=lambda b: min(abs(b[0]), abs(b[1])))
    f_lo = npv(lo)
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_mid == 0 or hi - lo < 1e-14:
            break
        if (f_mid < 0) == (f_lo < 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return _round((lo + hi) / 2)


def _round(rate: float) -> Decimal:
    return Decimal(repr(rate)).quantize(Decimal("1e-10"))


def account_xirr(
    transactions: Iterable[Transaction], as_of: date, final_value_eur: Decimal, fx: FxLookup
) -> Decimal:
    """XIRR from the investor's side: deposits out, withdrawals and the value at
    `as_of` back in."""
    flows = [(day, -amount) for day, amount in external_flows(transactions, as_of, fx)]
    return xirr([*flows, (as_of, final_value_eur)])
