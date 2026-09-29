"""Fixtures for the stocks-night tests; the fakes behind them are in night_helpers.py. No network:
the market and sources steps are replaced unless a test says otherwise, and ntfy is a list."""

import json
import sys
from datetime import datetime, timezone

import pytest
from night_helpers import FakeClock

from app import reminders, vault
from app.stocks import market, night, sources


@pytest.fixture
def follow(conn):
    """follow("NVDA", "NVIDIA", currency="USD", kind="stock", tags=[...], watch=True) -> id"""
    def add(symbol, name=None, *, currency="USD", kind="stock", tags=(), watch=True):
        iid = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, 'NMS', ?, ?, ?)",
                           (symbol, currency, name or f"{symbol} Inc.", kind)).lastrowid
        if watch:
            conn.execute("INSERT INTO watchlist (instrument_id, tags_json) VALUES (?, ?)", (iid, json.dumps(list(tags))))
        return iid
    return add


@pytest.fixture
def hold(conn):
    """hold(instrument_id, qty="10"): a buy a month before the tests' dates."""
    def add(instrument_id, qty="10"):
        row = conn.execute("SELECT id FROM broker_accounts WHERE name = 'Test'").fetchone()
        account = row["id"] if row else conn.execute(
            "INSERT INTO broker_accounts (name, broker, kind) VALUES ('Test', 'test', 'brokerage')").lastrowid
        conn.execute("INSERT INTO portfolio_tx (account_id, instrument_id, kind, ts, qty, price, currency) "
                     "VALUES (?, ?, 'buy', '2026-08-01T12:00:00Z', ?, '100', 'USD')", (account, instrument_id, qty))
    return add


@pytest.fixture
def bars(conn):
    """bars(instrument_id, [("2026-09-28", "100"), ...])"""
    def add(instrument_id, rows):
        conn.executemany("INSERT INTO price_bars (instrument_id, date, close, source) VALUES (?, ?, ?, 'yahoo')",
                         [(instrument_id, d, c) for d, c in rows])
    return add


@pytest.fixture
def sent(conn, monkeypatch):
    """ntfy set up in the vault; every publish lands in the returned list as (cfg, title, message)."""
    vault.set_json(conn, "ntfy", {"url": "https://ntfy.example.ee", "topic": "meeldetuletused", "token": "tk"})
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    out = []
    monkeypatch.setattr(reminders, "publish", lambda cfg, title, message: out.append((cfg, title, message)))
    return out


@pytest.fixture
def quiet_data(monkeypatch):
    """The night's data steps without the network: what each was called with is recorded."""
    calls = []
    monkeypatch.setattr(market, "refresh_prices", lambda conn, today: calls.append(("prices", today)) or
                        {"refreshed": {}, "failed": {}, "fx": {"rates": {}, "failed": {}}})
    monkeypatch.setattr(sources, "ingest", lambda conn, now, run_id=None: calls.append(("ingest", now)) or
                        {"edgar": {"status": "succeeded", "fetched": 0, "new": 0, "error": None}})
    return calls


@pytest.fixture
def ai(monkeypatch):
    """ai(fake): install a night_helpers.FakeAI as the AI package's modules."""
    def install(fake):
        research, verdict = fake.modules()
        monkeypatch.setitem(sys.modules, "app.stocks.research", research)
        monkeypatch.setitem(sys.modules, "app.stocks.verdict", verdict)
        return fake
    return install


@pytest.fixture
def no_ai(monkeypatch):
    """The AI package not there yet (None in sys.modules makes its import fail as if missing)."""
    monkeypatch.setitem(sys.modules, "app.stocks.research", None)
    monkeypatch.setitem(sys.modules, "app.stocks.verdict", None)


@pytest.fixture
def clock(monkeypatch):
    """A night clock; night._sleep moves it instead of sleeping."""
    c = FakeClock(datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc))  # 01:30 on 30.09 in Tallinn
    monkeypatch.setattr(night, "_sleep", c.sleep)
    return c
