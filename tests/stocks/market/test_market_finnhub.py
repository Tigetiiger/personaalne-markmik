"""Finnhub adapter (ported from stock-oracle; respx routes became market_helpers.Router)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from market_helpers import FINNHUB, finnhub_routes, load, no_wait

from app.stocks.providers import FinnhubProvider, InstrumentRef, NotCovered, ProviderError, RateLimited

AAPL = InstrumentRef("AAPL", "USD")
START, END = date(2020, 8, 3), date(2020, 9, 4)


def finnhub(http):
    return FinnhubProvider("test-key", http, limiter=no_wait())


def midnight(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def test_candle_request_sends_key_in_header_not_url(router, http):
    finnhub_routes(router, free_tier=False)

    finnhub(http).daily_bars(AAPL, START, END)

    request = router.calls[-1]
    assert "token" not in request.url.params
    assert "test-key" not in str(request.url)
    assert request.url.params["resolution"] == "D"
    assert int(request.url.params["from"]) == midnight(START)
    assert int(request.url.params["to"]) == midnight(END) + 86399


def test_duplicate_and_out_of_range_candles_are_dropped(router, http):
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3), date(2024, 1, 9)]
    router.add(FINNHUB + "/stock/candle", httpx.Response(200, json={
        "s": "ok", "t": [midnight(d) for d in days], "o": [1, 2, 2.5, 4], "h": [1, 2, 2.5, 4],
        "l": [1, 2, 2.5, 4], "c": [1, 2, 2.5, 4], "v": [10, 20, 25, 40]}))

    bars = finnhub(http).daily_bars(AAPL, date(2024, 1, 1), date(2024, 1, 5))

    assert [(b.date, b.close) for b in bars] == [
        (date(2024, 1, 2), Decimal(1)),
        (date(2024, 1, 3), Decimal("2.5")),  # the later duplicate wins
    ]


def test_unexpected_candle_status_is_an_error(router, http):
    router.add(FINNHUB + "/stock/candle", httpx.Response(200, json={"s": "error"}))
    with pytest.raises(ProviderError, match="unexpected status"):
        finnhub(http).daily_bars(AAPL, START, END)


def test_free_tier_candles_are_not_covered(router, http):
    finnhub_routes(router, free_tier=True)
    with pytest.raises(NotCovered, match="not available on this plan"):
        finnhub(http).daily_bars(AAPL, START, END)


def test_no_access_as_401_is_also_not_covered(router, http):
    router.add(FINNHUB + "/stock/dividend", httpx.Response(401, json=load("finnhub/no_access.json")))
    with pytest.raises(NotCovered):
        finnhub(http).corporate_actions(AAPL, START, END)


def test_bad_key_is_a_provider_error_without_the_key(router, http):
    router.add(FINNHUB + "/quote", httpx.Response(401, json={"error": "Invalid API key"}))
    with pytest.raises(ProviderError, match="bad API key") as info:
        finnhub(http).quote(AAPL)
    assert not isinstance(info.value, NotCovered)
    assert "test-key" not in str(info.value)


def test_429_carries_retry_after(router, http):
    router.add(FINNHUB + "/quote", httpx.Response(429, headers={"Retry-After": "12"}))
    with pytest.raises(RateLimited) as info:
        finnhub(http).quote(AAPL)
    assert info.value.retry_after == 12.0
    assert info.value.source == "finnhub"


def test_429_without_usable_retry_after(router, http):
    router.add(FINNHUB + "/quote", httpx.Response(429, headers={"Retry-After": "soon"}))
    with pytest.raises(RateLimited) as info:
        finnhub(http).quote(AAPL)
    assert info.value.retry_after is None


def test_server_error_and_invalid_json(router, http):
    router.add(FINNHUB + "/quote", httpx.Response(502))
    with pytest.raises(ProviderError, match="HTTP 502"):
        finnhub(http).quote(AAPL)
    router.add(FINNHUB + "/quote", httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError, match="invalid JSON"):
        finnhub(http).quote(AAPL)


def test_quote_fields(router, http):
    finnhub_routes(router, free_tier=False)

    quote = finnhub(http).quote(AAPL)

    assert quote.price == Decimal("120.96")
    assert quote.previous_close == Decimal("120.88")
    assert quote.as_of == datetime(2020, 9, 4, 20, 0, tzinfo=UTC)


def test_provider_symbol_mapping(router, http):
    finnhub_routes(router, free_tier=False)
    ref = InstrumentRef("Apple", "USD", {"finnhub": "AAPL"})

    assert finnhub(http).quote(ref).symbol == "AAPL"

    not_on_finnhub = InstrumentRef("VWCE", "EUR", {"finnhub": None, "yahoo": "VWCE.DE"})
    calls = len(router.calls)
    with pytest.raises(NotCovered):
        finnhub(http).quote(not_on_finnhub)
    assert len(router.calls) == calls  # no request wasted


def test_split_ratio_and_dividend_currency_default(router, http):
    router.add(FINNHUB + "/stock/split", httpx.Response(200, json=[
        {"symbol": "X", "date": "2024-06-10", "fromFactor": 3, "toFactor": 2}]))
    router.add(FINNHUB + "/stock/dividend", httpx.Response(200, json=[
        {"symbol": "X", "date": "2024-06-01", "amount": 0.5, "currency": None}]))

    actions = finnhub(http).corporate_actions(InstrumentRef("X", "CAD"), date(2024, 1, 1), date(2024, 12, 31))

    assert actions[0].currency == "CAD"
    assert actions[1].ratio == Decimal(2) / Decimal(3)  # a 2-for-3 reverse split


def test_search_maps_fields(router, http):
    finnhub_routes(router, free_tier=False)

    first = finnhub(http).search("apple")[0]

    assert (first.symbol, first.name, first.kind, first.source) == ("AAPL", "APPLE INC", "Common Stock", "finnhub")


def test_default_limiter_is_60_per_minute(http):
    limiter = FinnhubProvider("k", http)._limiter
    assert (limiter.calls, limiter.period) == (60, 60.0)
