"""Finnhub REST adapter (free tier).

Free tier, as of 2026-09: `/quote` (US real-time) and `/search` work.
`/stock/candle` and `/stock/dividend` answer "You don't have access to this
resource." on the free plan, and so may `/stock/split`. Those responses become
`NotCovered`, so a `FallbackProvider` moves on to Yahoo; if the key is ever
upgraded the same code starts returning data. Ported from stock-oracle.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import httpx

from .ratelimit import RateLimiter
from .types import (
    Bar,
    CorporateAction,
    InstrumentRef,
    NotCovered,
    ProviderError,
    Quote,
    RateLimited,
    SymbolMatch,
    clean_bars,
)

BASE_URL = "https://finnhub.io/api/v1"
NO_ACCESS = "don't have access"


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class FinnhubProvider:
    name = "finnhub"

    def __init__(
        self, api_key: str, client: httpx.Client, limiter: RateLimiter | None = None
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._limiter = limiter or RateLimiter(60, 60.0)

    def _get(self, path: str, params: dict[str, str | int]) -> Any:
        self._limiter.acquire()
        try:
            # Header rather than the `token` query param keeps the key out of URLs and logs.
            response = self._client.get(
                BASE_URL + path, params=params, headers={"X-Finnhub-Token": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"{path}: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise RateLimited(self.name, f"{path}: rate limited", _retry_after(response))
        if response.status_code in (401, 403):
            if NO_ACCESS in response.text:
                raise NotCovered(self.name, f"{path}: not available on this plan")
            raise ProviderError(self.name, f"{path}: HTTP {response.status_code} (bad API key?)")
        if response.status_code != 200:
            raise ProviderError(self.name, f"{path}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, f"{path}: invalid JSON") from exc

    def daily_bars(self, ref: InstrumentRef, start: date, end: date) -> list[Bar]:
        data = self._get(
            "/stock/candle",
            {
                "symbol": ref.symbol_for(self.name),
                "resolution": "D",
                "from": int(datetime.combine(start, time.min, UTC).timestamp()),
                "to": int(datetime.combine(end, time.max, UTC).timestamp()),
            },
        )
        if data.get("s") == "no_data":
            return []
        if data.get("s") != "ok":
            raise ProviderError(self.name, f"/stock/candle: unexpected status {data.get('s')!r}")
        bars = [
            # Daily candles are stamped at 00:00 UTC of the trading date.
            Bar(
                date=datetime.fromtimestamp(t, UTC).date(),
                open=_dec(o),
                high=_dec(h),
                low=_dec(lo),
                close=_dec(c),
                volume=int(v),
                source=self.name,
            )
            for t, o, h, lo, c, v in zip(
                data["t"], data["o"], data["h"], data["l"], data["c"], data["v"], strict=True
            )
        ]
        return clean_bars(bars, start, end)

    def quote(self, ref: InstrumentRef) -> Quote:
        symbol = ref.symbol_for(self.name)
        data = self._get("/quote", {"symbol": symbol})
        # Unknown and non-US symbols come back as all zeros rather than an error.
        if not data.get("c") or not data.get("t"):
            raise NotCovered(self.name, f"no quote for {symbol}")
        return Quote(
            symbol=symbol,
            price=_dec(data["c"]),
            previous_close=_dec(data["pc"]) if data.get("pc") else None,
            as_of=datetime.fromtimestamp(data["t"], UTC),
            source=self.name,
        )

    def corporate_actions(
        self, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        symbol = ref.symbol_for(self.name)
        params: dict[str, str | int] = {
            "symbol": symbol,
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
        # Dividends first: it is the endpoint most likely to be premium, and a
        # half answer (splits without dividends) would be worse than NotCovered.
        dividends = self._get("/stock/dividend", params)
        splits = self._get("/stock/split", params)
        actions = [
            CorporateAction.dividend(
                date.fromisoformat(row["date"]),
                _dec(row["amount"]),
                row.get("currency") or ref.currency,
                self.name,
            )
            for row in dividends
        ]
        actions += [
            CorporateAction.split(
                date.fromisoformat(row["date"]),
                _dec(row["toFactor"]) / _dec(row["fromFactor"]),
                self.name,
            )
            for row in splits
        ]
        return sorted(
            (a for a in actions if start <= a.date <= end), key=lambda a: (a.date, a.kind)
        )

    def search(self, query: str) -> list[SymbolMatch]:
        data = self._get("/search", {"q": query})
        return [
            SymbolMatch(
                symbol=row["symbol"],
                name=row.get("description") or row["symbol"],
                kind=row.get("type") or None,
                source=self.name,
            )
            for row in data.get("result", [])
        ]
