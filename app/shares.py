"""Share links: a view or edit link to one note. The URL token is 128 random bits and only its
SHA-256 (auth.token_hash) is stored, so the database alone cannot open a link, and the owner sees
a link's URL only when creating it. Visiting a link goes through auth.enter_share."""

import secrets
from datetime import datetime

from . import auth, images
from .db import tx
from .util import iso, now_iso

MODES = ("view", "edit")


def create(conn, note_id, mode, expires_at=None):
    """New link -> (row, token). expires_at: UTC ISO time in the future, or None (never).
    The pictures the note shows now become ones its guests may load."""
    if mode not in MODES:
        raise ValueError("Tundmatu jagamise viis.")
    if expires_at is not None:
        try:
            when = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError
            expires_at = iso(when)
        except (AttributeError, ValueError, OverflowError):
            raise ValueError("Vigane aegumise aeg.") from None
        if expires_at <= now_iso():
            raise ValueError("Aegumise aeg on möödas.")
    token = secrets.token_urlsafe(16)
    with tx(conn):
        share_id = conn.execute(
            "INSERT INTO shares (token_hash, note_id, mode, expires_at) VALUES (?, ?, ?, ?)",
            (auth.token_hash(token), note_id, mode, expires_at),
        ).lastrowid
        images.allow_shown(conn, note_id)
    return get(conn, share_id), token


def get(conn, share_id):
    return conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()


def list_for_note(conn, note_id):
    """The note's links not revoked, newest first; expired ones too, so the owner sees them end."""
    return conn.execute(
        "SELECT * FROM shares WHERE note_id = ? AND revoked_at IS NULL ORDER BY created_at DESC, id DESC",
        (note_id,),
    ).fetchall()


def edit_open(conn, note_id):
    """Whether guests can change the note now: it has an edit link not revoked and not expired."""
    return conn.execute(
        "SELECT 1 FROM shares WHERE note_id = ? AND mode = 'edit' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
        (note_id, now_iso()),
    ).fetchone() is not None


def revoke(conn, share_id):
    """End a link at once: the next request through it is refused (auth.load_actor)."""
    conn.execute("UPDATE shares SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_iso(), share_id))
