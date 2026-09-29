"""Helpers shared by the portfolio tests."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.util import TALLINN as TZ


def D(value: str | int) -> Decimal:
    return Decimal(value)


def at(day: str, time: str = "12:00") -> datetime:
    """A Tallinn wall-clock timestamp, e.g. at("2026-01-05")."""
    return datetime.fromisoformat(f"{day} {time}").replace(tzinfo=TZ)


def d(day: str) -> date:
    return date.fromisoformat(day)


class Fx:
    """ECB-style table: EUR per unit of foreign currency, by date. A lookup uses
    the latest rate on or before the date, like weekend ECB lookups."""

    def __init__(self, table: dict[str, dict[str, str]]) -> None:
        # {"USD": {"2026-01-05": "0.90", ...}}
        self.table = {
            ccy: sorted((d(day), Decimal(rate)) for day, rate in rates.items())
            for ccy, rates in table.items()
        }
        self.calls: list[tuple[date, str, str]] = []

    def eur_per(self, ccy: str, on: date) -> Decimal:
        if ccy == "EUR":
            return Decimal(1)
        rates = [rate for day, rate in self.table[ccy] if day <= on]
        if not rates:
            raise LookupError(f"no {ccy} rate on or before {on}")
        return rates[-1]

    def __call__(self, on: date, from_ccy: str, to_ccy: str) -> Decimal:
        self.calls.append((on, from_ccy, to_ccy))
        return self.eur_per(from_ccy, on) / self.eur_per(to_ccy, on)


def no_fx(on: date, from_ccy: str, to_ccy: str) -> Decimal:
    raise AssertionError(f"unexpected FX lookup {on} {from_ccy}->{to_ccy}")


# --- Database builders (Märkmik): the market package's tables are filled directly -----------------

TODAY = date(2026, 9, 21)


def add_instrument(conn, symbol, *, kind="stock", currency="USD", exchange="US", watch=True, name=None, isin=None):
    iid = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind, isin) VALUES (?, ?, ?, ?, ?, ?)",
                       (symbol, exchange, currency, name or f"{symbol} Inc", kind, isin)).lastrowid
    if watch:
        conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (iid,))
    return iid


def add_bars(conn, instrument_id, closes, end=None, source="yahoo"):
    """One bar per calendar day ending at `end` (default the day before TODAY), oldest first."""
    end = end or TODAY - timedelta(days=1)
    start = end - timedelta(days=len(closes) - 1)
    for n, close in enumerate(closes):
        conn.execute("INSERT INTO price_bars (instrument_id, date, close, adj_close, source) VALUES (?, ?, ?, ?, ?)",
                     (instrument_id, (start + timedelta(days=n)).isoformat(), str(close), str(close), source))


def add_fx(conn, day, quote, rate):
    conn.execute("INSERT INTO fx_rates (date, base, quote, rate) VALUES (?, 'EUR', ?, ?)", (day.isoformat(), quote, rate))


def add_account(conn, name="LHV", kind="investment_account"):
    return conn.execute("INSERT INTO broker_accounts (name, broker, kind) VALUES (?, 'LHV', ?)", (name, kind)).lastrowid


def add_tx(conn, account_id, kind, day, *, instrument=None, currency="EUR", **fields):
    """A transaction at 10:00 UTC on `day`; Decimal fields as keyword arguments."""
    cols = {"account_id": account_id, "instrument_id": instrument, "kind": kind,
            "ts": f"{day.isoformat()}T10:00:00Z", "currency": currency,
            **{k: str(v) for k, v in fields.items()}}
    return conn.execute(f"INSERT INTO portfolio_tx ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        list(cols.values())).lastrowid
