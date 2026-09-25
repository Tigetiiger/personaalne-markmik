"""Fixtures for the sharing package (WP-G)."""

import os

import pytest

from app import collab, notes, shares


@pytest.fixture(autouse=True)
def _no_presence_between_tests():
    # Collab presence is process memory; one test's must not reach into the next.
    for store in (collab._participants, collab._numbers, collab._last_seen, collab._touched):
        store.clear()


@pytest.fixture
def note(conn):
    return notes.create(conn, "Jagatud", body="# Jagatud\n\nVaata [teist](/m/teine) ja [välja](https://example.com/a).\n")


@pytest.fixture
def other(conn):
    return notes.create(conn, "Teine", body="# Teine\n\nSalajane jutt.\n")


@pytest.fixture
def link(conn):
    """link(note_id, mode, expires_at=None) -> (share row, token)."""
    return lambda note_id, mode, expires_at=None: shares.create(conn, note_id, mode, expires_at)


@pytest.fixture
def browser(app, client):
    """browser(share_id=None) -> a new anonymous browser, holding that share when given (as
    /j/<token> leaves it; also for revoked or expired shares, which /j/ would refuse)."""
    def make(share_id=None, name="Mari"):
        b = type(client)(app.test_client())
        with b.c.session_transaction() as s:
            s["csrf"] = os.urandom(16).hex()
            if share_id is not None:
                s["share_id"] = share_id
                s["guest_name"] = name
        return b
    return make


@pytest.fixture
def expire(conn):
    """expire(share_id): the link's time ran out (create() refuses a past expiry)."""
    return lambda share_id: conn.execute("UPDATE shares SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (share_id,))
