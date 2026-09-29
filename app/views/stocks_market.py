"""Aktsiad: the market package's API (docs/CONTRACTS.md, "Aktsiad"): the watchlist (Jälgin), symbol
search, a stock's page data and its chart. Owner only, JSON, Estonian errors.

Decimals go out as strings; the chart's series are plain numbers, because they are only drawn and
Lightweight Charts wants numbers.

Owner: WP stocks-market."""

import json
import logging
import os
import threading
import time as clock
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db, tx
from ..security import rate_limit
from ..stocks import market, trend
from ..stocks.common import by_symbol, clear_all, dtext
from ..stocks.providers import ProviderError
from ..util import TALLINN, local_today

log = logging.getLogger(__name__)
bp = Blueprint("stocks_market", __name__)

SPARK_DAYS = 90
SIX = Decimal("0.000001")
NOTE_MAX = 2000
TAG_MAX, TAGS_MAX = 40, 20


def _bad(message, status=400):
    return jsonify(error=message), status


def _s(value, quant=None):
    """Decimal -> JSON string; None stays None."""
    if value is None:
        return None
    return dtext(value.quantize(quant) if quant else value)


def _holdings(conn):
    """Held quantity per instrument from the portfolio package; {} until that package is there."""
    try:
        from ..stocks import portfolio
    except ImportError:
        return {}
    return portfolio.holdings(conn)


def _change(price, previous):
    if price is None or not previous:
        return None
    return price / previous - 1


# --- Jälgin ------------------------------------------------------------------------------------

def _rows(conn, today, only=None):
    where, args = ("WHERE w.instrument_id = ?", (only,)) if only else ("", ())
    pairs = conn.execute(
        f"SELECT i.*, w.note, w.tags_json, w.added_at FROM instruments i JOIN watchlist w ON w.instrument_id = i.id {where} "
        "ORDER BY i.symbol, i.id", args).fetchall()
    ids = [p["id"] for p in pairs]
    if not ids:
        return []
    series = market.closes(conn, ids, today - timedelta(days=370))
    held = _holdings(conn)
    marks = ",".join("?" * len(ids))
    analysed = dict(conn.execute(
        f"SELECT instrument_id, MAX(as_of) FROM analyses WHERE instrument_id IN ({marks}) GROUP BY instrument_id", ids).fetchall())
    verdicts = {r["instrument_id"]: r for r in conn.execute(
        "SELECT instrument_id, verdict, conviction, as_of FROM ("
        " SELECT *, ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY as_of DESC, id DESC) AS n"
        f" FROM verdicts WHERE instrument_id IN ({marks})) WHERE n = 1", ids)}
    spark_from = (today - timedelta(days=SPARK_DAYS)).isoformat()
    out = []
    for p in pairs:
        closes = series[p["id"]]
        last = market.latest_price(conn, p["id"])
        v = verdicts.get(p["id"])
        out.append({
            "id": p["id"], "symbol": p["symbol"], "name": p["name"], "exchange": p["exchange"],
            "currency": p["currency"], "kind": p["kind"], "note": p["note"] or "",
            "tags": json.loads(p["tags_json"] or "[]"), "added_at": p["added_at"],
            "price": _s(last and last["price"]), "previous_close": _s(last and last["previous_close"]),
            "day_change": _s(last and _change(last["price"], last["previous_close"]), SIX),
            "as_of": last and last["as_of"],
            "ret_1m": _s(market.change_since(closes, 30), SIX), "ret_1y": _s(market.change_since(closes, 365), SIX),
            "spark": [_s(c) for d, c in closes if d >= spark_from],
            "held": _s(held.get(p["id"])),
            "last_analysis": analysed.get(p["id"]),
            "verdict": {"verdict": v["verdict"], "conviction": v["conviction"], "as_of": v["as_of"]} if v else None,
            "has_bars": bool(closes) or last is not None,
            "loading": market.backfill_running(conn, p["id"]),
        })
    return out


@bp.get("/api/stocks/watchlist")
def watchlist():
    return jsonify(_rows(get_db(), local_today()))


