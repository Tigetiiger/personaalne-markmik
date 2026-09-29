"""Alpha Vantage NEWS_SENTIMENT free tier (a second sentiment opinion). Ported from stock-oracle.

GET https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=&time_from=YYYYMMDDTHHMM
    &sort=LATEST&limit=&apikey=
-> {"items", "feed": [{title, url, time_published "YYYYMMDDTHHMMSS", authors[], summary, source,
    overall_sentiment_score, ticker_sentiment: [{ticker, relevance_score, ticker_sentiment_score}]}]}
Free plan: 25 requests a day (the count is kept in `settings`, store.SettingsBudget). Over the
limit the API still answers 200, with an "Information" (or older "Note") message instead of a
feed. The key can only go in the query string: HttpFetcher never logs one."""

import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import BudgetExhausted, DailyBudget, HttpFetcher, RateLimiter, Sleep, SourceError
from .models import DocumentIn, InstrumentRef, is_us_listing
from .text import html_to_text

QUERY_URL = "https://www.alphavantage.co/query"
FREE_DAILY_REQUESTS = 25


def parse_time_published(raw: Any) -> datetime | None:
    # The docs name no time zone; the stamps line up with the linked articles' UTC times.
    # strptime is lenient with digit counts ("0730" would parse as 07:03:00 under %H%M%S), so
    # the format is picked by length.
    formats = {15: "%Y%m%dT%H%M%S", 13: "%Y%m%dT%H%M"}
    if not isinstance(raw, str) or len(raw) not in formats:
        return None
    try:
        return datetime.strptime(raw, formats[len(raw)]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None  # NaN would make meta_json invalid JSON


class AlphaVantageSource:
    name = "alphavantage"

    def __init__(self, client: httpx.Client, *, api_key: str, budget: DailyBudget, limiter: RateLimiter | None = None,
                 sleep: Sleep = time.sleep, limit: int = 50):
        self.api_key = api_key
        self.limit = limit
        self.http = HttpFetcher(client, limiter or RateLimiter(1.5, sleep=sleep), sleep=sleep, budget=budget)

    def covers(self, instrument: InstrumentRef) -> bool:
        """The free tier serves US listings only."""
        return is_us_listing(instrument)

    def fetch(self, instrument: InstrumentRef, since: datetime, now: datetime) -> list[DocumentIn]:
        response = self.http.get(QUERY_URL, params={
            "function": "NEWS_SENTIMENT", "tickers": instrument.ticker,
            "time_from": since.astimezone(UTC).strftime("%Y%m%dT%H%M"), "sort": "LATEST", "limit": self.limit,
            "apikey": self.api_key})
        try:
            data = response.json()
        except ValueError:
            raise SourceError("alphavantage: non-JSON response") from None
        if not isinstance(data, dict):
            raise SourceError("alphavantage: unexpected response")
        if "feed" not in data:
            message = data.get("Information") or data.get("Note") or data.get("Error Message")
            if data.get("Information") or data.get("Note"):
                raise BudgetExhausted(f"alphavantage: {message}")
            raise SourceError(f"alphavantage: {message or 'no feed in response'}")
        docs = []
        for item in data["feed"] if isinstance(data["feed"], list) else []:
            doc = self._document(item, instrument)
            if doc is not None and since <= doc.published_at <= now:
                docs.append(doc)
        return docs

    def _document(self, item: Any, instrument: InstrumentRef) -> DocumentIn | None:
        if not isinstance(item, dict):
            return None
        published = parse_time_published(item.get("time_published"))
        url = item.get("url")
        if published is None or not url:
            return None
        sentiments = [s for s in item.get("ticker_sentiment") or [] if isinstance(s, dict)]
        own = next((s for s in sentiments if s.get("ticker") == instrument.ticker), None)
        tickers = (instrument.ticker, *(s["ticker"] for s in sentiments if s.get("ticker") and s["ticker"] != instrument.ticker))
        authors = [a for a in item.get("authors") or [] if isinstance(a, str) and a]
        return DocumentIn(
            source=self.name, external_id=str(url), url=str(url), title=html_to_text(item.get("title")) or None,
            body=html_to_text(item.get("summary")) or None, author=", ".join(authors) or None, published_at=published,
            instruments=tickers,
            meta={"publisher": item.get("source"), "overall_sentiment": _float(item.get("overall_sentiment_score")),
                  "sentiment": _float(own.get("ticker_sentiment_score")) if own else None,
                  "relevance": _float(own.get("relevance_score")) if own else None})
