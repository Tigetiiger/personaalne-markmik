"""Fixtures for the stocks-ai tests; the helpers behind them are in ai_helpers.py. No network: the
model is AI_FAKE with a scripted runner.fake_structured, pages come from a dict instead of
ics.download."""

import pytest
from ai_helpers import FakeModel, FakePages

from app import ics
from app.ai import runner


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    real = runner.run_structured

    def spy(conn, prompt, schema, **kw):
        fake.calls.append({"prompt": prompt, **kw})
        return real(conn, prompt, schema, **kw)

    monkeypatch.setattr(runner, "run_structured", spy)
    monkeypatch.setattr(runner, "fake_structured", fake)
    return fake


@pytest.fixture
def pages(monkeypatch):
    fake = FakePages()
    monkeypatch.setattr(ics, "download", fake)
    return fake
