"""Yahoo's chart and search JSON, from the recordings (market_helpers.py says what each is)."""

import copy
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from market_helpers import load, no_wait, yahoo_routes

from app.stocks.providers import InstrumentRef, NotCovered, ProviderError, RateLimited, YahooProvider
from app.stocks.providers.yahoo import CHART_URL

VWCE = InstrumentRef("VWCE.DE", "EUR", {"finnhub": None})
NVO = InstrumentRef("NVO", "USD")


def yahoo(http):
    return YahooProvider(http, limiter=no_wait())


def test_ucits_etf_bars_skip_xetra_holidays(router, http):
    yahoo_routes(router)

    bars = yahoo(http).daily_bars(VWCE, date(2024, 12, 20), date(2025, 1, 3))

    # 24-26 Dec and 31 Dec/1 Jan are XETRA holidays; weekends are absent too.
    assert [b.date for b in bars] == [date(2024, 12, 20), date(2024, 12, 23), date(2024, 12, 27),
                                      date(2024, 12, 30), date(2025, 1, 2), date(2025, 1, 3)]
    assert all(b.source == "yahoo" and b.adj_close is not None for b in bars)


def test_bar_date_is_the_session_day_in_the_exchange_zone(router, http):
    # AAPL's first recorded bar opens 2020-08-03 09:30 New York = 13:30 UTC; VWCE.DE 09:00 Berlin.
    yahoo_routes(router)
    assert yahoo(http).daily_bars(InstrumentRef("AAPL", "USD"), date(2020, 8, 3), date(2020, 8, 3))[0].date == date(2020, 8, 3)


def test_bars_with_null_values_are_skipped(router, http):
    body = load("yahoo/chart_vwce_de_2024-12.json")
    q = body["chart"]["result"][0]["indicators"]["quote"][0]
    q["close"][1] = None  # as Yahoo has for 3 VWCE.DE days in 5 years
    q["open"][2] = q["high"][2] = q["low"][2] = q["close"][2] = q["volume"][2] = None
    q["volume"][3] = None
    router.add(CHART_URL, httpx.Response(200, json=body))

    bars = yahoo(http).daily_bars(VWCE, date(2024, 12, 16), date(2024, 12, 20))

    assert [b.date for b in bars] == [date(2024, 12, 16), date(2024, 12, 19), date(2024, 12, 20)]
    assert bars[1].volume is None


def test_float32_noise_is_removed(router, http):
    yahoo_routes(router)
    bar = yahoo(http).daily_bars(InstrumentRef("AAPL", "USD"), date(2020, 8, 4), date(2020, 8, 4))[0]
    assert bar.close == Decimal("109.665")  # 109.66500091552734 in the JSON


def test_duplicate_bars_keep_the_last(router, http):
    body = load("yahoo/chart_aapl_2020-08.json")
    result = body["chart"]["result"][0]
    result["timestamp"].insert(1, result["timestamp"][0] + 60)  # a second bar for 2020-08-03
    for series in (result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]):
        for key, values in series.items():
            values.insert(1, 111.0 if key != "volume" else 5)
    router.add(CHART_URL, httpx.Response(200, json=body))

    bars = yahoo(http).daily_bars(InstrumentRef("AAPL", "USD"), date(2020, 8, 3), date(2020, 8, 4))

    assert [(b.date, b.close) for b in bars] == [(date(2020, 8, 3), Decimal(111)), (date(2020, 8, 4), Decimal("109.665"))]


def test_dividend_is_unadjusted_by_later_splits(router, http):
    yahoo_routes(router)

    actions = yahoo(http).corporate_actions(NVO, date(2023, 8, 1), date(2023, 9, 29))

    # Yahoo reports 0.442 (divided by the 2:1 split of 2023-09-20); paid per share was 0.884.
    assert [(a.date, a.kind, a.amount, a.ratio) for a in actions] == [
        (date(2023, 8, 18), "dividend", Decimal("0.884"), None),
        (date(2023, 9, 20), "split", None, Decimal(2)),
    ]


def test_bars_and_actions_share_one_request(router, http):
    yahoo_routes(router)
    provider = yahoo(http)
    provider.corporate_actions(NVO, date(2023, 8, 1), date(2023, 9, 29))
    provider.daily_bars(NVO, date(2023, 8, 1), date(2023, 9, 29))
    assert len(router.calls) == 1
    provider.daily_bars(NVO, date(2023, 8, 2), date(2023, 9, 29))
    assert len(router.calls) == 2


def test_weekend_answer_has_no_bars_and_no_actions(router, http):
    router.add(CHART_URL, httpx.Response(200, json=load("yahoo/chart_aapl_weekend.json")))
    provider = yahoo(http)
    ref = InstrumentRef("AAPL", "USD")
    assert provider.daily_bars(ref, date(2020, 8, 8), date(2020, 8, 9)) == []
    assert provider.corporate_actions(ref, date(2020, 8, 8), date(2020, 8, 9)) == []


