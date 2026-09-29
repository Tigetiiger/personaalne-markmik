"""Market data in the database: adding symbols, the 5-year backfill, the night's price refresh
(bars, corporate actions, SPY, ECB FX), the daytime quote cache, and the read side every other
stocks package uses (bars, latest price, FX, price stats, the trend card). Rewritten from
stock-oracle's providers/sync.py, providers/store.py, pipeline/market.py and web/market.py on
sqlite3 (docs/CONTRACTS.md, "Aktsiad": stocks-market).

When several sources stored the same day (stock-oracle's imported "yfinance" bars next to our
"yahoo" ones), the first source in alphabetical order wins, so the numbers are stable."""

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from ..db import connect, tx
from ..util import iso, local_today
from . import config, listing, trend
from .common import by_symbol, claim, claimed, dec, dtext, followed, instrument, release
from .providers import (
    SPLIT,
    FallbackProvider,
    FinnhubProvider,
    FrankfurterFX,
    InstrumentRef,
    NotCovered,
    ProviderError,
    RateLimiter,
    YahooProvider,
)

log = logging.getLogger(__name__)

BENCHMARK = "SPY"
BASE_CURRENCY = "EUR"
HISTORY_YEARS = 5
# Re-fetch a few days already stored: providers revise the latest bars, and the ECB occasionally
# publishes late.
REFRESH_OVERLAP_DAYS = 7
FX_OVERLAP_DAYS = 7
NEW_YORK = ZoneInfo("America/New_York")
US_OPEN = time(9, 30)
US_CLOSE = time(16, 0)
CURRENCY = re.compile(r"[A-Z]{3}")
KINDS = ("stock", "etf")

# Tests (and the browser tests' fake Yahoo) put an httpx.MockTransport here; None = the network.
TRANSPORT = None
# One limiter per provider for the whole process: a backfill thread and a page request together
# still keep Yahoo's one call per 2 s, Finnhub's 60 a minute and Frankfurter's 10 a second.
LIMITS = {"yahoo": RateLimiter(1, 2.0), "finnhub": RateLimiter(60, 60.0), "frankfurter": RateLimiter(10, 1.0)}


@contextmanager
def _providers(conn):
    with httpx.Client(timeout=20, transport=TRANSPORT) as client:
        yahoo = YahooProvider(client, LIMITS["yahoo"])
        key = config.secret(conn, "finnhub_key")
        finnhub = FinnhubProvider(key, client, LIMITS["finnhub"]) if key else None
        yield SimpleNamespace(
            yahoo=yahoo,
            finnhub=finnhub,
            # Finnhub first for quotes (real-time US), Yahoo for the rest and when it fails.
            quotes=FallbackProvider(finnhub, yahoo) if finnhub else yahoo,
            fx=FrankfurterFX(client, LIMITS["frankfurter"]),
        )


def ref_for(row):
    return InstrumentRef(symbol=row["symbol"], currency=row["currency"],
                         provider_symbols=json.loads(row["provider_symbols_json"] or "{}"))


def years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


# --- Search and the watchlist --------------------------------------------------------------------

def search(conn, query):
    """[{symbol, name, exchange, currency, kind}]; currency and exchange guessed from the suffix.
    ProviderError when every source failed."""
    query = (query or "").strip()[:40]
    if not query:
        return []
    with _providers(conn) as p:
        matches = listing.search_symbols([p.finnhub, p.yahoo] if p.finnhub else [p.yahoo], query)
    return [{"symbol": x.symbol, "name": x.name, "exchange": x.exchange, "currency": x.currency, "kind": x.kind}
            for x in map(listing.guess_listing, matches)]


def _describe(conn, symbol):
    """Yahoo's meta for a symbol typed without a search result; {} when Yahoo cannot be asked now
    (the guesses from the suffix are used then)."""
    try:
        with _providers(conn) as p:
            return p.yahoo.describe(symbol)
    except NotCovered:
        raise ValueError(f"Sümbolit {symbol} ei leitud.") from None
    except ProviderError as exc:
        log.warning("describing %s failed: %s", symbol, exc)
        return {}


