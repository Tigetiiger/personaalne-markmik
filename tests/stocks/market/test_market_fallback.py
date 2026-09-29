"""FallbackProvider (ported from stock-oracle)."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.stocks.providers import (
    Bar,
    CorporateAction,
    FallbackProvider,
    InstrumentRef,
    NotCovered,
    ProviderError,
    Quote,
    RateLimited,
    SymbolMatch,
)

REF = InstrumentRef("X", "USD")
DAY = date(2024, 1, 2)


@dataclass
class Stub:
    name: str
    error: ProviderError | None = None
    calls: list[str] = field(default_factory=list)

    def _answer(self, method: str) -> None:
        self.calls.append(method)
        if self.error is not None:
            raise self.error

    def daily_bars(self, ref: InstrumentRef, start: date, end: date) -> list[Bar]:
        self._answer("daily_bars")
        return [Bar(DAY, Decimal(1), self.name)]

    def quote(self, ref: InstrumentRef) -> Quote:
        self._answer("quote")
        return Quote("X", Decimal(1), datetime(2024, 1, 2, tzinfo=UTC), self.name)

    def corporate_actions(
        self, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        self._answer("corporate_actions")
        return [CorporateAction.split(DAY, Decimal(2), self.name)]

    def search(self, query: str) -> list[SymbolMatch]:
        self._answer("search")
        return [SymbolMatch("X", "X Corp", self.name)]


def test_primary_serves_when_healthy() -> None:
    primary, fallback = Stub("a"), Stub("b")
    provider = FallbackProvider(primary, fallback)

    assert provider.daily_bars(REF, DAY, DAY)[0].source == "a"
    assert provider.served == {"daily_bars": "a"}
    assert fallback.calls == []
    assert provider.name == "a+b"


@pytest.mark.parametrize(
    "error",
    [
        RateLimited("a", "429", retry_after=5),
        NotCovered("a", "premium"),
        ProviderError("a", "HTTP 500"),
    ],
)
def test_any_provider_error_falls_back(error: ProviderError) -> None:
    provider = FallbackProvider(Stub("a", error), Stub("b"))

    assert provider.quote(REF).source == "b"
    assert provider.corporate_actions(REF, DAY, DAY)[0].source == "b"
    assert provider.search("x")[0].source == "b"
    assert provider.daily_bars(REF, DAY, DAY)[0].source == "b"
    assert set(provider.served.values()) == {"b"}


def test_fallback_failure_propagates_with_primary_as_context() -> None:
    primary_error = RateLimited("a", "429")
    provider = FallbackProvider(Stub("a", primary_error), Stub("b", NotCovered("b", "nope")))

    with pytest.raises(NotCovered) as info:
        provider.quote(REF)
    assert info.value.__context__ is primary_error


def test_non_provider_errors_are_not_swallowed() -> None:
    class Broken(Stub):
        def quote(self, ref: InstrumentRef) -> Quote:
            raise ZeroDivisionError

    provider = FallbackProvider(Broken("a"), Stub("b"))
    with pytest.raises(ZeroDivisionError):
        provider.quote(REF)
