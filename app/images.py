"""Uploaded pictures: stored once under their content hash in <DATA_DIR>/pildid, one row per file
in `images`. Never deleted: any note may still point at one."""

import hashlib
import re
from pathlib import Path

from flask import current_app

from . import svg

TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_BYTES = 10 * 1024 * 1024
NAME = re.compile(r"^[0-9a-f]{40}\.(png|jpe?g|gif|webp|svg)$")


def folder():
    # resolve(): send_from_directory reads a relative path against the app package, while writing
    # goes against the working directory.
    return (Path(current_app.config["DATA_DIR"]) / "pildid").resolve()


def url(name):
    return f"/pildid/{name}"


def store(conn, data, original, author):
    """Save one uploaded file; returns its URL, or None when it is not a picture we take.
    Like MarkText, the name is the SHA-1 of the content: the same picture twice is one file."""
    ext = Path(original).suffix.lower()
    if ext not in TYPES or len(data) > MAX_BYTES:
        return None
    if ext == ".svg":
        data = svg.clean(data)
        if data is None:
            return None
    name = f"{hashlib.sha1(data).hexdigest()}{ext}"
    target = folder()
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(data)
    # Same bytes again: the first uploader and time stay.
    conn.execute("INSERT OR IGNORE INTO images (name, original_name, author) VALUES (?, ?, ?)", (name, original, author))
    return url(name)


def listing(conn):
    """Every picture ever added, newest first, for the editor's gallery."""
    rows = conn.execute("SELECT * FROM images ORDER BY created_at DESC, rowid DESC").fetchall()
    return [{"name": r["name"], "url": url(r["name"]), "original_name": r["original_name"]} for r in rows]


def referenced(conn, note_id, name):
    """Whether the note shows this picture: in its saved text, or in an op of its live session
    (inserted by a co-editor a moment ago and not saved yet)."""
    ref = url(name)
    if conn.execute("SELECT 1 FROM notes WHERE id = ? AND instr(body, ?) > 0", (note_id, ref)).fetchone():
        return True
    return conn.execute(
        """SELECT 1 FROM collab_ops o JOIN collab_sessions s ON s.id = o.session_id
           WHERE s.note_id = ? AND s.closed = 0 AND instr(o.op, ?) > 0 LIMIT 1""",
        (note_id, ref),
    ).fetchone() is not None
