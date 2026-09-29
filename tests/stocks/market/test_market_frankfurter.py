"""Frankfurter (ECB) adapter, on the recorded fixtures (ported from stock-oracle)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from market_helpers import load, no_wait

from app.stocks.providers import FrankfurterFX, FxPoint, NotCovered, ProviderError, RateLimited, rate_on
from app.stocks.providers.frankfurter import BASE_URL

RATES = BASE_URL + "/rates"


def fx(http):
    return FrankfurterFX(http, limiter=no_wait())


def test_weekend_returns_friday_rate(router, http):
    router.add(RATES, httpx.Response(200, json=load("frankfurter/rates_weekend_2024-01-06_USD_EUR.json")))

    point = fx(http).rate(date(2024, 1, 6), "USD", "EUR")  # a Saturday

    assert point == FxPoint(date(2024, 1, 5), "USD", "EUR", Decimal("0.91567"))
    assert dict(router.calls[-1].url.params) == {"date": "2024-01-06", "base": "USD", "quotes": "EUR", "providers": "ECB"}


def test_target_holiday_returns_last_business_day(router, http):
    router.add(RATES, httpx.Response(200, json=load("frankfurter/rates_holiday_2024-12-26_EUR_USD.json")))

    point = fx(http).rate(date(2024, 12, 26), "EUR", "USD")  # St Stephen's Day

    assert point.date == date(2024, 12, 24)
    assert point.rate == Decimal("1.0395")


def test_same_currency_needs_no_request(router, http):
    assert fx(http).rate(date(2024, 1, 6), "EUR", "EUR").rate == Decimal(1)
    assert not router.calls


def test_range_and_lookup_across_holidays(router, http):
    router.add(RATES, httpx.Response(200, json=load("frankfurter/rates_range_2024-12-21_2025-01-03_USD_EUR.json")))

    points = fx(http).rates(date(2024, 12, 21), date(2025, 1, 3), "USD", "EUR")

    assert points[0].date == date(2024, 12, 20)  # starts at the business day before a Saturday
    assert [p.date for p in points] == sorted(p.date for p in points)
    assert rate_on(points, date(2024, 12, 26)).date == date(2024, 12, 24)
    assert rate_on(points, date(2025, 1, 1)).date == date(2024, 12, 31)
    assert rate_on(points, date(2025, 1, 2)).rate == Decimal("0.9689")
    with pytest.raises(NotCovered):
        rate_on(points, date(2024, 12, 19))


def test_date_before_ecb_history_is_not_covered(router, http):
    router.add(RATES, httpx.Response(200, json=[]))  # as recorded for 1990-01-05
    with pytest.raises(NotCovered):
        fx(http).rate(date(1990, 1, 5), "USD", "EUR")


def test_unknown_currency(router, http):
    router.add(RATES, httpx.Response(422, json={"status": 422, "message": "invalid currency: XXX"}))
    with pytest.raises(NotCovered, match="invalid currency: XXX"):
        fx(http).rate(date(2024, 1, 5), "XXX", "EUR")


def test_422_without_json_body(router, http):
    router.add(RATES, httpx.Response(422, text="nope"))
    with pytest.raises(NotCovered, match="HTTP 422"):
        fx(http).rate(date(2024, 1, 5), "XXX", "EUR")


@pytest.mark.parametrize(("response", "error", "match"), [
    (httpx.Response(429), RateLimited, "rate limited"),
    (httpx.Response(522, text="error code: 522"), ProviderError, "HTTP 522"),
    (httpx.Response(200, text="not json"), ProviderError, "unexpected response"),
    (httpx.Response(200, json=[{"date": "2024-01-05"}]), ProviderError, "unexpected"),
])
def test_failures(router, http, response, error, match):
    router.add(RATES, response)
    with pytest.raises(error, match=match):
        fx(http).rates(date(2024, 1, 1), date(2024, 1, 5), "USD", "EUR")


def test_transport_error(router, http):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    router.add(RATES, slow)
    with pytest.raises(ProviderError, match="ReadTimeout"):
        fx(http).rates(date(2024, 1, 1), date(2024, 1, 5), "USD", "EUR")