def add(conn, symbol, *, name=None, exchange=None, currency=None, kind=None):
    """The instrument (created from Yahoo's chart meta for what is not given) on the watchlist.
    Idempotent. ValueError with an Estonian message for bad input or an unknown symbol."""
    symbol = (symbol or "").strip().upper()
    if not config.SYMBOL.fullmatch(symbol):
        raise ValueError("Vigane sümbol.")
    if kind is not None and kind not in KINDS:
        raise ValueError("Liik peab olema aktsia või ETF.")
    currency = currency.strip().upper() if currency else None
    if currency is not None and not CURRENCY.fullmatch(currency):
        raise ValueError("Vigane valuuta.")
    exchange = exchange.strip()[:40] if exchange else None
    name = name.strip()[:200] if name else None

    def existing():
        if exchange:
            return conn.execute("SELECT * FROM instruments WHERE symbol = ? AND exchange = ?", (symbol, exchange)).fetchone()
        return by_symbol(conn, symbol)

    row = existing()
    if row is None and None in (name, exchange, currency, kind):
        info = _describe(conn, symbol)
        guessed_exchange, guessed_currency = listing.guess_exchange(symbol)
        name = name or info.get("name") or symbol
        exchange = exchange or guessed_exchange
        currency = currency or info.get("currency") or guessed_currency
        kind = kind or listing.guess_kind(info.get("type"))
    with tx(conn):
        row = existing()
        if row is None:
            new_id = conn.execute(
                "INSERT INTO instruments (symbol, exchange, currency, name, kind, provider_symbols_json) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, exchange, currency, name, kind, json.dumps(listing.provider_symbols_for(symbol, exchange)))).lastrowid
            row = instrument(conn, new_id)
        conn.execute("INSERT OR IGNORE INTO watchlist (instrument_id) VALUES (?)", (row["id"],))
    return row


def backfill_running(conn, instrument_id):
    return claimed(conn, f"backfill:{instrument_id}")


def start_backfill(db_path, instrument_id):
    """5 years of bars and actions on a thread of its own. False when one is already running for
    this instrument."""
    conn = connect(db_path)
    try:
        if not claim(conn, f"backfill:{instrument_id}"):
            return False
    finally:
        conn.close()
    threading.Thread(target=_backfill_thread, args=(db_path, instrument_id), daemon=True,
                     name=f"stocks-backfill-{instrument_id}").start()
    return True


def _backfill_thread(db_path, instrument_id):
    conn = connect(db_path)
    try:
        backfill(conn, instrument_id, local_today())
    except ProviderError as exc:
        # The night's refresh finds no bars and backfills again.
        log.warning("backfill of instrument %s failed: %s", instrument_id, exc)
    except Exception:
        log.exception("backfill of instrument %s failed", instrument_id)
    finally:
        release(conn, f"backfill:{instrument_id}")
        conn.close()


def backfill(conn, instrument_id, today):
    """5 years of daily bars and corporate actions up to `today`; returns the bars stored.
    Idempotent: stored rows are overwritten on their natural keys."""
    row = instrument(conn, instrument_id)
    if row is None:
        raise LookupError(f"no instrument {instrument_id}")
    with _providers(conn) as p:
        return _full(conn, row, p.yahoo, today)


# --- Writes --------------------------------------------------------------------------------------

def _full(conn, row, provider, today, *, replace=False):
    """The whole history. `replace`: a new split put every stored bar on the old scale, so they
    go (from every source) before the new ones are written."""
    start = years_before(today, HISTORY_YEARS)
    ref = ref_for(row)
    actions = provider.corporate_actions(ref, start, today)
    bars = provider.daily_bars(ref, start, today)
    with tx(conn):
        if replace and bars:
            conn.execute("DELETE FROM price_bars WHERE instrument_id = ?", (row["id"],))
        _store_bars(conn, row["id"], bars)
        _store_actions(conn, row["id"], actions)
    return len(bars)


def _store_bars(conn, instrument_id, bars):
    rows = {(b.date, b.source): b for b in bars}.values()
    conn.executemany(
        "INSERT INTO price_bars (instrument_id, date, open, high, low, close, adj_close, volume, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (instrument_id, date, source) DO UPDATE SET "
        "open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close, "
        "adj_close = excluded.adj_close, volume = excluded.volume, "
        "fetched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
        [(instrument_id, b.date.isoformat(), dtext(b.open), dtext(b.high), dtext(b.low), dtext(b.close),
          dtext(b.adj_close), b.volume, b.source) for b in rows])


def _store_actions(conn, instrument_id, actions):
    rows = {(a.date, a.kind, a.source): a for a in actions}.values()
    conn.executemany(
        "INSERT INTO corporate_actions (instrument_id, date, kind, ratio, amount, currency, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (instrument_id, date, kind, source) DO UPDATE SET "
        "ratio = excluded.ratio, amount = excluded.amount, currency = excluded.currency",
        [(instrument_id, a.date.isoformat(), a.kind, dtext(a.ratio), dtext(a.amount), a.currency, a.source) for a in rows])


def _refresh(conn, row, provider, today):
    """What is new since the last stored bar, with an overlap. A full backfill when nothing is
    stored yet, or when a split appears that is not stored: stored bars are split-adjusted as
    delivered, so every bar before the split is now on the wrong scale."""
    last = conn.execute("SELECT MAX(date) AS d FROM price_bars WHERE instrument_id = ?", (row["id"],)).fetchone()["d"]
    if last is None:
        return {"bars": _full(conn, row, provider, today), "backfill": True}
    start = min(date.fromisoformat(last), today) - timedelta(days=REFRESH_OVERLAP_DAYS)
    ref = ref_for(row)
    actions = provider.corporate_actions(ref, start, today)
    known = {r["date"] for r in conn.execute(
        "SELECT date FROM corporate_actions WHERE instrument_id = ? AND kind = 'split'", (row["id"],))}
    if any(a.kind == SPLIT and a.date.isoformat() not in known for a in actions):
        return {"bars": _full(conn, row, provider, today, replace=True), "backfill": True}
    bars = provider.daily_bars(ref, start, today)
    with tx(conn):
        _store_bars(conn, row["id"], bars)
        _store_actions(conn, row["id"], actions)
    return {"bars": len(bars), "backfill": False}


def ensure_benchmark(conn):
    """SPY must have bars: the vs-SPY pane and the trend card compare against it. Created on
    first use; not put on the watchlist."""
    row = conn.execute("SELECT * FROM instruments WHERE symbol = ? ORDER BY id LIMIT 1", (BENCHMARK,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, ?, ?, ?, ?)",
                     (BENCHMARK, "NYSE ARCA", "USD", "SPDR S&P 500 ETF Trust", "etf"))
        row = conn.execute("SELECT * FROM instruments WHERE symbol = ? ORDER BY id LIMIT 1", (BENCHMARK,)).fetchone()
    return row


def refresh_prices(conn, today):
    """The night's step "prices": bars and actions for SPY, the watchlist and anything ever
    traded, one instrument at a time (a failure costs only that one), then ECB FX."""
    ensure_benchmark(conn)
    rows = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ? OR id IN (SELECT instrument_id FROM watchlist) "
        "OR id IN (SELECT instrument_id FROM portfolio_tx WHERE instrument_id IS NOT NULL) ORDER BY symbol, id",
        (BENCHMARK,)).fetchall()
    done, failed = {}, {}
    with _providers(conn) as p:
        for row in rows:
            try:
                done[row["symbol"]] = _refresh(conn, row, p.yahoo, today)
            except ProviderError as exc:
                failed[row["symbol"]] = str(exc)[:500]
                log.warning("prices %s: %s", row["symbol"], exc)
        fx_result = _refresh_fx(conn, p.fx, today)
    return {"refreshed": done, "failed": failed, "fx": fx_result}


