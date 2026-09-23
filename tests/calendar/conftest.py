"""Calendar test helpers. File names in this folder start with test_cal_ so they never clash with
another package's test modules (the test folders have no __init__.py)."""

import pytest

from app import events, security


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The login rate limit counts per process (security._hits): the many owner logins here
    must not lock out tests that run after this folder."""
    security._hits.clear()
    yield
    security._hits.clear()


@pytest.fixture
def add(conn):
    """add(title=..., **columns) -> id; a timed event unless all_day/start_date are given."""
    def add(**fields):
        fields.setdefault("title", "Sündmus")
        if not fields.get("all_day"):
            fields.setdefault("start_at", "2026-10-07T07:00:00Z")
        return events.create(conn, fields)
    return add


@pytest.fixture
def note(conn):
    """note(slug, daily_date=None) -> id, written straight to the table (notes.py belongs to WP-B)."""
    def note(slug, daily_date=None, deleted=False):
        return conn.execute(
            "INSERT INTO notes (slug, title, is_daily, daily_date, deleted_at) VALUES (?, ?, ?, ?, ?)",
            (slug, slug.capitalize(), int(daily_date is not None), daily_date, "2026-01-01T00:00:00Z" if deleted else None),
        ).lastrowid
    return note


@pytest.fixture
def task(conn):
    def task(note_id, text, due_date, done=False, due_time=None):
        return conn.execute(
            "INSERT INTO tasks (note_id, line_key, text, done, due_date, due_time) VALUES (?, ?, ?, ?, ?, ?)",
            (note_id, f"{text}:{due_date}", text, int(done), due_date, due_time),
        ).lastrowid
    return task
