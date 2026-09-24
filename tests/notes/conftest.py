import pytest

from app import security


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    """The login limit (20 a minute per address) is process-wide, and the owner fixture logs in once
    per test: without this the 21st API test in a run gets 429."""
    monkeypatch.setattr(security, "_hits", {})
