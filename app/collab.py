"""Live co-editing: per-note sessions whose ot-json1 operations the server only sequences.

The server never interprets operations. Every editor starts from the session's base text (Muya
always parses it to the same state), applies every operation in seq order and transforms its own
unsent changes against new ones (tools/editor/collab.js). An operation is accepted only from a
sender who has seen all earlier ones (base == last seq); anyone else gets the newer operations
and sends theirs again, transformed.

A session is valid while closed = 0 and notes.rev = note_rev. Its own saves move note_rev along
with the note; any other write bumps rev, so its editors get {"reset": true} and rejoin with the
new text.

Busy (is_busy): a valid session with an op or save in the last ACTIVE_S seconds, or with ops not
saved yet. Server-side writers (tasks, AI, git import) leave a busy note alone. An editor that is
merely open does not make the note busy: an outside write then resets it and it rejoins.
"""

import json
import logging
import time
from datetime import timedelta

from .db import tx
from .jobs import job
from .util import iso, now_iso, now_utc

log = logging.getLogger(__name__)

ACTIVE_S = 90
GONE_S = 30  # a client not heard from for this long no longer counts as a participant
TOUCH_S = 10  # last_active_at is written at most this often per session
MAX_OP = 64 * 1024  # bytes of JSON; a paste of a whole book is one op, and every client replays it
KEEP_DAYS = 7

# Who is in which session and where their caret is: {session_id: {client: (name, last seen,
# cursor)}}. In memory: it is only the present moment, and gunicorn runs one worker.
_participants = {}
_touched = {}  # {session_id: monotonic time last_active_at was last written}


class OpTooLarge(ValueError):
    pass


def get(conn, session_id):
    return conn.execute("SELECT * FROM collab_sessions WHERE id = ?", (session_id,)).fetchone()


def last_seq(conn, session_id):
    return conn.execute("SELECT coalesce(max(seq), 0) FROM collab_ops WHERE session_id = ?", (session_id,)).fetchone()[0]


def _valid(conn, row):
    if row is None or row["closed"]:
        return False
    note = conn.execute("SELECT rev FROM notes WHERE id = ? AND deleted_at IS NULL", (row["note_id"],)).fetchone()
    return note is not None and note["rev"] == row["note_rev"]


def _active(session_id, now=None):
    now = time.monotonic() if now is None else now
    clients = _participants.get(session_id, {})
    for client in [c for c, (_, seen, _) in clients.items() if now - seen > GONE_S]:
        del clients[client]
    return clients


def participants(session_id):
    return sorted({name for name, _, _ in _active(session_id).values()})


def cursors(session_id, client):
    """Other editors' carets: [{client, name, cursor}], cursor = {path, start, end} or None."""
    return [
        {"client": c, "name": name, "cursor": cursor}
        for c, (name, _, cursor) in sorted(_active(session_id).items())
        if c != client
    ]


def _touch(conn, session_id):
    now = time.monotonic()
    if now - _touched.get(session_id, -TOUCH_S) >= TOUCH_S:
        _touched[session_id] = now
        conn.execute("UPDATE collab_sessions SET last_active_at = ? WHERE id = ?", (now_iso(), session_id))


def _forget(session_id):
    _participants.pop(session_id, None)
    _touched.pop(session_id, None)


def is_busy(conn, note_id):
    cutoff = iso(now_utc() - timedelta(seconds=ACTIVE_S))
    row = conn.execute(
        """SELECT 1 FROM collab_sessions s JOIN notes n ON n.id = s.note_id
           WHERE s.note_id = ? AND s.closed = 0 AND s.note_rev = n.rev
             AND (s.last_active_at > ?
                  OR (SELECT coalesce(max(seq), 0) FROM collab_ops WHERE session_id = s.id) > s.saved_seq)
           LIMIT 1""",
        (note_id, cutoff),
    ).fetchone()
    return row is not None


def close_all(conn, note_id):
    """After an outside write: editors get 'reset' on their next sync and rejoin."""
    for (session_id,) in conn.execute(
        "SELECT id FROM collab_sessions WHERE note_id = ? AND closed = 0", (note_id,)
    ).fetchall():
        _forget(session_id)
    conn.execute("UPDATE collab_sessions SET closed = 1 WHERE note_id = ? AND closed = 0", (note_id,))


def close(conn, session_id):
    """End one session, e.g. when an operation broke a client: everyone rejoins from the last
    saved text instead of carrying on with documents that may have drifted apart."""
    _forget(session_id)
    conn.execute("UPDATE collab_sessions SET closed = 1 WHERE id = ?", (session_id,))


