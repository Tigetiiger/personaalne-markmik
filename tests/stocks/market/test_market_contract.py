"""The adapter contract (ported from stock-oracle): the same assertions against every provider.

Every environment serves AAPL for August 2020, which contains a dividend (ex 2020-08-07, $0.82 as
paid) and the 4-for-1 split (2020-08-31)."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest
from market_helpers import finnhub_routes, no_wait, yahoo_routes

from app.stocks.providers import (
    SPLIT,
    Bar,
    CorporateAction,
    FallbackProvider,
    FinnhubProvider,
    InstrumentRef,
    NotCovered,
    ProviderError,
    RateLimited,
    SymbolMatch,
    YahooProvider,
)

AAPL = InstrumentRef("AAPL", "USD")
UNKNOWN = InstrumentRef("NOPE", "USD")
START, END = date(2020, 8, 3), date(2020, 9, 4)


@dataclass
class ContractEnv:
    provider: object
    throttle: object
    break_transport: object
    known: InstrumentRef = AAPL
    unknown: InstrumentRef = UNKNOWN


@pytest.fixture(params=["finnhub", "yahoo", "fallback"])
def env(request, router, http):
    finnhub = FinnhubProvider("test-key", http, limiter=no_wait())
    yahoo = YahooProvider(http, limiter=no_wait())
    if request.param == "finnhub":
        finnhub_routes(router, free_tier=False)
        provider = finnhub
    elif request.param == "yahoo":
        yahoo_routes(router)
        provider = yahoo
    else:
        # The real deployment with a key: Finnhub's free tier first, Yahoo behind it.
        finnhub_routes(router, free_tier=True)
        yahoo_routes(router)
        provider = FallbackProvider(finnhub, yahoo)
    return ContractEnv(provider, router.throttle, router.break_transport)


def served_by(env, method):
    return env.provider.served[method] if isinstance(env.provider, FallbackProvider) else env.provider.name


def test_daily_bars_are_sorted_unique_in_range_and_decimal(env):
    bars = env.provider.daily_bars(env.known, START, END)

    assert len(bars) == 25  # trading days, no weekends
    dates = [b.date for b in bars]
    assert dates == sorted(set(dates))
    assert dates[0] == START and dates[-1] == END
    assert all(isinstance(b, Bar) and isinstance(b.close, Decimal) for b in bars)
    assert all(b.low is not None and b.high is not None and b.low <= b.close <= b.high for b in bars)
    assert all(b.volume is not None and b.volume > 0 for b in bars)
    assert {b.source for b in bars} == {served_by(env, "daily_bars")}


def test_daily_bars_are_split_adjusted_as_delivered(env):
    bars = {b.date: b for b in env.provider.daily_bars(env.known, START, END)}

    # Pre-split AAPL traded near $435; split-adjusted it is a quarter of that.
    assert Decimal(100) < bars[date(2020, 8, 28)].close < Decimal(130)
    assert bars[date(2020, 8, 31)].close == Decimal("129.04")


def test_daily_bars_sub_range_and_empty_range(env):
    provider, ref = env.provider, env.known

    sub = provider.daily_bars(ref, date(2020, 8, 8), date(2020, 8, 11))  # Sat..Tue
    assert [b.date for b in sub] == [date(2020, 8, 10), date(2020, 8, 11)]

    assert provider.daily_bars(ref, date(2020, 8, 8), date(2020, 8, 9)) == []  # a weekend


def test_quote(env):
    quote = env.provider.quote(env.known)

    assert quote.price > 0
    assert quote.previous_close is not None and quote.previous_close > 0
    assert quote.as_of.tzinfo is not None
    assert quote.symbol == "AAPL"
    assert quote.source == served_by(env, "quote")


def test_corporate_actions_parse_split_and_dividend(env):
    actions = env.provider.corporate_actions(env.known, START, END)

    assert actions == [
        CorporateAction.dividend(date(2020, 8, 7), Decimal("0.82"), "USD", actions[0].source),
        CorporateAction.split(date(2020, 8, 31), Decimal(4), actions[1].source),
    ]
    assert {a.source for a in actions} == {served_by(env, "corporate_actions")}
    assert actions[1].kind == SPLIT


def test_corporate_actions_outside_range_are_excluded(env):
    assert env.provider.corporate_actions(env.known, date(2020, 8, 8), date(2020, 8, 30)) == []


def test_search_returns_matches(env):
    matches = env.provider.search("apple")

    assert matches
    assert all(isinstance(m, SymbolMatch) and m.symbol and m.name for m in matches)


def test_unknown_symbol_quote_is_not_covered(env):
    with pytest.raises(NotCovered):
        env.provider.quote(env.unknown)


def test_unknown_symbol_bars_are_empty_or_not_covered(env):
    try:
        bars = env.provider.daily_bars(env.unknown, START, END)
    except NotCovered:
        return
    assert bars == []


@pytest.mark.parametrize("method", ["daily_bars", "quote", "corporate_actions", "search"])
def test_throttling_raises_rate_limited(env, method):
    env.throttle()
    with pytest.raises(RateLimited):
        _call(env, method)


@pytest.mark.parametrize("method", ["daily_bars", "quote", "corporate_actions", "search"])
def test_transport_failure_raises_provider_error(env, method):
    env.break_transport()
    with pytest.raises(ProviderError):
        _call(env, method)


def _call(env, method):
    provider, ref = env.provider, env.known
    match method:
        case "daily_bars":
            return provider.daily_bars(ref, START, START + timedelta(days=3))
        case "quote":
            return provider.quote(ref)
        case "corporate_actions":
            return provider.corporate_actions(ref, START, END)
        case _:
            return provider.search("apple")
