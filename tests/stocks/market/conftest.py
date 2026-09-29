"""Fixtures of the market package's tests; the doubles themselves are in market_helpers.py."""

import httpx
import pytest
from market_helpers import Router, no_wait

from app.stocks import market
from app.stocks.providers.fake import FakeMarket


@pytest.fixture
def router():
    return Router()


@pytest.fixture
def http(router):
    with httpx.Client(timeout=5, transport=httpx.MockTransport(router)) as client:
        yield client


@pytest.fixture
def fake(monkeypatch):
    """market.py's providers talk to a FakeMarket, without pacing."""
    f = FakeMarket()
    monkeypatch.setattr(market, "TRANSPORT", f.transport)
    monkeypatch.setattr(market, "LIMITS", {"yahoo": no_wait(), "finnhub": no_wait(), "frankfurter": no_wait()})
    return f
