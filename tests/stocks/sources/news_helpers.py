"""Helpers for the stocks-sources tests (conftest.py makes fixtures of them): a fake monotonic
clock and a routed httpx.MockTransport. Every file under fixtures/ comes from stock-oracle's tests
(handcrafted after the real responses' shapes, see its tests/unit/sources/conftest.py)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 8, 1, tzinfo=UTC)


def fixture_bytes(name):
    return (FIXTURES / name).read_bytes()


@dataclass
class FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    now: float = 1000.0
    sleeps: list = field(default_factory=list)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class MockHTTP:
    """Routes by scheme://host/path (the query is ignored). A route answers with its responses in
    turn and repeats the last; a response may be an httpx.Response, a callable(request) or an
    exception to raise. Unrouted requests get 404 and are listed in `unmatched`."""

    def __init__(self, clock=None):
        self.routes = {}
        self.calls = []
        self.unmatched = []
        self.stamps = []
        self.clock = clock

    def on(self, url, *responses):
        self.routes[url] = list(responses)
        return self

    def json(self, url, data, status=200):
        return self.on(url, httpx.Response(status, json=data))

    def bytes(self, url, name, status=200):
        return self.on(url, httpx.Response(status, content=fixture_bytes(name)))

    def called(self, url):
        return [r for r in self.calls if f"{r.url.scheme}://{r.url.host}{r.url.path}" == url]

    def handler(self, request):
        self.calls.append(request)
        if self.clock is not None:
            self.stamps.append(self.clock.now)
        key = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        if key not in self.routes:
            self.unmatched.append(str(request.url))
            return httpx.Response(404)
        queue = self.routes[key]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))
