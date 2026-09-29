"""The rate limiter and the value types (ported from stock-oracle)."""

from datetime import date
from decimal import Decimal

import pytest

from app.stocks.providers import DIVIDEND, SPLIT, Bar, CorporateAction, InstrumentRef, NotCovered, RateLimiter
from app.stocks.providers.types import clean_bars


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_limiter_allows_a_burst_then_waits_for_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, 60.0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        limiter.acquire()
    assert clock.sleeps == []

    clock.now = 10.0
    limiter.acquire()
    assert clock.sleeps == [50.0]  # until the first call leaves the window


def test_limiter_forgets_calls_outside_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, 1.0, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    clock.now = 5.0
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == []


def test_limiter_never_exceeds_rate_over_many_calls() -> None:
    clock = FakeClock()
    limiter = RateLimiter(60, 60.0, clock=clock, sleep=clock.sleep)
    for _ in range(150):
        limiter.acquire()
    assert clock.now >= 120.0


@pytest.mark.parametrize(("calls", "period"), [(0, 1.0), (1, 0.0)])
def test_limiter_rejects_nonsense(calls: int, period: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(calls, period)


def test_corporate_action_validation() -> None:
    with pytest.raises(ValueError, match="positive ratio"):
        CorporateAction(date(2024, 1, 1), SPLIT, "s", ratio=Decimal(0))
    with pytest.raises(ValueError, match="currency"):
        CorporateAction(date(2024, 1, 1), DIVIDEND, "s", amount=Decimal(1))


def test_clean_bars_sorts_dedupes_and_trims() -> None:
    bars = [
        Bar(date(2024, 1, 3), Decimal(3), "s"),
        Bar(date(2024, 1, 2), Decimal(2), "s"),
        Bar(date(2024, 1, 3), Decimal(4), "s"),
        Bar(date(2024, 1, 9), Decimal(9), "s"),
    ]
    assert clean_bars(bars, date(2024, 1, 1), date(2024, 1, 5)) == [
        Bar(date(2024, 1, 2), Decimal(2), "s"),
        Bar(date(2024, 1, 3), Decimal(4), "s"),
    ]


def test_symbol_for() -> None:
    ref = InstrumentRef("VWCE", "EUR", {"yahoo": "VWCE.DE", "finnhub": ""})
    assert ref.symbol_for("yahoo") == "VWCE.DE"
    assert ref.symbol_for("other") == "VWCE"
    with pytest.raises(NotCovered):
        ref.symbol_for("finnhub")


def test_unknown_action_kind() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        CorporateAction(date(2024, 1, 1), "merger", "s")


def test_limiter_is_shared_safely_between_threads() -> None:
    import threading

    clock = FakeClock()
    limiter = RateLimiter(1, 2.0, clock=clock, sleep=clock.sleep)
    threads = [threading.Thread(target=limiter.acquire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert clock.now == 8.0  # four waits of 2 s, none skipped
