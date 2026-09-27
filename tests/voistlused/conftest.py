"""Fixtures for the competition finder. Nothing here reaches the network: the teaduskool CSV is a
saved copy (tests/fixtures/teaduskool), candidate pages are strings, and the model is the fake
runner with canned discovery and extraction answers. File names start with test_voistlused_ so
they never clash with another package's test modules."""

from datetime import date
from pathlib import Path

import pytest

from app import eligibility, ics, voistlusallikad, voistlused
from app.ai import runner
from app.sources import teaduskool

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "teaduskool"
FEEDS = Path(__file__).resolve().parent.parent / "fixtures" / "voistlused"
CSV = (FIXTURES / "kalender.csv").read_bytes()
EMPTY = {"ics": b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n", "rss": b"<rss><channel></channel></rss>",
         "html": b""}
TODAY = date(2026, 9, 27)  # the day the saved CSV was fetched


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(voistlused, "local_today", lambda: TODAY)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(url, *a, **kw):
        raise AssertionError(f"unexpected download of {url}")
    monkeypatch.setattr(ics, "download", refuse)


class World:
    """The finder's outside world. `csv`: what the teaduskool fetch returns (bytes, or an
    Exception to raise); `candidates`: the discovery answer; `pages`: url -> (text, events) for
    the fetch and the extraction answer; `facts`: url -> the facts extraction's answer;
    `sources`: a known source's key -> its body (bytes, or an Exception), empty when not given.
    `fetched`, `calls` record what happened: calls are {"schema", "prompt", "system",
    "web_search", "model"}."""

    def __init__(self):
        self.csv = CSV
        self.candidates = []
        self.pages = {}
        self.facts = {}
        self.sources = {}
        self.read_sources = []
        self.fetched = []
        self.calls = []

    def fetch_csv(self):
        if isinstance(self.csv, Exception):
            raise self.csv
        return self.csv

    def fetch_source(self, url):
        src = next(s for s in voistlusallikad.SOURCES if s.url == url)
        self.read_sources.append(src.key)
        body = self.sources.get(src.key, EMPTY[src.kind])
        if isinstance(body, Exception):
            raise body
        return body

    def feed(self, key, name):
        """A saved real feed (tests/fixtures/voistlused/<name>) as source `key`."""
        body = (FEEDS / name).read_bytes()
        self.sources[key] = body

    def fetch_page(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise ValueError("Lehte ei saanud alla laadida.")
        return self.pages[url][0]

    def answer(self, prompt, schema, model):
        if schema is voistlused.DISCOVERY_SCHEMA:
            return {"candidates": self.candidates}
        if schema is eligibility.FACTS_SCHEMA:
            return next((f for url, f in self.facts.items() if f"\n{url}\n" in prompt), self.fact())
        return {"events": next((events for url, (_, events) in self.pages.items() if f"\n{url}\n" in prompt), [])}

    def page(self, url, text, *events):
        self.pages[url] = (text, list(events))

    def saved(self, url, name, **facts):
        """A saved real page (tests/fixtures/teaduskool/pages/<name>.txt) at url, with facts."""
        self.page(url, (FIXTURES / "pages" / f"{name}.txt").read_text())
        if facts:
            self.facts[url] = self.fact(**facts)

    @staticmethod
    def fact(**fields):
        """A facts answer: nothing known unless given."""
        return {**dict.fromkeys(eligibility.FACTS_PROPERTIES), **fields}

    def candidate(self, url, title="Võistlus", why="Sobib huvidega."):
        self.candidates.append({"title": title, "url": url, "why": why})

    ROBOTEX = """Robotex International 2026
Robotex International toimub 27.–29. novembril 2026 Tallinnas.
Registreerimine on avatud kuni 1. novembrini 2026.
Kategooriad: sumo, joonejärgimine, Lego."""

    @staticmethod
    def event(**fields):
        """One event of an extraction answer; what is not given is a valid find on ROBOTEX."""
        return {"title": "Robotex International 2026", "date_text": "27.–29. novembril 2026",
                "start_date": "2026-11-27", "end_date": "2026-11-29", "registration_text": None,
                "registration_deadline": None, "location": "Tallinn",
                "source_quote": "Robotex International toimub 27.–29. novembril 2026 Tallinnas.", "confidence": "high",
                **dict.fromkeys(eligibility.FACTS_PROPERTIES), **fields}


@pytest.fixture
def world(monkeypatch):
    w = World()
    real = runner.run_structured

    def recorded(conn, prompt, schema, *, model="haiku", system=None, web_search=False):
        w.calls.append({"schema": schema, "prompt": prompt, "system": system, "web_search": web_search, "model": model})
        return real(conn, prompt, schema, model=model, system=system, web_search=web_search)

    monkeypatch.setattr(teaduskool, "fetch", w.fetch_csv)
    monkeypatch.setattr(voistlused, "fetch_page", w.fetch_page)
    monkeypatch.setattr(voistlusallikad, "download", w.fetch_source)
    monkeypatch.setattr(runner, "fake_structured", w.answer)
    monkeypatch.setattr(runner, "run_structured", recorded)
    return w
