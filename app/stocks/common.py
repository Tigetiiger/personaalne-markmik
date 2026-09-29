"""Helpers every part of the stocks package shares: Decimal columns, JSON settings, instrument
lookups and the run claims that keep one night or one on-demand AI run going at a time."""

import json
from datetime import timedelta
from decimal import Decimal

from ..db import tx
from ..util import now_iso, now_utc, parse_iso


def dec(value):
    """A TEXT money/quantity column (or any number) as Decimal; None stays None."""
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def dtext(value):
    """Decimal (or int, or numeric text) -> the TEXT stored in the database; None stays None.
    Never float: a float carries binary noise into money columns."""
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("float given for a Decimal column")
    d = dec(value)
    if not d.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return str(d)


def get_json(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_json(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))


def instrument(conn, instrument_id):
    return conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()


def by_symbol(conn, symbol):
    """The instrument with this symbol; the followed one first when two exchanges share it."""
    return conn.execute(
        "SELECT i.* FROM instruments i LEFT JOIN watchlist w ON w.instrument_id = i.id "
        "WHERE i.symbol = ? ORDER BY w.instrument_id IS NULL, i.id LIMIT 1", (symbol.upper(),)).fetchone()


def followed(conn, *, kinds=("stock", "etf")):
    """Watchlist instruments (holdings are on it too: a buy adds one), by symbol."""
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT i.* FROM instruments i JOIN watchlist w ON w.instrument_id = i.id "
        f"WHERE i.kind IN ({marks}) ORDER BY i.symbol", kinds).fetchall()


def provider_symbol(row, provider):
    """The instrument's symbol at a provider, or None when that provider does not cover it.
    Without an entry the plain symbol is tried."""
    symbols = json.loads(row["provider_symbols_json"] or "{}")
    return symbols[provider] if provider in symbols else row["symbol"]


# --- Claims: one run of a kind at a time, across the web and worker processes ----------------------

def claim(conn, name, stale=timedelta(minutes=30)):
    """Take the claim `stocks:running:<name>`. False when another run holds it and it is not
    older than `stale` (a process that died never releases it)."""
    key = f"stocks:running:{name}"
    with tx(conn):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is not None and parse_iso(row["value"]) > now_utc() - stale:
            return False
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (key, now_iso()))
    return True


def claimed(conn, name, stale=timedelta(minutes=30)):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"stocks:running:{name}",)).fetchone()
    return row is not None and parse_iso(row["value"]) > now_utc() - stale


def release(conn, name):
    conn.execute("DELETE FROM settings WHERE key = ?", (f"stocks:running:{name}",))
