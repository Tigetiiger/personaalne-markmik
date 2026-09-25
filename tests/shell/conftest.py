from datetime import date

import pytest

from app import util

TODAY = date(2026, 9, 24)  # a Thursday


@pytest.fixture
def today(monkeypatch):
    """Capture resolves dates against this day."""
    monkeypatch.setattr(util, "local_today", lambda: TODAY)
    return TODAY
