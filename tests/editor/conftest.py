"""Fixtures for the editor + collab package (WP-A)."""

import os

import pytest

from app import collab, notes, security, shares

MARI = {"name": "mari", "is_guest": True}
JAAN = {"name": "jaan", "is_guest": True}


@pytest.fixture(autouse=True)
def _no_presence_between_tests():
    # Presence and rate limits are process memory; one test's must not reach into the next.
    for store in (collab._participants, collab._numbers, collab._last_seen, collab._touched, security._hits):
        store.clear()


@pytest.fixture
def note(conn):
    return notes.create(conn, "Alus", body="# Alus\n")


@pytest.fixture
def guest(app, conn, client):
    """guest(note_id, mode, name) -> a client holding a share link for that note."""
    def make(note_id, mode, name="Külaline"):
        c = type(client)(app.test_client())
        share_id = shares.create(conn, note_id, mode)[0]["id"]
        with c.c.session_transaction() as s:
            s["share_id"] = share_id
            s["guest_name"] = name
            s["csrf"] = os.urandom(16).hex()  # a browser of its own
        return c
    return make
