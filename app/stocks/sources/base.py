"""What every adapter shares: request pacing, the free-tier daily quota, and an HTTP wrapper that
retries 429/5xx with backoff. Ported from stock-oracle's sources/base.py.

Clocks and sleep functions are injected so tests can check pacing without waiting and without
the wall clock."""

import logging
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from ...util import now_utc

log = logging.getLogger(__name__)

Sleep = Callable[[float], None]
Monotonic = Callable[[], float]


class SourceError(Exception):
    """A source could not deliver (after retries); the run records it and moves on."""


class BudgetExhausted(SourceError):
    """A free-tier daily quota is used up."""


class RateLimitedError(SourceError):
    """Still throttled (HTTP 429) after every retry: stop asking for a while."""


class RateLimiter:
    """Spaces calls at least `min_interval` seconds apart."""

    def __init__(self, min_interval: float, *, clock: Monotonic = time.monotonic, sleep: Sleep = time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    @classmethod
    def per_second(cls, rate: float, *, clock: Monotonic = time.monotonic, sleep: Sleep = time.sleep):
        return cls(1.0 / rate, clock=clock, sleep=sleep)

    def acquire(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self.min_interval


class BudgetStore(Protocol):
    """Where DailyBudget counts are kept between processes (store.SettingsBudget)."""

    def load(self, source: str, day: date) -> int: ...

    def save(self, source: str, day: date, used: int) -> None: ...


class DailyBudget:
    """A free-tier daily request cap. The count lives in memory; pass `used` to carry it over a
    restart within the same day, and `on_spend` to persist every spend."""

    def __init__(self, limit: int, *, today: Callable[[], date], used: int = 0,
                 on_spend: Callable[[date, int], None] | None = None):
        self.limit = limit
        self._today = today
        self._day = today()
        self.used = used
        self._on_spend = on_spend

    @classmethod
    def stored(cls, limit: int, *, source: str, store: BudgetStore, today: Callable[[], date]):
        """A budget that starts from, and writes every spend to, `store`."""
        return cls(limit, today=today, used=store.load(source, today()),
                   on_spend=lambda day, used: store.save(source, day, used))

    @property
    def remaining(self) -> int:
        self._roll()
        return max(self.limit - self.used, 0)

    def spend(self) -> None:
        self._roll()
        if self.used >= self.limit:
            raise BudgetExhausted(f"daily budget of {self.limit} requests used up")
        self.used += 1
        if self._on_spend is not None:
            self._on_spend(self._day, self.used)

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self.used = 0


RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_after(response: httpx.Response, now: datetime) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max((when - now).total_seconds(), 0.0)


class HttpFetcher:
    """GET with pacing and retries.

    Retries 429, 5xx and transport errors with exponential backoff (honouring Retry-After up to
    `max_wait`), then raises SourceError. Other 4xx raise SourceError at once: retrying a bad key
    or a bad request never helps. Messages carry the URL without its query (keys travel there)."""

    def __init__(self, client: httpx.Client, limiter: RateLimiter, *, sleep: Sleep = time.sleep,
                 headers: Mapping[str, str] | None = None, max_retries: int = 4, backoff: float = 1.0,
                 max_wait: float = 120.0, budget: DailyBudget | None = None):
        self.client = client
        self.limiter = limiter
        self.sleep = sleep
        self.headers = dict(headers or {})
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_wait = max_wait
        self.budget = budget

    def get(self, url: str, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None) -> httpx.Response:
        merged = {**self.headers, **(headers or {})}
        attempt = 0
        while True:
            if self.budget is not None:
                self.budget.spend()
            self.limiter.acquire()
            wait: float | None
            try:
                response = self.client.get(url, params=params, headers=merged)
            except httpx.TransportError as exc:
                # The exception's text may quote the request; only its class is kept.
                problem = type(exc).__name__
                wait = None
            else:
                if response.status_code < 400:
                    return response
                if response.status_code not in RETRY_STATUSES:
                    raise SourceError(f"GET {_redact(url)} -> HTTP {response.status_code}")
                problem = f"HTTP {response.status_code}"
                wait = _retry_after(response, now_utc())
            if attempt >= self.max_retries:
                error = RateLimitedError if problem == "HTTP 429" else SourceError
                raise error(f"GET {_redact(url)} failed after {attempt + 1} attempts: {problem}")
            if wait is None:
                wait = self.backoff * (2**attempt)
            wait = min(wait, self.max_wait)
            log.warning("GET %s: %s, retrying in %.1fs", _redact(url), problem, wait)
            self.sleep(wait)
            attempt += 1


def _redact(url: str) -> str:
    # Query strings carry API keys (apikey=); never log them.
    return url.split("?", 1)[0]
