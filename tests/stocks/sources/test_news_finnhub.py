"""Finnhub company news and earnings calendar (ported from stock-oracle's test_finnhub.py)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from news_helpers import NOW

from app.stocks.sources.base import RateLimiter, SourceError
from app.stocks.sources.finnhub import BASE_URL, FinnhubSource
from app.stocks.sources.models import InstrumentRef

NVDA = InstrumentRef(ticker="NVDA", name="NVIDIA", exchange="NMS", currency="USD")
SINCE = datetime(2026, 9, 1, tzinfo=UTC)


def source(client, clock):
    return FinnhubSource(client, api_key="test-key", limiter=RateLimiter(1.0, clock=clock, sleep=clock.sleep), sleep=clock.sleep)


def test_company_news(http, hclient, clock):
    http.bytes(f"{BASE_URL}/company-news", "finnhub/company_news.json")
    docs = source(hclient, clock).fetch(NVDA, SINCE, NOW)

    request = http.calls[-1]
    assert request.url.params["symbol"] == "NVDA"
    assert request.url.params["from"] == "2026-09-01" and request.url.params["to"] == "2026-09-21"
    assert request.headers["X-Finnhub-Token"] == "test-key"
    assert "token" not in request.url.params and "test-key" not in str(request.url)

    # Undated, too old, and empty items are dropped.
    assert [d.external_id for d in docs] == ["136001", "136002"]
    first, second = docs
    assert first.published_at == datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    assert first.body == "Nvidia on Sunday unveiled its next-generation chips."
    assert first.meta["publisher"] == "Reuters"
    assert second.instruments == ("NVDA", "INTC")


def test_finnhub_symbol_and_coverage():
    ref = InstrumentRef(ticker="BRK-B", name="Berkshire", exchange="NYQ", currency="USD", symbols={"finnhub": "BRK.B"})
    assert ref.symbol_at("finnhub") == "BRK.B"
    finnhub = FinnhubSource(httpx.Client(), api_key="k")
    assert finnhub.covers(ref) and finnhub.covers(NVDA)
    assert not finnhub.covers(InstrumentRef(ticker="VWCE.DE", name="Vanguard", exchange="GER", currency="EUR"))
    assert not finnhub.covers(InstrumentRef(ticker="X", name="X", currency="USD", symbols={"finnhub": None}))


@pytest.mark.parametrize("payload", [{"error": "bad"}, "not json at all"])
def test_unexpected_news_payload_raises(http, hclient, clock, payload):
    http.on(f"{BASE_URL}/company-news", httpx.Response(200, text=payload) if isinstance(payload, str) else
            httpx.Response(200, json=payload))
    with pytest.raises(SourceError):
        source(hclient, clock).fetch(NVDA, SINCE, NOW)


def test_earnings_calendar(http, hclient, clock):
    http.bytes(f"{BASE_URL}/calendar/earnings", "finnhub/earnings_calendar.json")
    past, upcoming = source(hclient, clock).earnings_calendar(NVDA, date(2026, 6, 1), date(2026, 12, 31))
    assert http.calls[-1].url.params["from"] == "2026-06-01"
    assert past.day == date(2026, 8, 27) and past.time_of_day == "amc" and past.fiscal_period == "2027Q2"
    assert past.eps_actual == Decimal("1.05") and past.revenue_actual == Decimal("52100000000")
    assert upcoming.eps_actual is None and upcoming.time_of_day is None and upcoming.source == "finnhub"


def test_rate_limit_then_success(http, hclient, clock):
    http.on(f"{BASE_URL}/company-news", httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json=[]))
    assert source(hclient, clock).fetch(NVDA, SINCE, NOW) == []
    assert 3.0 in clock.sleeps
