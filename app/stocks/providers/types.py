"""Value types, errors and the `PriceProvider` protocol shared by every adapter (ported from
stock-oracle's providers/types.py).

Prices are Decimal, never float. Bars are daily and keyed by the exchange's local trading date.
OHLC and close are *split-adjusted as delivered*: Yahoo back-adjusts history when a split happens,
so a new split means the stored history must be fetched again (`market.refresh_prices`). Dividend
amounts are as paid per share at the time, not split-adjusted.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

# corporate_actions.kind
SPLIT = "split"
DIVIDEND = "dividend"


class ProviderError(Exception):
    """A provider failed to answer. The message never contains an API key."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class RateLimited(ProviderError):
    def __init__(self, source: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(source, message)
        self.retry_after = retry_after


class NotCovered(ProviderError):
    """The provider has no data for this symbol/endpoint (or it is premium-only)."""


@dataclass(frozen=True)
class InstrumentRef:
    """What an adapter needs to know about an instrument.

    `provider_symbols` maps provider name to that provider's symbol. A key present with an empty
    value means "this provider does not cover it".
    """

    symbol: str
    currency: str
    provider_symbols: Mapping[str, str | None] = field(default_factory=dict)

    def symbol_for(self, provider: str) -> str:
        if provider not in self.provider_symbols:
            return self.symbol
        mapped = self.provider_symbols[provider]
        if not mapped:
            raise NotCovered(provider, f"{self.symbol} is marked as not covered")
        return mapped


@dataclass(frozen=True)
class Bar:
    date: date
    close: Decimal
    source: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    adj_close: Decimal | None = None
    volume: int | None = None


@dataclass(frozen=True)
class IntradayBar:
    """A bar shorter than a day, for the chart only; never stored."""

    start: datetime  # UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    as_of: datetime
    source: str
    previous_close: Decimal | None = None


@dataclass(frozen=True)
class CorporateAction:
    """A split (`ratio` = new shares per old share) or a cash dividend."""

    date: date
    kind: str
    source: str
    ratio: Decimal | None = None
    amount: Decimal | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.kind == SPLIT:
            if self.ratio is None or self.ratio <= 0:
                raise ValueError(f"split needs a positive ratio, got {self.ratio}")
        elif self.kind == DIVIDEND:
            if self.amount is None or not self.currency:
                raise ValueError("dividend needs an amount and a currency")
        else:
            raise ValueError(f"unknown kind {self.kind!r}")

    @classmethod
    def split(cls, day: dt.date, ratio: Decimal, source: str) -> "CorporateAction":
        return cls(day, SPLIT, source, ratio=ratio)

    @classmethod
    def dividend(cls, day: dt.date, amount: Decimal, currency: str, source: str) -> "CorporateAction":
        return cls(day, DIVIDEND, source, amount=amount, currency=currency)


@dataclass(frozen=True)
class SymbolMatch:
    symbol: str
    name: str
    source: str
    kind: str | None = None
    exchange: str | None = None


class PriceProvider(Protocol):
    """Market data behind one interface. Date ranges are inclusive at both ends.

    Every method raises `ProviderError` (or a subclass) on failure, never a transport- or
    library-specific exception.
    """

    @property
    def name(self) -> str: ...

    def daily_bars(self, ref: InstrumentRef, start: date, end: date) -> list[Bar]:
        """Bars sorted by date, one per date, only dates within [start, end]."""
        ...

    def quote(self, ref: InstrumentRef) -> Quote: ...

    def corporate_actions(self, ref: InstrumentRef, start: date, end: date) -> list[CorporateAction]: ...

    def search(self, query: str) -> list[SymbolMatch]: ...


def clean_bars(bars: Iterable[Bar], start: date, end: date) -> list[Bar]:
    """Drop bars outside [start, end] and duplicates (the last one for a date wins)."""
    by_date = {bar.date: bar for bar in bars if start <= bar.date <= end}
    return [by_date[day] for day in sorted(by_date)]