@bp.post("/api/stocks/watchlist")
def add():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("symbol"), str):
        return _bad("Sümbol puudub.")
    fields = {k: data.get(k) if isinstance(data.get(k), str) and data.get(k).strip() else None
              for k in ("name", "exchange", "currency", "kind")}
    conn = get_db()
    try:
        row = market.add(conn, data["symbol"], **fields)
    except ValueError as exc:
        return _bad(str(exc))
    if not conn.execute("SELECT 1 FROM price_bars WHERE instrument_id = ? LIMIT 1", (row["id"],)).fetchone():
        # Five years of bars take a paced Yahoo call or two: the page shows "Laen ajalugu…" meanwhile.
        market.start_backfill(current_app.config["DATABASE_PATH"], row["id"])
    return jsonify(_rows(conn, local_today(), only=row["id"])[0]), 201


def _watched(conn, instrument_id):
    return conn.execute("SELECT * FROM watchlist WHERE instrument_id = ?", (instrument_id,)).fetchone()


@bp.delete("/api/stocks/watchlist/<int:instrument_id>")
def remove(instrument_id):
    conn = get_db()
    if _watched(conn, instrument_id) is None:
        return _bad("Seda aktsiat ei jälgita.", 404)
    # The instrument and its bars stay: holdings, research and the import may still refer to it.
    conn.execute("DELETE FROM watchlist WHERE instrument_id = ?", (instrument_id,))
    return "", 204


