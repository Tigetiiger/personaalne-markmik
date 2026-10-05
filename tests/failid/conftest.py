"""Fixtures for the data file store (docs/FAILID-PLAN.md, wp/failid-store)."""

import os

import pytest

from app import shares


@pytest.fixture
def guest(app, conn, client):
    """guest(note_id, mode="view") -> a browser holding a share link for that note."""
    def make(note_id, mode="view"):
        c = type(client)(app.test_client())
        share_id = shares.create(conn, note_id, mode)[0]["id"]
        with c.c.session_transaction() as s:
            s["share_id"] = share_id
            s["guest_name"] = "Mari"
            s["csrf"] = os.urandom(16).hex()
        return c
    return make
