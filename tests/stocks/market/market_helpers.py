"""Offline doubles for the market package (imported by its tests; conftest.py holds the fixtures).

Fixture provenance (fixtures/):
- yahoo/*.json: Yahoo's chart and search JSON recorded with httpx on 2026-09-29 (no key, no
  cookie), bodies unchanged: AAPL 2020-08 (the $0.82 dividend reported as 0.205, and the 4:1
  split), VWCE.DE around Christmas 2024 (XETRA holidays), NVO 2023-08/09 (2:1 split), an empty
  weekend range, 5-day quotes, one day of 5-minute bars, an unknown symbol (404), two searches.
- frankfurter/*.json: recorded from api.frankfurter.dev v2 on 2026-09-21 (copied from
  stock-oracle).
- finnhub/*.json: HANDCRAFTED in the shapes of Finnhub's published spec (copied from
  stock-oracle); no API key in any of them.
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.stocks.providers import RateLimiter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def no_wait():
    return RateLimiter(10_000, 1.0)


class Router:
    """An httpx transport answering by URL prefix (the last matching route wins); every request
    is kept in `calls`. An unrouted request fails the test."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, prefix, answer):
        self.routes.append((prefix, answer))

    def __call__(self, request):
        self.calls.append(request)
        for prefix, answer in reversed(self.routes):
            if str(request.url).startswith(prefix):
                return answer(request) if callable(answer) else answer
        raise AssertionError(f"unrouted request {request.url}")

    def throttle(self):
        self.add("", httpx.Response(429, headers={"Retry-After": "7"}))

    def break_transport(self):
        def boom(request):
            raise httpx.ConnectError("boom", request=request)
        self.add("", boom)


# --- Yahoo from the recordings -------------------------------------------------------------

DAILY = {"AAPL": "chart_aapl_2020-08", "VWCE.DE": "chart_vwce_de_2024-12", "NVO": "chart_nvo_2023-09"}
RANGES = {("AAPL", "5d", "1d"): "chart_aapl_5d", ("VWCE.DE", "5d", "1d"): "chart_vwce_de_5d",
          ("AAPL", "1d", "5m"): "chart_aapl_1d_5m"}


def _window(body, lo, hi):
    """A recorded daily chart cut to [lo, hi), as Yahoo answers a narrower period: the empty
    case in the shape of the recorded weekend."""
    body = copy.deepcopy(body)
    result = body["chart"]["result"][0]
    keep = [i for i, t in enumerate(result["timestamp"]) if lo <= t < hi]
    if not keep:
        del result["timestamp"]
        result["indicators"] = {"quote": [{}], "adjclose": [{}]}
        result["events"] = {"dividends": {}}
        return body
    result["timestamp"] = [result["timestamp"][i] for i in keep]
    for series in (result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]):
        for key in series:
            series[key] = [series[key][i] for i in keep]
    for kind in ("dividends", "splits"):
        events = result.get("events", {}).get(kind, {})
        for stamp in list(events):
            if not lo <= int(stamp) < hi:
                del events[stamp]
    return body


def yahoo_chart(request):
    symbol = request.url.path.rsplit("/", 1)[1]
    params = request.url.params
    if "range" in params:
        name = RANGES.get((symbol, params["range"], params["interval"]))
    else:
        name = DAILY.get(symbol)
    if name is None:
        return httpx.Response(404, json=load("yahoo/chart_nope.json"))
    body = load(f"yahoo/{name}.json")
    if "period1" in params:
        body = _window(body, int(params["period1"]), int(params["period2"]))
    return httpx.Response(200, json=body)


def yahoo_search(request):
    name = "search_vwce" if "vwce" in request.url.params["q"].lower() else "search_apple"
    return httpx.Response(200, json=load(f"yahoo/{name}.json"))


def yahoo_routes(router):
    router.add("https://query1.finance.yahoo.com/v8/finance/chart/", yahoo_chart)
    router.add("https://query2.finance.yahoo.com/v1/finance/search", yahoo_search)


# --- Finnhub -------------------------------------------------------------------------------

FINNHUB = "https://finnhub.io/api/v1"


def finnhub_routes(router, *, free_tier):
    """Symbol-aware routes. `free_tier` answers candles/dividends/splits with "no access"."""

    def by_symbol(known, unknown):
        def respond(request):
            assert request.headers["X-Finnhub-Token"] == "test-key"
            name = known if request.url.params.get("symbol") == "AAPL" else unknown
            return httpx.Response(200, json=load(f"finnhub/{name}"))
        return respond

    if free_tier:
        for path in ("/stock/candle", "/stock/dividend", "/stock/split"):
            router.add(FINNHUB + path, httpx.Response(403, json=load("finnhub/no_access.json")))
    else:
        router.add(FINNHUB + "/stock/candle", by_symbol("candle_aapl_2020-08.json", "candle_no_data.json"))
        router.add(FINNHUB + "/stock/dividend", lambda r: httpx.Response(
            200, json=load("finnhub/dividend_aapl.json") if r.url.params["symbol"] == "AAPL" else []))
        router.add(FINNHUB + "/stock/split", lambda r: httpx.Response(
            200, json=load("finnhub/split_aapl.json") if r.url.params["symbol"] == "AAPL" else []))
    router.add(FINNHUB + "/quote", by_symbol("quote_aapl.json", "quote_unknown.json"))
    router.add(FINNHUB + "/search", httpx.Response(200, json=load("finnhub/search_apple.json")))


def utc(*args):
    return datetime(*args, tzinfo=UTC)