def test_quote_uses_the_last_bar_before_the_market_day(router, http):
    yahoo_routes(router)

    quote = yahoo(http).quote(InstrumentRef("AAPL", "USD"))

    assert quote.price == Decimal("338.4")
    assert quote.as_of == datetime(2026, 9, 28, 20, 0, 1, tzinfo=UTC)
    assert quote.previous_close == Decimal("341.07")  # the close of Friday 2026-09-25


def test_quote_without_an_earlier_bar_uses_chart_previous_close(router, http):
    body = load("yahoo/chart_aapl_5d.json")
    result = body["chart"]["result"][0]
    result["timestamp"] = result["timestamp"][-1:]
    for key, values in result["indicators"]["quote"][0].items():
        result["indicators"]["quote"][0][key] = values[-1:]
    router.add(CHART_URL, httpx.Response(200, json=body))

    assert yahoo(http).quote(InstrumentRef("AAPL", "USD")).previous_close == Decimal("338.98")  # meta.chartPreviousClose


def test_quote_without_a_price_is_not_covered(router, http):
    body = load("yahoo/chart_aapl_5d.json")
    del body["chart"]["result"][0]["meta"]["regularMarketPrice"]
    router.add(CHART_URL, httpx.Response(200, json=body))
    with pytest.raises(NotCovered):
        yahoo(http).quote(InstrumentRef("AAPL", "USD"))


def test_intraday_bars(router, http):
    yahoo_routes(router)

    bars = yahoo(http).intraday_bars(InstrumentRef("AAPL", "USD"), "5m", "1d")

    assert len(bars) == 79
    assert bars[0].start == datetime(2026, 9, 28, 13, 30, tzinfo=UTC)
    assert all(b.low <= b.close <= b.high for b in bars)
    params = router.calls[-1].url.params
    assert (params["range"], params["interval"], params["includePrePost"]) == ("1d", "5m", "false")


def test_describe_reads_the_meta(router, http):
    yahoo_routes(router)
    assert yahoo(http).describe("VWCE.DE") == {"name": "Vanguard FTSE All-World UCITS ETF USD Accumulation", "currency": "EUR",
                                                "exchange": "GER", "type": "ETF"}


def test_search_maps_fields(router, http):
    yahoo_routes(router)

    matches = yahoo(http).search("vwce")

    assert (matches[0].symbol, matches[0].kind, matches[0].exchange, matches[0].source) == ("VWCE.DE", "ETF", "XETRA", "yahoo")
    assert len(matches) == 7


def test_unknown_symbol_is_not_covered(router, http):
    yahoo_routes(router)
    with pytest.raises(NotCovered, match="No data found"):
        yahoo(http).daily_bars(InstrumentRef("NOPE", "USD"), date(2020, 8, 3), date(2020, 8, 4))


def test_unexpected_shapes_are_provider_errors(router, http):
    route_body = {"chart": {"result": [{"meta": {}, "timestamp": [1], "indicators": {}}]}}
    router.add(CHART_URL, httpx.Response(200, json=route_body))
    with pytest.raises(ProviderError, match="unexpected bars"):
        yahoo(http).daily_bars(InstrumentRef("X", "USD"), date(2020, 1, 1), date(2020, 1, 2))
    router.add(CHART_URL, httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError, match="invalid JSON"):
        yahoo(http).quote(InstrumentRef("X", "USD"))
    router.add(CHART_URL, httpx.Response(502))
    with pytest.raises(ProviderError, match="HTTP 502"):
        yahoo(http).quote(InstrumentRef("X", "USD"))


def test_429_is_rate_limited(router, http):
    router.add(CHART_URL, httpx.Response(429, text="Too Many Requests"))
    with pytest.raises(RateLimited):
        yahoo(http).quote(InstrumentRef("AAPL", "USD"))


def test_symbol_is_escaped_in_the_path(router, http):
    yahoo_routes(router)
    with pytest.raises(NotCovered):
        yahoo(http).quote(InstrumentRef("A/B", "USD"))
    assert router.calls[-1].url.raw_path.startswith(b"/v8/finance/chart/A%2FB?")


def test_default_limiter_is_one_call_per_2_seconds(http):
    limiter = YahooProvider(http)._limiter
    assert (limiter.calls, limiter.period) == (1, 2.0)


def test_recordings_are_unchanged_by_the_window_helper():
    body = load("yahoo/chart_aapl_2020-08.json")
    assert copy.deepcopy(body) == load("yahoo/chart_aapl_2020-08.json")
