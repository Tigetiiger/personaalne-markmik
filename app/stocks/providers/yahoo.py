"""Yahoo Finance's chart and search JSON through httpx (docs/spikes/yahoo.md): daily bars with
adjusted closes, dividends and splits, intraday bars for the stock page's chart, quotes and
symbol search. Replaces stock-oracle's yfinance adapter without pandas, numpy or a disk cache.

Unofficial and throttled by Yahoo, so calls go through a slow limiter and a 429 surfaces as
`RateLimited`.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .ratelimit import RateLimiter
from .types import (
    Bar,
    CorporateAction,
    InstrumentRef,
    IntradayBar,
    NotCovered,
    ProviderError,
    Quote,
    RateLimited,
    SymbolMatch,
    clean_bars,
)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
# A full desktop-Chrome User-Agent got 429 where this short one was answered (2026-09-29).
HEADERS = {"User-Agent": "Mozilla/5.0 (Markmik)"}
DAILY = {"interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}


def _dec(value: Any) -> Decimal | None:
    """Yahoo prices are float32 underneath (135.63999938964844 for 135.64), so keep the 7
    significant digits float32 actually carries."""
    if value is None:
        return None
    return Decimal(f"{float(value):.7g}")


def _midnight(day: date) -> int:
    return int(datetime.combine(day, time.min, UTC).timestamp())


def _zone(meta: dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


class YahooProvider:
    name = "yahoo"

    def __init__(self, client: httpx.Client, limiter: RateLimiter | None = None) -> None:
        self._client = client
        self._limiter = limiter or RateLimiter(1, 2.0)
        # daily_bars and corporate_actions over the same range read the same response: a
        # backfill or refresh costs one paced call per instrument instead of two.
        self._memo: tuple[tuple[str, int, int], dict[str, Any]] | None = None

    def _get(self, url: str, params: dict[str, Any], what: str) -> Any:
        self._limiter.acquire()
        try:
            response = self._client.get(url, params=params, headers=HEADERS)
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"{what}: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise RateLimited(self.name, f"{what}: throttled by Yahoo")
        if response.status_code == 404:
            try:
                reason = response.json()["chart"]["error"]["description"]
            except (ValueError, KeyError, TypeError):
                reason = "not found"
            raise NotCovered(self.name, f"{what}: {reason}")
        if response.status_code != 200:
            raise ProviderError(self.name, f"{what}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, f"{what}: invalid JSON") from exc

    def _chart(self, symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        body = self._get(CHART_URL + urlquote(symbol, safe=".-=^"), params, symbol)
        try:
            chart = body["chart"]
            if not chart.get("result"):
                error = chart.get("error") or {}
                raise NotCovered(self.name, f"{symbol}: {error.get('description') or 'no data'}")
            result: dict[str, Any] = chart["result"][0]
            result.setdefault("meta", {})
        except (KeyError, TypeError, IndexError) as exc:
            raise ProviderError(self.name, f"{symbol}: unexpected response") from exc
        return result

    def _daily(self, ref: InstrumentRef, start: date, end: date) -> tuple[dict[str, Any], str]:
        symbol = ref.symbol_for(self.name)
        # Timestamps are the session open in the exchange's zone, which in UTC can fall on the
        # day before (Asia-Pacific): ask for a day more on each side and filter by local date.
        key = (symbol, _midnight(start - timedelta(days=1)), _midnight(end + timedelta(days=2)))
        if self._memo is None or self._memo[0] != key:
            result = self._chart(symbol, {"period1": key[1], "period2": key[2], **DAILY})
            self._memo = (key, result)
        return self._memo[1], symbol

    def daily_bars(self, ref: InstrumentRef, start: date, end: date) -> list[Bar]:
        result, symbol = self._daily(ref, start, end)
        zone = _zone(result["meta"])
        try:
            stamps = result.get("timestamp") or []
            q = result["indicators"]["quote"][0] if stamps else {}
            adjusted = ((result["indicators"].get("adjclose") or [{}])[0].get("adjclose")) if stamps else None
            bars = []
            for i, stamp in enumerate(stamps):
                o, h, lo, c = (_dec(q[k][i]) for k in ("open", "high", "low", "close"))
                # Yahoo has a few days with null values (VWCE.DE: 3 in 5 years): not a bar.
                if o is None or h is None or lo is None or c is None:
                    continue
                volume = q.get("volume", [None] * len(stamps))[i]
                bars.append(Bar(
                    date=datetime.fromtimestamp(stamp, zone).date(),
                    open=o, high=h, low=lo, close=c,
                    adj_close=_dec(adjusted[i]) if adjusted else None,
                    volume=None if volume is None else int(volume),
                    source=self.name,
                ))
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise ProviderError(self.name, f"{symbol}: unexpected bars") from exc
        return clean_bars(bars, start, end)

    def corporate_actions(self, ref: InstrumentRef, start: date, end: date) -> list[CorporateAction]:
        """Splits and dividends with an ex-date in [start, end]. Yahoo divides a dividend by every
        later split; that is undone for the splits inside the range, which is all of them when
        `end` is today, as it is for every caller."""
        result, symbol = self._daily(ref, start, end)
        zone = _zone(result["meta"])
        events = result.get("events") or {}
        currency = result["meta"].get("currency") or ref.currency
        try:
            splits = [
                (datetime.fromtimestamp(s["date"], zone).date(), Decimal(str(s["numerator"])) / Decimal(str(s["denominator"])))
                for s in (events.get("splits") or {}).values()
            ]
            actions = [CorporateAction.split(day, ratio.normalize(), self.name) for day, ratio in splits]
            for d in (events.get("dividends") or {}).values():
                day = datetime.fromtimestamp(d["date"], zone).date()
                amount = Decimal(str(d["amount"]))
                for split_day, ratio in splits:
                    if split_day > day:
                        amount *= ratio
                actions.append(CorporateAction.dividend(day, _dec(amount), currency, self.name))
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ProviderError(self.name, f"{symbol}: unexpected events") from exc
        return sorted((a for a in actions if start <= a.date <= end), key=lambda a: (a.date, a.kind))

    def intraday_bars(self, ref: InstrumentRef, interval: str, period: str) -> list[IntradayBar]:
        """Regular-session bars (e.g. interval "5m", period "1d"), about 15 minutes delayed.
        Yahoo keeps 5m/15m bars for 60 days and 1h bars for two years."""
        symbol = ref.symbol_for(self.name)
        result = self._chart(symbol, {"range": period, "interval": interval, "includePrePost": "false"})
        try:
            stamps = result.get("timestamp") or []
            q = result["indicators"]["quote"][0] if stamps else {}
            bars = []
            for i, stamp in enumerate(stamps):
                o, h, lo, c = (_dec(q[k][i]) for k in ("open", "high", "low", "close"))
                if o is None or h is None or lo is None or c is None:
                    continue
                volume = q.get("volume", [None] * len(stamps))[i]
                bars.append(IntradayBar(datetime.fromtimestamp(stamp, UTC), o, h, lo, c,
                                        None if volume is None else int(volume)))
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise ProviderError(self.name, f"{symbol}: unexpected bars") from exc
        return bars

    def quote(self, ref: InstrumentRef) -> Quote:
        symbol = ref.symbol_for(self.name)
        result = self._chart(symbol, {"range": "5d", "interval": "1d"})
        meta = result["meta"]
        price = _dec(meta.get("regularMarketPrice"))
        if price is None or not meta.get("regularMarketTime"):
            raise NotCovered(self.name, f"no quote for {symbol}")
        zone = _zone(meta)
        as_of = datetime.fromtimestamp(meta["regularMarketTime"], UTC)
        market_day = as_of.astimezone(zone).date()
        # The previous close is the last daily bar before the quote's own trading day.
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close", []) if stamps else []
        earlier = [c for t, c in zip(stamps, closes) if c is not None and datetime.fromtimestamp(t, zone).date() < market_day]
        previous = _dec(earlier[-1]) if earlier else _dec(meta.get("chartPreviousClose"))
        return Quote(symbol=symbol, price=price, previous_close=previous, as_of=as_of, source=self.name)

    def describe(self, symbol: str) -> dict[str, Any]:
        """What the chart's meta says about a symbol: {name, currency, exchange, type}, for adding
        a symbol typed without a search result."""
        meta = self._chart(symbol, {"range": "5d", "interval": "1d"})["meta"]
        return {
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName"),
            "type": meta.get("instrumentType"),
        }

    def search(self, query: str) -> list[SymbolMatch]:
        body = self._get(SEARCH_URL, {"q": query, "quotesCount": 10, "newsCount": 0, "listsCount": 0}, "search")
        rows = body.get("quotes") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(self.name, "search: unexpected response")
        return [
            SymbolMatch(
                symbol=row["symbol"],
                name=row.get("longname") or row.get("shortname") or row["symbol"],
                kind=row.get("quoteType"),
                exchange=row.get("exchDisp") or row.get("exchange"),
                source=self.name,
            )
            for row in rows
            if isinstance(row, dict) and row.get("symbol")
        ]
