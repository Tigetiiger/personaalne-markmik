"""Fixtures for the stocks-sources tests; the helpers behind them are in news_helpers.py."""

import json

import pytest
from news_helpers import FakeClock, MockHTTP


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def http(clock):
    return MockHTTP(clock)


@pytest.fixture
def hclient(http):
    with http.client() as c:
        yield c


@pytest.fixture
def follow(conn):
    """follow("NVDA", "NVIDIA", exchange="NMS", currency="USD", kind="stock", symbols={...}) -> id"""
    def add(symbol, name, *, exchange="NMS", currency="USD", kind="stock", symbols=None):
        iid = conn.execute(
            "INSERT INTO instruments (symbol, exchange, currency, name, kind, provider_symbols_json) VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, exchange, currency, name, kind, json.dumps(symbols or {}))).lastrowid
        conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (iid,))
        return iid
    return add
