"""Uploaded pictures: stored once under their content hash in <DATA_DIR>/pildid, one row per file
in `images`. Never deleted: any note may still point at one.

A share-link guest may load only the pictures in `note_images` for its note. What the note's text
shows is not enough: an edit guest could type any picture's name into it."""

import hashlib
import re
from pathlib import Path

from flask import current_app

from . import svg
from .db import tx

TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_BYTES = 10 * 1024 * 1024
NAME = re.compile(r"^[0-9a-f]{40}\.(png|jpe?g|gif|webp|svg)$")
SHOWN = re.compile(r"/pildid/([0-9a-f]{40}\.(?:png|jpe?g|gif|webp|svg))")
SEEDED_KEY = "note_images_seeded"


def folder():
    # resolve(): send_from_directory reads a relative path against the app package, while writing
    # goes against the working directory.
    return (Path(current_app.config["DATA_DIR"]) / "pildid").resolve()


def url(name):
    return f"/pildid/{name}"


def store(conn, data, original, author, note_id=None):
    """Save one uploaded file; returns its URL, or None when it is not a picture we take.
    Like MarkText, the name is the SHA-1 of the content: the same picture twice is one file.
    note_id: the note it was uploaded into; that note's guests may load it."""
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
    if note_id is not None:
        allow(conn, note_id, [name])
    return url(name)


def listing(conn):
    """Every picture ever added, newest first, for the editor's gallery."""
    rows = conn.execute("SELECT * FROM images ORDER BY created_at DESC, rowid DESC").fetchall()
    return [{"name": r["name"], "url": url(r["name"]), "original_name": r["original_name"]} for r in rows]


def allow(conn, note_id, names):
    conn.executemany("INSERT OR IGNORE INTO note_images (note_id, name) VALUES (?, ?)", [(note_id, n) for n in names])


def allow_shown(conn, note_id):
    """The pictures the note's saved text shows now become ones its guests may load."""
    row = conn.execute("SELECT body FROM notes WHERE id = ?", (note_id,)).fetchone()
    allow(conn, note_id, set(SHOWN.findall(row["body"])) if row else ())


def allowed(conn, note_id, name):
    return conn.execute("SELECT 1 FROM note_images WHERE note_id = ? AND name = ?", (note_id, name)).fetchone() is not None


def seed_shared(conn):
    """Once per database, at the first start with note_images: the links made before it existed
    get the pictures their notes show at that moment. Not on a guest's first request: by then an
    edit guest may have typed another picture's name into the note."""
    with tx(conn):
        if conn.execute("SELECT 1 FROM settings WHERE key = ?", (SEEDED_KEY,)).fetchone():
            return
        for (note_id,) in conn.execute("SELECT DISTINCT note_id FROM shares WHERE revoked_at IS NULL").fetchall():
            allow_shown(conn, note_id)
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (SEEDED_KEY,))