def _tags(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError("Sildid on vigased.")
    tags = sorted({t.strip().lower().lstrip("#") for t in value if t.strip().lstrip("#")})
    if len(tags) > TAGS_MAX or any(len(t) > TAG_MAX for t in tags):
        raise ValueError(f"Kuni {TAGS_MAX} silti, igaüks kuni {TAG_MAX} märki.")
    return tags


@bp.patch("/api/stocks/watchlist/<int:instrument_id>")
def edit(instrument_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _bad("Vigane päring.")
    conn = get_db()
    with tx(conn):
        if _watched(conn, instrument_id) is None:
            return _bad("Seda aktsiat ei jälgita.", 404)
        if "note" in data:
            note = data["note"]
            if note is not None and not isinstance(note, str):
                return _bad("Märkus on vigane.")
            if note and len(note) > NOTE_MAX:
                return _bad(f"Märkus on liiga pikk (kuni {NOTE_MAX} märki).")
            conn.execute("UPDATE watchlist SET note = ? WHERE instrument_id = ?", ((note or "").strip() or None, instrument_id))
        if "tags" in data:
            try:
                tags = _tags(data["tags"])
            except ValueError as exc:
                return _bad(str(exc))
            conn.execute("UPDATE watchlist SET tags_json = ? WHERE instrument_id = ?", (json.dumps(tags), instrument_id))
    return jsonify(_rows(conn, local_today(), only=instrument_id)[0])


@bp.get("/api/stocks/search")
@rate_limit("stocks-search", 30, 60)
def search():
    try:
        return jsonify(market.search(get_db(), request.args.get("q", "")))
    except ProviderError as exc:
        log.warning("symbol search failed: %s", exc)
        return _bad("Sümbolite otsing ei tööta praegu. Proovi minuti pärast uuesti.", 503)


# --- The stock page ----------------------------------------------------------------------------

def _instrument_or_404(symbol):
    row = by_symbol(get_db(), symbol)
    if row is None:
        return None, _bad("Tundmatu sümbol.", 404)
    return row, None


@bp.get("/api/stocks/instruments/<symbol>")
def instrument_detail(symbol):
    row, error = _instrument_or_404(symbol)
    if error:
        return error
    conn, today = get_db(), local_today()
    watch = _watched(conn, row["id"])
    last = market.latest_price(conn, row["id"])
    stats = market.price_stats(conn, row["id"], today)
    return jsonify({
        "instrument": {k: row[k] for k in ("id", "symbol", "name", "exchange", "currency", "kind", "isin", "sector")},
        "watch": {"note": watch["note"] or "", "tags": json.loads(watch["tags_json"] or "[]"), "added_at": watch["added_at"]} if watch else None,
        "last": {"price": _s(last["price"]), "previous_close": _s(last["previous_close"]), "as_of": last["as_of"],
                 "source": last["source"], "day_change": _s(_change(last["price"], last["previous_close"]), SIX)} if last else None,
        "stats": {k: _s(v) for k, v in stats.items()},
        "trend": market.trend_summary(conn, row["id"], today),
        "has_bars": stats["last_close"] is not None,
        "loading": market.backfill_running(conn, row["id"]),
        "benchmark": row["symbol"] == market.BENCHMARK,
    })


def _line(days, values):
    return [{"time": d, "value": round(v, 4)} for d, v in zip(days, values, strict=True) if v]


def chart_payload(conn, row):
    """Candles, volume, SMA 50/200, the vs-SPY ratio and the E/R markers from the stored daily bars,
    and the same grouped into weekly and monthly bars for 2Y and 5Y."""
    rows = market.bars(conn, row["id"])
    candles, volume = [], []
    for r in rows:
        close = float(r["close"])
        candles.append({"time": r["date"], "open": float(r["open"]) if r["open"] is not None else close,
                        "high": float(r["high"]) if r["high"] is not None else close,
                        "low": float(r["low"]) if r["low"] is not None else close, "close": close})
        if r["volume"] is not None:
            volume.append({"time": r["date"], "value": r["volume"]})
    if not rows:
        return {"candles": [], "volume": [], "sma_50": [], "sma_200": [], "relative": [], "markers": [], "weekly": {}, "monthly": {}}
    days = [c["time"] for c in candles]
    closes = [c["close"] for c in candles]
    lines = {
        "sma_50": _line(days, trend.sma(closes, trend.FAST)),
        "sma_200": _line(days, trend.sma(closes, trend.SLOW)),
        "relative": [{"time": d.isoformat(), "value": round(v, 6)} for d, v in market.vs_benchmark(conn, row, rows)],
    }
    first = rows[0]["date"]
    earnings = [r[0] for r in conn.execute("SELECT DISTINCT date FROM earnings WHERE instrument_id = ? AND date >= ?", (row["id"], first))]
    research = [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM analyses WHERE instrument_id = ? AND as_of >= ?", (row["id"], first))]
    markers = sorted([{"time": d, "kind": "earnings", "text": "E"} for d in earnings]
                     + [{"time": d[:10], "kind": "research", "text": "R"} for d in research], key=lambda m: (m["time"], m["kind"]))
    return {"candles": candles, "volume": volume, **lines, "markers": markers,
            "weekly": trend.resample(candles, volume, lines, "week"),
            "monthly": trend.resample(candles, volume, lines, "month")}


@bp.get("/api/stocks/chart/<symbol>")
def chart(symbol):
    row, error = _instrument_or_404(symbol)
    return error or jsonify(chart_payload(get_db(), row))


# Range button -> (Yahoo interval, periods to load, shortest first). Panning to the left edge asks
# for the next period ("depth"). Yahoo keeps 5m and 15m bars for about 60 days and 1h bars for two
# years (stock-oracle's PLAN, Phase 11).
INTRADAY = {
    "1D": ("5m", ("1d", "5d", "1mo")),
    "5D": ("15m", ("5d", "1mo")),
    "1M": ("1h", ("1mo", "3mo", "6mo", "1y", "2y")),
}
INTRADAY_TTL_S = 300.0
# The app runs one gunicorn worker, so an in-process cache is the whole cache. Never stored.
_intraday_cache = {}
_intraday_lock = threading.Lock()


def intraday_payload(bars):
    """Chart JSON for intraday bars. Lightweight Charts shows a timestamp as UTC, so each one is
    shifted by the Tallinn offset to read as Tallinn time."""
    candles, volume = [], []
    for bar in bars:
        offset = bar.start.astimezone(TALLINN).utcoffset() or timedelta(0)
        t = int(bar.start.timestamp() + offset.total_seconds())
        candles.append({"time": t, "open": float(bar.open), "high": float(bar.high), "low": float(bar.low), "close": float(bar.close)})
        if bar.volume is not None:
            volume.append({"time": t, "value": bar.volume})
    return {"candles": candles, "volume": volume}


@bp.get("/api/stocks/chart/<symbol>/intraday")
@rate_limit("stocks-intraday", 60, 60)
def intraday(symbol):
    range_ = request.args.get("range", "")
    if range_ not in INTRADAY:
        return _bad("Tundmatu vahemik.")
    interval, periods = INTRADAY[range_]
    try:
        depth = int(request.args.get("depth", "0"))
    except ValueError:
        depth = -1
    if not 0 <= depth < len(periods):
        return _bad("Vigane sügavus.")
    row, error = _instrument_or_404(symbol)
    if error:
        return error
    key = (row["id"], range_, depth)
    with _intraday_lock:
        hit = _intraday_cache.get(key)
    if hit is not None and hit[0] > clock.monotonic():
        return jsonify(hit[1])
    try:
        bars = market.intraday(get_db(), row, interval, periods[depth])
    except ProviderError as exc:
        log.warning("intraday %s %s failed: %s", row["symbol"], range_, exc)
        return _bad("Päevasisesed hinnad pole praegu saadaval.", 503)
    payload = {"interval": interval, "period": periods[depth], "depth": depth, "more": depth + 1 < len(periods),
               **intraday_payload(bars)}
    with _intraday_lock:
        now = clock.monotonic()
        for k in [k for k, (expires, _) in _intraday_cache.items() if expires <= now]:
            del _intraday_cache[k]
        _intraday_cache[key] = (now + INTRADAY_TTL_S, payload)
    return jsonify(payload)


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) ------------------------------------------

if os.environ.get("MARKMIK_TEST_PAGES") == "1":
    from ..stocks.providers import RateLimiter
    from ..stocks.providers.fake import FakeMarket, synthetic_bars

    def _bars_for(spec, end):
        days = int(spec.get("days", 1300))
        return synthetic_bars(end - timedelta(days=int(days * 7 / 5) + 3), end, first=float(spec.get("first", 100)),
                              drift=float(spec.get("drift", 0.0004)), seed=int(spec.get("seed", 1)))[-days:]

    @bp.post("/_test/stocks/seed")
    def test_seed():
        """Browser tests: replace every stock row with {instruments: [{symbol, name, exchange,
        currency, kind, watch, note, tags, days, first, drift, seed}], earnings: [{symbol, date}],
        analyses: [{symbol, as_of}], verdicts: [{symbol, as_of, verdict, conviction}]}; bars are
        synthetic and end today."""
        data = request.get_json()
        conn, end = get_db(), local_today()
        clear_all(conn)
        with tx(conn):
            ids = {}
            for spec in data.get("instruments", []):
                iid = conn.execute(
                    "INSERT INTO instruments (symbol, exchange, currency, name, kind, provider_symbols_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (spec["symbol"], spec.get("exchange", "US"), spec.get("currency", "USD"), spec.get("name", spec["symbol"]),
                     spec.get("kind", "stock"), json.dumps(spec.get("provider_symbols", {})))).lastrowid
                ids[spec["symbol"]] = iid
                if spec.get("watch", True):
                    conn.execute("INSERT INTO watchlist (instrument_id, note, tags_json) VALUES (?, ?, ?)",
                                 (iid, spec.get("note"), json.dumps(spec.get("tags", []))))
                if spec.get("days", 1300):
                    conn.executemany(
                        "INSERT INTO price_bars (instrument_id, date, open, high, low, close, adj_close, volume, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yahoo')",
                        [(iid, d.isoformat(), str(o), str(h), str(lo), str(c), str(a), v) for d, o, h, lo, c, a, v in _bars_for(spec, end)])
            for e in data.get("earnings", []):
                conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, ?, 'test')", (ids[e["symbol"]], e["date"]))
            for a in data.get("analyses", []):
                conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) "
                             "VALUES (?, 'on_demand', ?, 'Test', 'test', 'test')", (ids[a["symbol"]], a["as_of"]))
            for v in data.get("verdicts", []):
                conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                             "VALUES (?, ?, ?, ?, 'Test', 'test', 'test')", (ids[v["symbol"]], v["as_of"], v["verdict"], v["conviction"]))
        _intraday_cache.clear()
        return jsonify(ids)

    @bp.route("/_test/stocks/fake-market", methods=["POST", "DELETE"])
    def test_fake_market():
        """POST {symbols: [{symbol, name, currency, exchange, type, tz, days, first}], failure}: Yahoo,
        Frankfurter and Finnhub answer from a FakeMarket, without pacing. DELETE: the network again."""
        if request.method == "DELETE":
            market.TRANSPORT = None
            market.LIMITS = {"yahoo": RateLimiter(1, 2.0), "finnhub": RateLimiter(60, 60.0), "frankfurter": RateLimiter(10, 1.0)}
            return "", 204
        data = request.get_json()
        fake = FakeMarket()
        for spec in data.get("symbols", []):
            fake.add(spec["symbol"], _bars_for(spec, local_today()), name=spec.get("name"), currency=spec.get("currency", "USD"),
                     exchange=spec.get("exchange", "NMS"), type=spec.get("type", "EQUITY"), tz=spec.get("tz", "America/New_York"))
        fake.failure = data.get("failure")
        market.TRANSPORT = fake.transport
        market.LIMITS = {name: RateLimiter(10_000, 1.0) for name in ("yahoo", "finnhub", "frankfurter")}
        _intraday_cache.clear()
        return "", 204