def join(conn, note_id, client, actor):
    """Join the note's running session, or start a new one from the note's current text.

    A running session is joined while it is valid and someone is in it or it has unsaved ops, so
    unfinished work of a tab that died is not lost. Returns None for a missing note."""
    with tx(conn):
        note = conn.execute("SELECT body, rev FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()
        if note is None:
            return None
        row = conn.execute(
            "SELECT * FROM collab_sessions WHERE note_id = ? AND closed = 0 ORDER BY id DESC LIMIT 1", (note_id,)
        ).fetchone()
        if row is not None and _valid(conn, row) and (_active(row["id"]) or last_seq(conn, row["id"]) > row["saved_seq"]):
            return _joined(conn, row, client, actor, note["rev"])
        close_all(conn, note_id)
        cur = conn.execute(
            "INSERT INTO collab_sessions (note_id, base, note_rev, last_active_at) VALUES (?, ?, ?, ?)",
            (note_id, note["body"], note["rev"], now_iso()),
        )
        return _joined(conn, get(conn, cur.lastrowid), client, actor, note["rev"])


def _joined(conn, row, client, actor, rev):
    _participants.setdefault(row["id"], {})[client] = (actor["name"], time.monotonic(), None)
    return {
        "session": row["id"],
        "base": row["base"],
        "ops": _ops_after(conn, row["id"], 0),
        "saved_seq": row["saved_seq"],
        "participants": participants(row["id"]),
        "cursors": cursors(row["id"], client),
        "rev": rev,
    }


def _ops_after(conn, session_id, seq):
    rows = conn.execute(
        "SELECT * FROM collab_ops WHERE session_id = ? AND seq > ? ORDER BY seq", (session_id, seq)
    ).fetchall()
    return [
        {"seq": r["seq"], "op": json.loads(r["op"]), "client": r["client"], "op_id": r["op_id"], "author": r["author"]}
        for r in rows
    ]


def sync(conn, session_id, client, actor, seen, op=None, op_id="", base=None, cursor=None):
    """One poll: take the client's operation (if it has seen every earlier one) and hand back
    every operation after `seen`, its own included (that is how it learns it was accepted).
    cursor: the client's caret after all `seen` operations; the others see it."""
    if op is None:
        row = get(conn, session_id)
        if not _valid(conn, row):
            return {"reset": True}
    else:
        encoded = json.dumps(op, separators=(",", ":"))
        if len(encoded.encode()) > MAX_OP:
            raise OpTooLarge
        with tx(conn):
            row = get(conn, session_id)
            if not _valid(conn, row):
                return {"reset": True}
            already = conn.execute(
                "SELECT 1 FROM collab_ops WHERE session_id = ? AND client = ? AND op_id = ?", (session_id, client, op_id)
            ).fetchone()
            # Already there: a resend after a lost response. Stored once.
            if not already and base == last_seq(conn, session_id):
                conn.execute(
                    "INSERT INTO collab_ops (session_id, seq, op, client, op_id, author) VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, base + 1, encoded, client, op_id, actor["name"]),
                )
                _touch(conn, session_id)
    _participants.setdefault(session_id, {})[client] = (actor["name"], time.monotonic(), cursor)
    return {
        "ops": _ops_after(conn, session_id, seen),
        "saved_seq": row["saved_seq"],
        "participants": participants(session_id),
        "cursors": cursors(session_id, client),
    }


def leave(session_id, client):
    _participants.get(session_id, {}).pop(client, None)


def save(conn, session_id, body, seq, actor):
    """Write the session's text at `seq` to the note. False when the session is no longer valid
    (the client resets). Only a newer seq writes: an equal or older one comes from a slower
    client or a late request and changes nothing."""
    with tx(conn):
        row = get(conn, session_id)
        if not _valid(conn, row):
            return False
        # No further than the server's own log: a larger claim would mark unsent ops saved.
        seq = min(seq, last_seq(conn, session_id))
        if seq <= row["saved_seq"]:
            return True
        now = now_iso()
        conn.execute("UPDATE notes SET body = ?, rev = rev + 1, updated_at = ? WHERE id = ?", (body, now, row["note_id"]))
        rev = conn.execute("SELECT rev FROM notes WHERE id = ?", (row["note_id"],)).fetchone()["rev"]
        conn.execute(
            "UPDATE collab_sessions SET note_rev = ?, saved_seq = ?, last_active_at = ? WHERE id = ?",
            (rev, seq, now, session_id),
        )
        _touched[session_id] = time.monotonic()
        from . import notes
        notes.on_saved(conn, row["note_id"])
    return True


@job("collab-prune", every=3600)
def prune(conn):
    """Op logs of sessions closed a week ago: nobody can rejoin them."""
    cutoff = iso(now_utc() - timedelta(days=KEEP_DAYS))
    conn.execute(
        "DELETE FROM collab_ops WHERE session_id IN (SELECT id FROM collab_sessions WHERE closed = 1 AND last_active_at < ?)",
        (cutoff,),
    )
