"""Pacing, retries and the daily budget (ported from stock-oracle's test_base.py)."""

from datetime import date
from itertools import pairwise

import httpx
import pytest

from app.stocks.sources.base import BudgetExhausted, DailyBudget, HttpFetcher, RateLimitedError, RateLimiter, SourceError
from app.stocks.sources.store import SettingsBudget

URL = "https://api.example.com/v1/things"


def fetcher(client, clock, **kwargs):
    return HttpFetcher(client, RateLimiter(0.5, clock=clock, sleep=clock.sleep), sleep=clock.sleep, **kwargs)


def test_rate_limiter_spaces_calls(clock):
    limiter = RateLimiter.per_second(10, clock=clock, sleep=clock.sleep)
    stamps = []
    for _ in range(5):
        limiter.acquire()
        stamps.append(clock.now)
    assert [b - a for a, b in pairwise(stamps)] == pytest.approx([0.1] * 4)
    assert len(clock.sleeps) == 4  # the first call never waits


def test_rate_limiter_does_not_sleep_when_time_has_passed(clock):
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.now += 5
    limiter.acquire()
    assert clock.sleeps == []


def test_429_then_success_backs_off_and_retries(http, hclient, clock):
    http.on(URL, httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"ok": 1}))
    assert fetcher(hclient, clock, backoff=2.0).get(URL).json() == {"ok": 1}
    assert len(http.calls) == 3
    # Exponential backoff 2 s, 4 s; the 0.5 s pacing is absorbed by the longer sleeps.
    assert [s for s in clock.sleeps if s >= 1] == [2.0, 4.0]


def test_retry_after_seconds_is_honoured(http, hclient, clock):
    http.on(URL, httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200))
    fetcher(hclient, clock).get(URL)
    assert 7.0 in clock.sleeps


def test_retry_after_http_date_and_cap(http, hclient, clock):
    http.on(URL, httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}),
            httpx.Response(429, headers={"Retry-After": "garbage"}), httpx.Response(200))
    fetcher(hclient, clock, max_wait=30.0, backoff=1.0).get(URL)
    # A far-future date is capped; an unparseable header falls back to backoff (2**1).
    assert [s for s in clock.sleeps if s >= 1] == [30.0, 2.0]


def test_gives_up_after_max_retries(http, hclient, clock):
    http.on(URL, httpx.Response(500))
    with pytest.raises(SourceError, match="after 3 attempts: HTTP 500"):
        fetcher(hclient, clock, max_retries=2).get(URL)
    assert len(http.calls) == 3


def test_client_errors_are_not_retried_and_keys_not_leaked(http, hclient, clock):
    http.on(URL, httpx.Response(401))
    with pytest.raises(SourceError) as info:
        fetcher(hclient, clock).get(URL, params={"apikey": "SECRET123"})
    assert len(http.calls) == 1
    assert "401" in str(info.value) and "SECRET123" not in str(info.value)


def test_transport_errors_are_retried(http, hclient, clock):
    http.on(URL, httpx.ConnectTimeout("slow"), httpx.Response(200))
    assert fetcher(hclient, clock).get(URL).status_code == 200


def test_transport_errors_exhausted_name_only_the_class(http, hclient, clock):
    http.on(URL, httpx.ConnectError("down: https://api.example.com/v1/things?apikey=SECRET123"))
    with pytest.raises(SourceError, match="ConnectError") as info:
        fetcher(hclient, clock, max_retries=1).get(URL, params={"apikey": "SECRET123"})
    assert "SECRET123" not in str(info.value)


def test_default_headers_are_sent_and_merged(http, hclient, clock):
    http.on(URL, httpx.Response(200))
    fetcher(hclient, clock, headers={"User-Agent": "ua a@b.c"}).get(URL, headers={"X-Extra": "1"})
    sent = http.calls[-1].headers
    assert sent["User-Agent"] == "ua a@b.c" and sent["X-Extra"] == "1"


def test_daily_budget_counts_retries_and_resets_next_day(http, hclient, clock):
    day = [date(2026, 9, 21)]
    budget = DailyBudget(3, today=lambda: day[0])
    http.on(URL, httpx.Response(429), httpx.Response(200), httpx.Response(200))
    fetch = fetcher(hclient, clock, budget=budget)
    fetch.get(URL)
    assert budget.remaining == 1
    fetch.get(URL)
    with pytest.raises(BudgetExhausted):
        fetch.get(URL)
    day[0] = date(2026, 9, 22)
    assert budget.remaining == 3


def test_exhausted_429_is_a_rate_limit_and_5xx_is_not(http, hclient, clock):
    http.on(URL, httpx.Response(429))
    with pytest.raises(RateLimitedError):
        fetcher(hclient, clock, max_retries=1).get(URL)
    http.on(URL, httpx.Response(503))
    with pytest.raises(SourceError) as info:
        fetcher(hclient, clock, max_retries=1).get(URL)
    assert not isinstance(info.value, RateLimitedError)


def test_settings_budget_resumes_and_keeps_only_today(conn):
    day = [date(2026, 9, 21)]
    store = SettingsBudget(conn)
    store.save("alphavantage", day[0], 2)
    budget = DailyBudget.stored(3, source="alphavantage", store=SettingsBudget(conn), today=lambda: day[0])
    assert budget.remaining == 1
    budget.spend()
    with pytest.raises(BudgetExhausted):
        budget.spend()
    # A new day starts from zero; the old day's key goes.
    day[0] = date(2026, 9, 22)
    budget.spend()
    keys = [r["key"] for r in conn.execute("SELECT key FROM settings WHERE key LIKE 'stocks:av_calls:%'")]
    assert keys == ["stocks:av_calls:2026-09-22"] and store.load("alphavantage", day[0]) == 1
