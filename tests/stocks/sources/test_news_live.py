"""One real call per source: run with `uv run pytest -m live tests/stocks/sources`. Keys come from the
environment (SEC_USER_AGENT, FINNHUB_KEY, ALPHAVANTAGE_KEY, IR_FEED_URL); a test without its key is
skipped. Alpha Vantage spends one of the day's 25 calls."""

import os
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.stocks.sources.alphavantage import AlphaVantageSource
from app.stocks.sources.base import DailyBudget
from app.stocks.sources.edgar import EdgarSource
from app.stocks.sources.finnhub import FinnhubSource
from app.stocks.sources.models import InstrumentRef
from app.stocks.sources.rss import google_news_rss, ir_rss, yahoo_rss

pytestmark = pytest.mark.live
NVDA = InstrumentRef(ticker="NVDA", name="NVIDIA", exchange="NMS", currency="USD")
NOW = datetime.now(UTC)
SINCE = NOW - timedelta(days=30)


def env(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


@pytest.fixture
def web():
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        yield c


def test_edgar(web):
    docs = EdgarSource(web, user_agent=env("SEC_USER_AGENT")).fetch(NVDA, NOW - timedelta(days=90), NOW)
    assert docs and all(d.external_id and d.published_at <= NOW for d in docs)


def test_finnhub_news_and_earnings(web):
    finnhub = FinnhubSource(web, api_key=env("FINNHUB_KEY"))
    assert finnhub.fetch(NVDA, NOW - timedelta(days=3), NOW)
    assert finnhub.earnings_calendar(NVDA, date.today() - timedelta(days=100), date.today() + timedelta(days=100))


def test_yahoo_rss(web):
    assert yahoo_rss(web).fetch(NVDA, SINCE, NOW)


def test_google_news(web):
    assert google_news_rss(web).fetch(NVDA, SINCE, NOW)


def test_ir_feed(web):
    url = env("IR_FEED_URL")
    assert ir_rss(web, {"NVDA": [url]}).fetch(NVDA, NOW - timedelta(days=365), NOW)


def test_alphavantage(web):
    source = AlphaVantageSource(web, api_key=env("ALPHAVANTAGE_KEY"), budget=DailyBudget(1, today=date.today))
    assert source.fetch(NVDA, NOW - timedelta(days=7), NOW)
