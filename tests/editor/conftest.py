"""Fixtures for the editor + collab package (WP-A)."""

import os

import pytest

from app import collab, notes

MARI = {"name": "mari", "is_guest": True}
JAAN = {"name": "jaan", "is_guest": True}


@pytest.fixture(autouse=True)
def _no_presence_between_tests():
    # Presence is process memory; one test's editors must not linger into the next.
    collab._participants.clear()
    collab._touched.clear()


@pytest.fixture
def note(conn):
    return notes.create(conn, "Alus", body="Alus\n")


@pytest.fixture
def guest(app, conn, client):
    """guest(note_id, mode, name) -> a client holding a share link for that note."""
    def make(note_id, mode, name="Külaline"):
        c = type(client)(app.test_client())
        share_id = conn.execute(
            "INSERT INTO shares (token_hash, note_id, mode) VALUES (?, ?, ?)", (os.urandom(16).hex(), note_id, mode)
        ).lastrowid
        with c.c.session_transaction() as s:
            s["share_id"] = share_id
            s["guest_name"] = name
        return c
    return make
