"""Alpha Vantage news sentiment behind its daily budget (ported from stock-oracle's
test_second_opinion.py)."""

from datetime import UTC, date, datetime

import httpx
import pytest
from news_helpers import NOW

from app.stocks.sources.alphavantage import QUERY_URL, AlphaVantageSource, parse_time_published
from app.stocks.sources.base import BudgetExhausted, DailyBudget, RateLimiter, SourceError
from app.stocks.sources.models import InstrumentRef

NVDA = InstrumentRef(ticker="NVDA", name="NVIDIA")
SINCE = datetime(2026, 9, 14, tzinfo=UTC)
TODAY = date(2026, 9, 21)


def alphavantage(client, clock, budget):
    return AlphaVantageSource(client, api_key="av-key", budget=budget,
                              limiter=RateLimiter(1.5, clock=clock, sleep=clock.sleep), sleep=clock.sleep)


def test_alphavantage_news(http, hclient, clock):
    http.bytes(QUERY_URL, "alphavantage/news_sentiment.json")
    budget = DailyBudget(25, today=lambda: TODAY)
    docs = alphavantage(hclient, clock, budget).fetch(NVDA, SINCE, NOW)

    params = http.calls[-1].url.params
    assert params["function"] == "NEWS_SENTIMENT" and params["tickers"] == "NVDA" and params["time_from"] == "20260914T0000"
    assert budget.used == 1

    yahoo, minute = docs  # the unparseable timestamp is dropped
    assert yahoo.external_id == "https://finance.yahoo.com/news/nvidia-intel-partner-123.html"
    assert yahoo.published_at == datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert yahoo.author == "Jane Doe"
    assert yahoo.meta["sentiment"] == pytest.approx(0.305331) and yahoo.meta["relevance"] == pytest.approx(0.91)
    assert yahoo.instruments == ("NVDA", "INTC")
    assert minute.published_at == datetime(2026, 9, 21, 7, 30, tzinfo=UTC)
    assert minute.meta["overall_sentiment"] is None and minute.author is None


def test_budget_stops_requests(http, hclient, clock):
    http.bytes(QUERY_URL, "alphavantage/news_sentiment.json")
    source = alphavantage(hclient, clock, DailyBudget(1, today=lambda: TODAY))
    source.fetch(NVDA, SINCE, NOW)
    with pytest.raises(BudgetExhausted):
        source.fetch(NVDA, SINCE, NOW)
    assert len(http.calls) == 1


@pytest.mark.parametrize("key", ["Information", "Note"])
def test_rate_limit_message_is_budget_exhaustion(http, hclient, clock, key):
    http.json(QUERY_URL, {key: "Thank you for using Alpha Vantage! rate limit"})
    with pytest.raises(BudgetExhausted, match="rate limit"):
        alphavantage(hclient, clock, DailyBudget(25, today=lambda: TODAY)).fetch(NVDA, SINCE, NOW)


@pytest.mark.parametrize(("payload", "match"), [({"Error Message": "Invalid API call"}, "Invalid API call"),
                                                ({}, "no feed"), ([], "unexpected")])
def test_errors(http, hclient, clock, payload, match):
    http.json(QUERY_URL, payload)
    with pytest.raises(SourceError, match=match):
        alphavantage(hclient, clock, DailyBudget(25, today=lambda: TODAY)).fetch(NVDA, SINCE, NOW)


def test_non_json_answer(http, hclient, clock):
    http.on(QUERY_URL, httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(SourceError, match="non-JSON"):
        alphavantage(hclient, clock, DailyBudget(25, today=lambda: TODAY)).fetch(NVDA, SINCE, NOW)


def test_parse_time_published():
    assert parse_time_published("20260921T044609") == datetime(2026, 9, 21, 4, 46, 9, tzinfo=UTC)
    assert parse_time_published("20260921T0730") == datetime(2026, 9, 21, 7, 30, tzinfo=UTC)
    assert parse_time_published(None) is None and parse_time_published("yesterday") is None