def _refresh_fx(conn, fx, today):
    """EUR-based rates for every currency an instrument is quoted in or a transaction used."""
    currencies = {r[0] for r in conn.execute("SELECT currency FROM instruments UNION SELECT currency FROM portfolio_tx")}
    done, failed = {}, {}
    for currency in sorted(c for c in currencies if c and CURRENCY.fullmatch(c) and c != BASE_CURRENCY):
        latest = conn.execute("SELECT MAX(date) AS d FROM fx_rates WHERE base = ? AND quote = ?",
                              (BASE_CURRENCY, currency)).fetchone()["d"]
        start = (date.fromisoformat(latest) - timedelta(days=FX_OVERLAP_DAYS) if latest
                 else years_before(today, HISTORY_YEARS))
        try:
            points = fx.rates(start, today, BASE_CURRENCY, currency)
        except ProviderError as exc:
            failed[currency] = str(exc)[:500]
            continue
        conn.executemany(
            "INSERT INTO fx_rates (date, base, quote, rate) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (date, base, quote) DO UPDATE SET rate = excluded.rate",
            [(pt.date.isoformat(), pt.base, pt.quote, dtext(pt.rate)) for pt in points])
        done[currency] = len(points)
    return {"rates": done, "failed": failed}


def us_market_open(now):
    """Regular US session, weekdays 09:30-16:00 New York (holidays not known).

    Evaluated in New York time so the weeks when US and EU daylight saving time differ need no
    special case."""
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and US_OPEN <= local.time() < US_CLOSE


