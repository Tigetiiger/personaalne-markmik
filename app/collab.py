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
MAX_DEPTH = 64  # nesting of an op's descents; Muya's deepest is a list in a quote in a list...
MAX_CLIENTS = 30  # editor tabs in one session; past it only view guests are turned away
MAX_CLIENTS_PER_ACTOR = 6  # tabs of one browser session; the oldest makes room (reloads)
MAX_CLIENTS_PER_SHARE = 10  # tabs through one share link, however many browsers open it
KEEP_DAYS = 7

# ot-json1 component keys: pick, remove, drop, insert, edit (+ its type), text edit, number add.
_COMPONENT_KEYS = {"p", "r", "d", "i", "e", "et", "es", "ena"}

# Who is in which session and where their caret is: {session_id: {client: {"name", "seen",
# "cursor", "id"}}}. A client id is "<browser key>:<tab id>" (app/views/collab.py), so a browser
# can only act for its own tabs; others see a small number ("id") instead. In memory: it is only
# the present moment, and gunicorn runs one worker.
_participants = {}
_numbers = {}  # {session_id: last "id" handed out}
_last_seen = {}  # {session_id: monotonic time anyone was last heard from}
_touched = {}  # {session_id: monotonic time last_active_at was last written}


class OpTooLarge(ValueError):
    pass


class BadOp(ValueError):
    """Not an ot-json1 operation, or not plain JSON (NaN, Infinity)."""


class TooManyClients(Exception):
    pass


def _well_formed(op, depth=0):
    if depth > MAX_DEPTH or not isinstance(op, list):
        return False
    for part in op:
        if isinstance(part, bool) or part is None:
            return False
        if isinstance(part, int):
            if part < 0:
                return False
        elif isinstance(part, dict):
            if not part or not set(part) <= _COMPONENT_KEYS:
                return False
        elif isinstance(part, list):
            if not _well_formed(part, depth + 1):
                return False
        elif not isinstance(part, str):
            return False
    return True


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
    for client in [c for c, p in clients.items() if now - p["seen"] > GONE_S]:
        del clients[client]
    return clients


def _present(session_id, client, actor, cursor=None):
    """Record that the client is here (and where its caret is). actor: {"name", and for a
    guest "role" ("view" | "edit") and "share" (share id)}. The owner and edit guests are never
    turned away: a full session makes room by dropping its longest-idle view guest."""
    now = time.monotonic()
    clients = _active(session_id, now)
    role, share = actor.get("role", "owner"), actor.get("share")
    if client not in clients:
        browser = client.split(":", 1)[0]

        def drop_oldest(match):
            found = sorted((p["seen"], c) for c, p in clients.items() if match(c, p))
            if found:
                del clients[found[0][1]]
            return bool(found)

        # A browser's new tab (a reload, most often) takes the place of its oldest one.
        own = [c for c in clients if c.split(":", 1)[0] == browser]
        if len(own) >= MAX_CLIENTS_PER_ACTOR or (own and len(clients) >= MAX_CLIENTS):
            drop_oldest(lambda c, p: c.split(":", 1)[0] == browser)
        if share is not None and sum(p["share"] == share for p in clients.values()) >= MAX_CLIENTS_PER_SHARE:
            drop_oldest(lambda c, p: p["share"] == share)
        if len(clients) >= MAX_CLIENTS and not (role != "view" and drop_oldest(lambda c, p: p["role"] == "view")):
            if role == "view":
                raise TooManyClients
        _numbers[session_id] = _numbers.get(session_id, 0) + 1
        clients[client] = {"id": _numbers[session_id], "role": role, "share": share}
    clients[client].update(name=actor["name"], seen=now, cursor=cursor)
    _participants[session_id] = clients
    _last_seen[session_id] = now


def participants(session_id):
    return sorted({p["name"] for p in _active(session_id).values()})


def cursors(session_id, client):
    """Other editors' carets: [{id, name, cursor}], cursor = {path, start, end} or None."""
    return [
        {"id": p["id"], "name": p["name"], "cursor": p["cursor"]}
        for c, p in sorted(_active(session_id).items(), key=lambda item: item[1]["id"])
        if c != client
    ]


def _touch(conn, session_id):
    now = time.monotonic()
    if now - _touched.get(session_id, -TOUCH_S) >= TOUCH_S:
        _touched[session_id] = now
        conn.execute("UPDATE collab_sessions SET last_active_at = ? WHERE id = ?", (now_iso(), session_id))


