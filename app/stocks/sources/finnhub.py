"""Finnhub free tier: company news and the earnings calendar. Ported from stock-oracle.

Endpoints (https://finnhub.io/docs/api):
- GET /api/v1/company-news?symbol=&from=YYYY-MM-DD&to=YYYY-MM-DD
- GET /api/v1/calendar/earnings?from=&to=&symbol=
Free tier: 60 calls a minute. The key goes in the X-Finnhub-Token header so it never appears in
a URL."""

import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .base import HttpFetcher, RateLimiter, Sleep, SourceError
from .models import DocumentIn, EarningsItem, InstrumentRef, is_us_listing
from .text import from_unix, html_to_text

BASE_URL = "https://finnhub.io/api/v1"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _json_list(response: httpx.Response, key: str | None = None) -> list[Any]:
    try:
        data = response.json()
    except ValueError:
        raise SourceError("finnhub: non-JSON response") from None
    if key is not None:
        data = data.get(key) if isinstance(data, dict) else None
    if not isinstance(data, list):
        raise SourceError(f"finnhub: expected a list, got {type(data).__name__}")
    return data


class FinnhubSource:
    name = "finnhub"

    def __init__(self, client: httpx.Client, *, api_key: str, limiter: RateLimiter | None = None,
                 sleep: Sleep = time.sleep):
        self.http = HttpFetcher(client, limiter or RateLimiter(1.0, sleep=sleep), sleep=sleep,
                                headers={"X-Finnhub-Token": api_key})

    def covers(self, instrument: InstrumentRef) -> bool:
        """The free tier serves US listings only (European tickers get HTTP 403)."""
        return is_us_listing(instrument) and instrument.symbol_at("finnhub") is not None

    def fetch(self, instrument: InstrumentRef, since: datetime, now: datetime) -> list[DocumentIn]:
        response = self.http.get(f"{BASE_URL}/company-news", params={
            "symbol": instrument.symbol_at("finnhub"), "from": since.date().isoformat(), "to": now.date().isoformat()})
        docs = []
        for item in _json_list(response):
            doc = self._document(instrument, item)
            if doc is not None and since <= doc.published_at <= now:
                docs.append(doc)
        return docs

    def _document(self, instrument: InstrumentRef, item: Any) -> DocumentIn | None:
        if not isinstance(item, dict):
            return None
        stamp = item.get("datetime")
        if not isinstance(stamp, int | float) or isinstance(stamp, bool) or stamp <= 0:
            return None
        headline = html_to_text(item.get("headline")) or None
        url = item.get("url") or None
        if not headline and not url:
            return None
        related = [t.strip() for t in str(item.get("related") or "").split(",") if t.strip()]
        tickers = (instrument.ticker, *(t for t in related if t != instrument.ticker))
        return DocumentIn(source=self.name, external_id=str(item.get("id") or url), url=url, title=headline,
                          body=html_to_text(item.get("summary")) or None, author=None, published_at=from_unix(stamp),
                          instruments=tickers, meta={"publisher": item.get("source"), "category": item.get("category")})

    def earnings_calendar(self, instrument: InstrumentRef, start: date, end: date) -> list[EarningsItem]:
        response = self.http.get(f"{BASE_URL}/calendar/earnings", params={
            "symbol": instrument.symbol_at("finnhub"), "from": start.isoformat(), "to": end.isoformat()})
        items = []
        for row in _json_list(response, "earningsCalendar"):
            if not isinstance(row, dict):
                continue
            try:
                day = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            quarter, year = row.get("quarter"), row.get("year")
            hour = row.get("hour")
            items.append(EarningsItem(
                ticker=instrument.ticker, day=day, time_of_day=hour if hour in ("bmo", "amc") else None,
                fiscal_period=f"{year}Q{quarter}" if quarter and year else None,
                eps_estimate=_decimal(row.get("epsEstimate")), eps_actual=_decimal(row.get("epsActual")),
                revenue_estimate=_decimal(row.get("revenueEstimate")), revenue_actual=_decimal(row.get("revenueActual")),
                source=self.name))
        return items
