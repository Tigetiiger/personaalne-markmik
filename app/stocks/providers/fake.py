"""A fake market for tests: answers Yahoo's chart and search JSON, Frankfurter's rates and
Finnhub's quote in their real shapes (the recorded fixtures in tests/stocks/market/fixtures show
them) from synthetic series, through an httpx.MockTransport. Used by pytest and, through the
test-only route in app/views/stocks_market.py, by the browser tests, so neither reaches the
network."""

import math
import threading
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx


def weekdays(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def synthetic_bars(start, end, *, first=100.0, drift=0.0004, swing=0.02, seed=1):
    """Deterministic weekday bars [(date, open, high, low, close, adj_close, volume)]: a gentle
    trend with a wave on it, so the averages cross now and then."""
    out, price = [], first
    for i, day in enumerate(weekdays(start, end)):
        move = drift + swing * math.sin((i + seed * 7) / 23.0) / 10 + 0.004 * math.sin((i * seed) / 3.1)
        open_ = price
        price = max(1.0, price * (1 + move))
        high, low = max(open_, price) * 1.006, min(open_, price) * 0.994
        out.append((day, round(open_, 4), round(high, 4), round(low, 4), round(price, 4), round(price, 4),
                    int(1_000_000 + 400_000 * math.sin(i / 5.0) ** 2)))
    return out


class FakeMarket:
    def __init__(self):
        self.symbols = {}
        self.fx = {}           # quote currency -> rate per EUR
        self.finnhub = {}      # symbol -> (price, previous close, epoch)
        self.failure = None    # None | "throttle" | "down": every Yahoo call fails so
        self.calls = []
        self._lock = threading.Lock()
        self._opens = {}

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)

    def add(self, symbol, bars, *, name=None, currency="USD", exchange="NMS", type="EQUITY",
            tz="America/New_York", dividends=None, splits=None, price=None, market_time=None):
        """bars as synthetic_bars gives them; dividends {date: amount as Yahoo reports it (split-
        adjusted)}; splits {date: (numerator, denominator)}; market_time: aware datetime of the
        quote (default the last bar's close)."""
        self.symbols[symbol] = {"bars": list(bars), "name": name or symbol, "currency": currency, "exchange": exchange,
                                "type": type, "tz": tz, "dividends": dict(dividends or {}), "splits": dict(splits or {}),
                                "price": price, "market_time": market_time}

    # --- the transport ---------------------------------------------------------------------

    def handler(self, request):
        with self._lock:
            self.calls.append(request)
        url, host = request.url, request.url.host
        if host.endswith("finance.yahoo.com"):
            if self.failure == "throttle":
                return httpx.Response(429, text="Too Many Requests")
            if self.failure == "down":
                raise httpx.ConnectError("fake Yahoo is down", request=request)
            if url.path.startswith("/v8/finance/chart/"):
                return self._chart(unquote(url.path.rsplit("/", 1)[1]), url.params)
            if url.path == "/v1/finance/search":
                return self._search(url.params.get("q", ""))
        if host == "api.frankfurter.dev":
            return self._rates(url.params)
        if host == "finnhub.io" and url.path == "/api/v1/quote":
            return self._finnhub_quote(url.params.get("symbol"))
        return httpx.Response(404, json={"error": "fake market: no such endpoint"})

    def _chart(self, symbol, params):
        s = self.symbols.get(symbol)
        if s is None:
            return httpx.Response(404, json={"chart": {"result": None, "error": {
                "code": "Not Found", "description": "No data found, symbol may be delisted"}}})
        zone = ZoneInfo(s["tz"])

        def opening(day):
            key = (s["tz"], day)
            if key not in self._opens:
                self._opens[key] = int(datetime.combine(day, time(9, 30), zone).timestamp())
            return self._opens[key]

        last = s["bars"][-1] if s["bars"] else None
        market_time = s["market_time"] or (datetime.combine(last[0], time(16, 0), zone) if last else datetime.now(UTC))
        meta = {"currency": s["currency"], "symbol": symbol, "exchangeName": s["exchange"], "instrumentType": s["type"],
                "exchangeTimezoneName": s["tz"], "longName": s["name"], "shortName": s["name"],
                "regularMarketPrice": s["price"] if s["price"] is not None else (last[4] if last else None),
                "regularMarketTime": int(market_time.timestamp())}
        if params.get("interval", "1d") != "1d":
            stamps, quote = self._intraday(s, params, zone)
            return self._result(meta, stamps, quote, None, {})
        if "range" in params:
            days = {"5d": 5, "1mo": 22, "3mo": 64, "1y": 252}.get(params["range"], 5)
            rows = s["bars"][-days:]
        else:
            lo, hi = int(params["period1"]), int(params["period2"])
            rows = [b for b in s["bars"] if lo <= opening(b[0]) < hi]
        if len(rows) < len(s["bars"]) and rows:
            before = [b for b in s["bars"] if b[0] < rows[0][0]]
            meta["chartPreviousClose"] = before[-1][4] if before else rows[0][1]
        first, end = (rows[0][0], rows[-1][0] + timedelta(days=1)) if rows else (None, None)
        events = {}
        if rows and "div" in params.get("events", ""):
            events["dividends"] = {str(opening(d)): {"amount": a, "date": opening(d)}
                                   for d, a in s["dividends"].items() if first <= d < end}
            events["splits"] = {str(opening(d)): {"date": opening(d), "numerator": float(n), "denominator": float(m),
                                                  "splitRatio": f"{n}:{m}"}
                                for d, (n, m) in s["splits"].items() if first <= d < end}
        quote = {k: [b[i] for b in rows] for k, i in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 6))}
        return self._result(meta, [opening(b[0]) for b in rows], quote, [b[5] for b in rows], events)

    @staticmethod
    def _result(meta, stamps, quote, adjclose, events):
        result = {"meta": meta}
        if stamps:
            result["timestamp"] = stamps
            result["indicators"] = {"quote": [quote], **({"adjclose": [{"adjclose": adjclose}]} if adjclose else {})}
        else:
            result["indicators"] = {"quote": [{}], "adjclose": [{}]}
        if events:
            result["events"] = events
        return httpx.Response(200, json={"chart": {"result": [result], "error": None}})

    @staticmethod
    def _intraday(s, params, zone):
        """Bars of `interval` over the last trading days of `range`, around the last close."""
        step = {"5m": 5, "15m": 15, "1h": 60}.get(params.get("interval"), 5)
        days = {"1d": 1, "5d": 5, "1mo": 22, "3mo": 64, "6mo": 126, "1y": 252, "2y": 504}.get(params.get("range"), 1)
        stamps, quote = [], {k: [] for k in ("open", "high", "low", "close", "volume")}
        for n, bar in enumerate(s["bars"][-days:]):
            price = bar[1]
            for k in range(0, 390, step):
                start = datetime.combine(bar[0], time(9, 30), zone) + timedelta(minutes=k)
                close = price * (1 + 0.002 * math.sin((n * 390 + k) / 37.0))
                stamps.append(int(start.timestamp()))
                for key, value in (("open", price), ("high", max(price, close) * 1.001), ("low", min(price, close) * 0.999),
                                   ("close", close), ("volume", 10_000 + k * 10)):
                    quote[key].append(round(value, 4) if key != "volume" else value)
                price = close
        return stamps, quote

    def _search(self, query):
        q = query.strip().lower()
        quotes = [{"symbol": sym, "shortname": s["name"], "longname": s["name"], "quoteType": s["type"],
                   "exchange": s["exchange"], "exchDisp": s["exchange"]}
                  for sym, s in self.symbols.items() if q and (sym.lower().startswith(q) or q in s["name"].lower())]
        return httpx.Response(200, json={"quotes": quotes, "news": []})

    def _rates(self, params):
        quote = params.get("quotes")
        if quote not in self.fx:
            return httpx.Response(422, json={"status": 422, "message": f"invalid currency: {quote}"})
        start = date.fromisoformat(params.get("from") or params["date"])
        end = date.fromisoformat(params.get("to") or params["date"])
        while start.weekday() >= 5:  # a range starts at the last business day on or before it
            start -= timedelta(days=1)
        return httpx.Response(200, json=[{"date": d.isoformat(), "base": params["base"], "quote": quote, "rate": self.fx[quote]}
                                         for d in weekdays(start, end)])

    def _finnhub_quote(self, symbol):
        if symbol not in self.finnhub:
            return httpx.Response(200, json={"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})
        price, previous, stamp = self.finnhub[symbol]
        return httpx.Response(200, json={"c": price, "pc": previous, "t": stamp, "h": price, "l": price, "o": price})
