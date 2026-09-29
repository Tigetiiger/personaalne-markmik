"""Allocation of holdings by instrument, currency, and stock vs ETF."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .model import ZERO, FxLookup, Instrument, PortfolioError, Position
from .pnl import market_value_eur, price_for


@dataclass(frozen=True, slots=True)
class Slice:
    value_eur: Decimal
    weight: Decimal


def holdings_eur(
    positions: Mapping[str, Position], prices: Mapping[str, Decimal], as_of: date, fx: FxLookup
) -> dict[str, Decimal]:
    return {
        key: market_value_eur(position, price_for(prices, key), as_of, fx)
        for key, position in positions.items()
    }


def allocate(values: Mapping[str, Decimal], group: Callable[[str], str]) -> dict[str, Slice]:
    """Sum `values` (EUR per instrument) by `group(instrument)` and weight them.
    Weights sum to 1; an empty or all-zero portfolio has no slices."""
    totals: dict[str, Decimal] = {}
    for key, value in values.items():
        if value < 0:
            raise PortfolioError(f"negative holding value for {key}: {value}")
        name = group(key)
        totals[name] = totals.get(name, ZERO) + value
    grand = sum(totals.values(), ZERO)
    if grand == 0:
        return {}
    return {name: Slice(value, value / grand) for name, value in totals.items()}


def _lookup(instruments: Mapping[str, Instrument]) -> Callable[[str], Instrument]:
    def find(key: str) -> Instrument:
        try:
            return instruments[key]
        except KeyError:
            raise PortfolioError(f"unknown instrument {key}") from None

    return find


def by_instrument(values: Mapping[str, Decimal]) -> dict[str, Slice]:
    return allocate(values, lambda key: key)


def by_currency(
    values: Mapping[str, Decimal], instruments: Mapping[str, Instrument]
) -> dict[str, Slice]:
    find = _lookup(instruments)
    return allocate(values, lambda key: find(key).currency)


def by_kind(
    values: Mapping[str, Decimal], instruments: Mapping[str, Instrument]
) -> dict[str, Slice]:
    find = _lookup(instruments)
    return allocate(values, lambda key: find(key).kind.value)