def _forget(session_id):
    for store in (_participants, _numbers, _last_seen, _touched):
        store.pop(session_id, None)


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

    A running session is joined while it is valid and someone is in it (or was a moment ago: a
    reload), or it has unsaved ops, so unfinished work of a tab that died is not lost. Returns
    None for a missing note. TooManyClients when the session is full."""
    with tx(conn):
        note = conn.execute("SELECT body, rev FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()
        if note is None:
            return None
        row = conn.execute(
            "SELECT * FROM collab_sessions WHERE note_id = ? AND closed = 0 ORDER BY id DESC LIMIT 1", (note_id,)
        ).fetchone()
        recent = row is not None and time.monotonic() - _last_seen.get(row["id"], -GONE_S) < GONE_S
        if row is not None and _valid(conn, row) and (recent or last_seq(conn, row["id"]) > row["saved_seq"]):
            return _joined(conn, row, client, actor, note["rev"])
        close_all(conn, note_id)
        cur = conn.execute(
            "INSERT INTO collab_sessions (note_id, base, note_rev, last_active_at) VALUES (?, ?, ?, ?)",
            (note_id, note["body"], note["rev"], now_iso()),
        )
        return _joined(conn, get(conn, cur.lastrowid), client, actor, note["rev"])


def copy_state(conn, note_id, client, op_id):
    """For changes a tab kept in its browser when it went away (tools/editor/collab.js keepCopy):
    first: the op it had on its way is in what the note shows (saved, or in the live session);
    all: its last save, with the changes queued behind that op, was written (a final save: the
    session ended at that op). Asked before joining, which may close the session."""
    row = conn.execute(
        """SELECT o.seq, s.saved_seq, s.closed, s.note_rev = n.rev AS current,
                  (SELECT max(seq) FROM collab_ops WHERE session_id = s.id) AS last
           FROM collab_ops o JOIN collab_sessions s ON s.id = o.session_id JOIN notes n ON n.id = s.note_id
           WHERE s.note_id = ? AND o.client = ? AND o.op_id = ?""",
        (note_id, client, op_id),
    ).fetchone()
    if row is None:
        return {"first": False, "all": False}
    first = row["saved_seq"] >= row["seq"] or (not row["closed"] and bool(row["current"]))
    return {"first": first, "all": first and bool(row["closed"]) and row["saved_seq"] == row["seq"] == row["last"]}


def _joined(conn, row, client, actor, rev):
    _present(row["id"], client, actor)
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


def _encode(op):
    """The op as stored: BadOp for anything but a well-formed op of plain JSON, OpTooLarge."""
    if not _well_formed(op):
        raise BadOp
    try:
        # NaN or Infinity would be stored, then answered to everyone as invalid JSON.
        encoded = json.dumps(op, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        raise BadOp from exc
    if len(encoded.encode()) > MAX_OP:
        raise OpTooLarge
    return encoded


def _append(conn, session_id, client, actor, op_id, base, encoded):
    """Store the op as seq base + 1 if base is the last seq. A resend (lost answer) is stored once."""
    already = conn.execute(
        "SELECT 1 FROM collab_ops WHERE session_id = ? AND client = ? AND op_id = ?", (session_id, client, op_id)
    ).fetchone()
    if not already and base == last_seq(conn, session_id):
        conn.execute(
            "INSERT INTO collab_ops (session_id, seq, op, client, op_id, author) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, base + 1, encoded, client, op_id, actor["name"]),
        )
        _touch(conn, session_id)


def _reset(conn, row, client, op_id):
    """The session is over. "saved": the op being resent is already in the saved text (its
    answer was lost, a co-editor saved it), so the client must not carry it over again."""
    saved = row is not None and op_id and conn.execute(
        "SELECT 1 FROM collab_ops WHERE session_id = ? AND client = ? AND op_id = ? AND seq <= ?",
        (row["id"], client, op_id, row["saved_seq"]),
    ).fetchone() is not None
    return {"reset": True, "saved": bool(saved)}


def sync(conn, session_id, client, actor, seen, op=None, op_id="", base=None, cursor=None):
    """One poll: take the client's operation (if it has seen every earlier one) and hand back
    every operation after `seen`, its own included (that is how it learns it was accepted).
    cursor: the client's caret after all `seen` operations; the others see it.
    BadOp for anything but a well-formed op of plain JSON; OpTooLarge; TooManyClients."""
    if op is None:
        row = get(conn, session_id)
        if not _valid(conn, row):
            return {"reset": True}
    else:
        encoded = _encode(op)
        with tx(conn):
            row = get(conn, session_id)
            if not _valid(conn, row):
                return _reset(conn, row, client, op_id)
            _append(conn, session_id, client, actor, op_id, base, encoded)
    _present(session_id, client, actor, cursor)
    return {
        "ops": _ops_after(conn, session_id, seen),
        "saved_seq": row["saved_seq"],
        "participants": participants(session_id),
        "cursors": cursors(session_id, client),
    }


def leave(session_id, client):
    _participants.get(session_id, {}).pop(client, None)


def save(conn, session_id, body, seq, actor, client=None, op=None, op_id="", final=False):
    """Write the session's text at `seq` to the note. False when the session is no longer valid
    (the client resets). Only a newer seq writes: an equal or older one comes from a slower
    client or a late request and changes nothing.

    A tab going away sends its last save with the op still on its way (op, op_id: stored like a
    sync) and seq = that op's place: the text is written only if that op got it, else it would
    lack a co-editor's op that came first. final: the text also holds changes that never reached
    the log; written only if nothing came after seq, and the session then ends, so the next
    editor starts from this text rather than replaying a log without them."""
    encoded = None if op is None else _encode(op)
    with tx(conn):
        row = get(conn, session_id)
        if not _valid(conn, row):
            return False
        if encoded is not None:
            _append(conn, session_id, client, actor, op_id, seq - 1, encoded)
            at = conn.execute("SELECT client, op_id FROM collab_ops WHERE session_id = ? AND seq = ?", (session_id, seq)).fetchone()
            if at is None or (at["client"], at["op_id"]) != (client, op_id):
                return True
        last = last_seq(conn, session_id)
        if final:
            if seq != last:
                return True
        else:
            # No further than the server's own log: a larger claim would mark unsent ops saved.
            seq = min(seq, last)
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
        if final:
            close(conn, session_id)
    return True


@job("collab-prune", every=3600)
def prune(conn):
    """Sessions whose note has moved on can never be joined again: closed. Closed sessions a
    week old go with their op logs (the foreign key cascades)."""
    cutoff = iso(now_utc() - timedelta(days=KEEP_DAYS))
    with tx(conn):
        conn.execute(
            """UPDATE collab_sessions SET closed = 1 WHERE closed = 0 AND NOT EXISTS
               (SELECT 1 FROM notes n WHERE n.id = note_id AND n.rev = note_rev AND n.deleted_at IS NULL)"""
        )
        conn.execute("DELETE FROM collab_sessions WHERE closed = 1 AND last_active_at < ?", (cutoff,))
