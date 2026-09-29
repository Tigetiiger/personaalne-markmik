"""ECB reference rates from Frankfurter (v2 API, restricted to the ECB provider).

The ECB publishes on TARGET2 business days only. Asked for a weekend or a
holiday, Frankfurter answers with the last business day before it, and a
range starts at the last business day on or before `start`, so `rate_on`
always finds a rate for any day inside a fetched range. Ported from stock-oracle.
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from .ratelimit import RateLimiter
from .types import NotCovered, ProviderError, RateLimited

BASE_URL = "https://api.frankfurter.dev/v2"
SOURCE = "frankfurter"


@dataclass(frozen=True)
class FxPoint:
    """1 `base` = `rate` `quote` on `date` (the ECB publication date)."""

    date: date
    base: str
    quote: str
    rate: Decimal


def rate_on(points: Sequence[FxPoint], day: date) -> FxPoint:
    """The point for `day`, or the last one before it. `points` sorted by date."""
    index = bisect.bisect_right([p.date for p in points], day)
    if index == 0:
        raise NotCovered(SOURCE, f"no rate on or before {day}")
    return points[index - 1]


class FrankfurterFX:
    def __init__(self, client: httpx.Client, limiter: RateLimiter | None = None) -> None:
        self._client = client
        self._limiter = limiter or RateLimiter(10, 1.0)

    def _get(self, params: dict[str, str]) -> list[FxPoint]:
        self._limiter.acquire()
        try:
            response = self._client.get(BASE_URL + "/rates", params={**params, "providers": "ECB"})
        except httpx.HTTPError as exc:
            raise ProviderError(SOURCE, type(exc).__name__) from exc
        if response.status_code == 429:
            raise RateLimited(SOURCE, "rate limited")
        if response.status_code == 422:
            raise NotCovered(SOURCE, _message(response))
        if response.status_code != 200:
            raise ProviderError(SOURCE, f"HTTP {response.status_code}")
        try:
            rows: list[dict[str, Any]] = response.json()
            points = [
                FxPoint(
                    date=date.fromisoformat(row["date"]),
                    base=row["base"],
                    quote=row["quote"],
                    rate=Decimal(str(row["rate"])),
                )
                for row in rows
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(SOURCE, f"unexpected response: {exc}") from exc
        return sorted(points, key=lambda p: p.date)

    def rate(self, day: date, base: str, quote: str) -> FxPoint:
        """The rate for `day`; on a weekend or holiday, the last business day's."""
        if base == quote:
            return FxPoint(day, base, quote, Decimal(1))
        points = self._get({"date": day.isoformat(), "base": base, "quotes": quote})
        return rate_on(points, day)

    def rates(self, start: date, end: date, base: str, quote: str) -> list[FxPoint]:
        """Every published rate from the last business day on or before `start` to `end`."""
        return self._get(
            {"from": start.isoformat(), "to": end.isoformat(), "base": base, "quotes": quote}
        )


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json()["message"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {response.status_code}"