def refresh_quotes(conn, now):
    """Daytime quotes for the watchlist into `quotes`; {} outside US market hours."""
    if not us_market_open(now):
        return {}
    done, failed = [], {}
    rows = followed(conn)
    if not rows:
        return {"quoted": done, "failed": failed}
    with _providers(conn) as p:
        for row in rows:
            try:
                q = p.quotes.quote(ref_for(row))
            except ProviderError as exc:
                failed[row["symbol"]] = str(exc)[:500]
                continue
            conn.execute(
                "INSERT INTO quotes (instrument_id, price, previous_close, as_of, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (instrument_id) DO UPDATE SET price = excluded.price, previous_close = excluded.previous_close, "
                "as_of = excluded.as_of, source = excluded.source, fetched_at = excluded.fetched_at",
                (row["id"], dtext(q.price), dtext(q.previous_close), iso(q.as_of), q.source, iso(now)))
            done.append(row["symbol"])
    return {"quoted": done, "failed": failed}


def intraday(conn, row, interval, period):
    """Intraday bars from Yahoo for the chart; never stored."""
    with _providers(conn) as p:
        return p.yahoo.intraday_bars(ref_for(row), interval, period)


# --- Reads ---------------------------------------------------------------------------------------

BAR_SQL = (
    "SELECT instrument_id, date, open, high, low, close, adj_close, volume, source FROM ("
    " SELECT *, ROW_NUMBER() OVER (PARTITION BY instrument_id, date ORDER BY source) AS n FROM price_bars"
    " WHERE {where}) WHERE n = 1 ORDER BY instrument_id, date")


def _bar(r):
    return {"date": r["date"], "open": dec(r["open"]), "high": dec(r["high"]), "low": dec(r["low"]),
            "close": dec(r["close"]), "adj_close": dec(r["adj_close"]), "volume": r["volume"]}


def bars(conn, instrument_id, start=None, end=None):
    """Daily bars oldest first, one per date: [{date, open, high, low, close, adj_close, volume}]
    with Decimals. `start`/`end` (date or 'YYYY-MM-DD') are inclusive."""
    rows = conn.execute(BAR_SQL.format(where="instrument_id = ? AND date >= ? AND date <= ?"),
                        (instrument_id, str(start or "0000"), str(end or "9999")))
    return [_bar(r) for r in rows]


def closes(conn, instrument_ids, since):
    """{instrument_id: [(date, close Decimal)]} oldest first, for several instruments at once."""
    ids = list(instrument_ids)
    out = {i: [] for i in ids}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(BAR_SQL.format(where=f"instrument_id IN ({marks}) AND date >= ?"), (*ids, str(since))):
            out[r["instrument_id"]].append((r["date"], dec(r["close"])))
    return out


def latest_price(conn, instrument_id):
    """{price, previous_close, as_of, source}: the daytime quote (as_of UTC '…Z') unless a newer
    bar is stored, else the last bar (as_of its date 'YYYY-MM-DD'). None without either."""
    recent = conn.execute(
        "SELECT date, close, source FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY date ORDER BY source) AS n "
        "FROM price_bars WHERE instrument_id = ?) WHERE n = 1 ORDER BY date DESC LIMIT 2", (instrument_id,)).fetchall()
    q = conn.execute("SELECT * FROM quotes WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if q is not None and (not recent or q["as_of"][:10] >= recent[0]["date"]):
        return {"price": dec(q["price"]), "previous_close": dec(q["previous_close"]), "as_of": q["as_of"], "source": q["source"]}
    if not recent:
        return None
    return {"price": dec(recent[0]["close"]), "previous_close": dec(recent[1]["close"]) if len(recent) > 1 else None,
            "as_of": recent[0]["date"], "source": recent[0]["source"]}


def fx(conn, day, from_ccy, to_ccy):
    """1 `from_ccy` in `to_ccy` from the latest ECB rates on or before `day`, through EUR; None
    when a rate is missing."""
    if from_ccy == to_ccy:
        return Decimal(1)

    def per_eur(ccy):
        if ccy == BASE_CURRENCY:
            return Decimal(1)
        r = conn.execute("SELECT rate FROM fx_rates WHERE base = ? AND quote = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                         (BASE_CURRENCY, ccy, str(day))).fetchone()
        return dec(r["rate"]) if r else None

    a, b = per_eur(from_ccy), per_eur(to_ccy)
    if not a or b is None:
        return None
    return b / a


FOUR = Decimal("0.0001")
YEAR = trend.YEAR


def price_stats(conn, instrument_id, today):
    """{last_close, ret_5d, ret_20d, ret_60d, ret_1y, vol_20d, vol_60d, high_52w, low_52w}, Decimal
    or None. Returns, the range and volatility (annualised) use adjusted closes where the source
    has them; last_close is the plain close."""
    keys = ("last_close", "ret_5d", "ret_20d", "ret_60d", "ret_1y", "vol_20d", "vol_60d", "high_52w", "low_52w")
    out = dict.fromkeys(keys)
    rows = bars(conn, instrument_id, start=today - timedelta(days=400), end=today)[-(YEAR + 1):]
    if not rows:
        return out
    values = [r["adj_close"] if r["adj_close"] is not None else r["close"] for r in rows]
    out["last_close"] = rows[-1]["close"]
    out["high_52w"], out["low_52w"] = max(values), min(values)
    for key, n in (("ret_5d", 5), ("ret_20d", 20), ("ret_60d", 60), ("ret_1y", YEAR)):
        if len(values) > n and values[-1 - n]:
            out[key] = (values[-1] / values[-1 - n] - 1).quantize(FOUR)
    for key, n in (("vol_20d", 20), ("vol_60d", 60)):
        if len(values) > n and all(values[-1 - n:]):
            rets = [values[i] / values[i - 1] - 1 for i in range(len(values) - n, len(values))]
            mean = sum(rets) / n
            var = sum((r - mean) ** 2 for r in rets) / (n - 1)
            out[key] = (var * YEAR).sqrt().quantize(FOUR)
    return out


def _adjusted(r):
    return float(r["adj_close"] if r["adj_close"] is not None else r["close"])


def vs_benchmark(conn, row, rows):
    """The stock / SPY ratio on common dates from adjusted closes; [] for SPY itself or without
    SPY bars."""
    if row["symbol"] == BENCHMARK:
        return []
    bench = conn.execute("SELECT id FROM instruments WHERE symbol = ? ORDER BY id LIMIT 1", (BENCHMARK,)).fetchone()
    if bench is None:
        return []
    first = rows[0]["date"] if rows else None
    bench_rows = bars(conn, bench["id"], start=first)
    return trend.relative([(date.fromisoformat(r["date"]), _adjusted(r)) for r in rows],
                          [(date.fromisoformat(r["date"]), _adjusted(r)) for r in bench_rows])


def summary_dict(s):
    """trend.TrendSummary as JSON-ready values (ratios as rounded floats)."""
    if s is None:
        return None

    def r(v):
        return None if v is None else round(v, 6)

    return {
        "state": s.state, "price": r(s.price), "sma_50": r(s.sma_fast), "sma_200": r(s.sma_slow),
        "vs_sma_50": r(s.vs_fast), "vs_sma_200": r(s.vs_slow),
        "last_cross": {"date": s.last_cross.date.isoformat(), "kind": s.last_cross.kind} if s.last_cross else None,
        "high_52w": r(s.high_52w), "low_52w": r(s.low_52w), "range_position": r(s.range_position),
        "drawdown": r(s.drawdown), "excess": {str(k): r(v) for k, v in s.excess.items()},
    }


def trend_summary(conn, instrument_id, today):
    """The trend card: {state (uptrend | downtrend | mixed | None), price, sma_50, sma_200,
    vs_sma_50, vs_sma_200, last_cross {date, kind} | None, high_52w, low_52w, range_position,
    drawdown, excess {"20", "60", "252"} vs SPY}. None without bars."""
    row = instrument(conn, instrument_id)
    rows = bars(conn, instrument_id, end=today) if row else []
    if not rows:
        return None
    ratio = vs_benchmark(conn, row, rows)
    return summary_dict(trend.summarise([date.fromisoformat(r["date"]) for r in rows], [float(r["close"]) for r in rows],
                                        [_adjusted(r) for r in rows], ratio))


def change_since(series, days):
    """Return from the last close on or before (last date - days) to the last close; series is
    [(date text, close)] oldest first."""
    if not series:
        return None
    last_day, last = series[-1]
    cutoff = (date.fromisoformat(last_day) - timedelta(days=days)).isoformat()
    base = next((c for d, c in reversed(series) if d <= cutoff), None)
    if not base:
        return None
    try:
        return last / base - 1
    except InvalidOperation:
        return None
