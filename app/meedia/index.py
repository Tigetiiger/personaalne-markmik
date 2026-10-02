"""The database side of the media folder: the `media` tables follow the companion files.

Every change goes companion first, then index: the companion is written, and the row is made
from what was written. A companion edited by hand is noticed by its mtime on the next scan."""

import json
import logging
import os

from ..db import tx
from ..util import now_iso
from . import store

log = logging.getLogger("markmik.meedia")

# What update() lets a caller change. The rest comes from the file itself.
EDITABLE = {"title", "description", "tags", "auto_tags", "rejected_tags", "ocr", "ai", "taken_at"}


def index(conn, name, data, mtime_ns):
    """Insert or replace the row of `name` from its companion `data`; returns the row id."""
    tags = store.normalize_tags(data.get("tags"))
    rejected = set(store.normalize_tags(data.get("rejected_tags")))
    auto = [t for t in store.normalize_tags(data.get("auto_tags")) if t not in tags and t not in rejected]
    values = {
        "name": name,
        "sha256": str(data.get("sha256") or ""),
        "kind": store.kind_of(name),
        "mime": str(data.get("mime") or "application/octet-stream"),
        "bytes": int(data.get("bytes") or 0),
        "width": data.get("width"),
        "height": data.get("height"),
        "duration": data.get("duration"),
        "taken_at": data.get("taken_at"),
        "added_at": str(data.get("added_at") or now_iso()),
        "title": str(data.get("title") or ""),
        "description": str(data.get("description") or ""),
        "ocr": str(data.get("ocr") or ""),
        "tags_text": " ".join(tags + auto),
        "data_json": json.dumps(data, ensure_ascii=False),
        "companion_mtime": mtime_ns,
    }
    cols = ", ".join(values)
    marks = ", ".join(f":{c}" for c in values)
    updates = ", ".join(f"{c} = excluded.{c}" for c in values if c != "name")
    with tx(conn):
        conn.execute(f"INSERT INTO media ({cols}) VALUES ({marks}) ON CONFLICT (name) DO UPDATE SET {updates}", values)
        media_id = conn.execute("SELECT id FROM media WHERE name = ?", (name,)).fetchone()[0]
        conn.execute("DELETE FROM media_tags WHERE media_id = ?", (media_id,))
        conn.executemany("INSERT INTO media_tags (media_id, tag, auto) VALUES (?, ?, ?)",
                         [(media_id, t, 0) for t in tags] + [(media_id, t, 1) for t in auto])
        conn.execute("DELETE FROM media_pending WHERE name = ?", (name,))
    return media_id


def index_companion(conn, root, name):
    """Index `name` from its companion on disk. A companion that is not valid JSON leaves the
    row as it was (logged once: its mtime is remembered) and returns False."""
    path = store.companion_path(root, name)
    mtime = path.stat().st_mtime_ns
    try:
        data = store.read_companion(root, name)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        log.warning("media companion %s unreadable: %s", path.name, exc)
        conn.execute("UPDATE media SET companion_mtime = ? WHERE name = ?", (mtime, name))
        return False
    index(conn, name, data, mtime)
    return True


def intake(conn, root, name, *, source):
    """Take in a file already in the root: write its companion and index it. Returns the row id."""
    data = store.new_companion(root / name, source=source, added_at=now_iso())
    return index(conn, name, data, store.write_companion(root, name, data))


def update(conn, root, name, changes):
    """Apply `changes` (keys in EDITABLE) to the companion on disk, then index it. Tags are
    normalized. KeyError for a key outside EDITABLE; FileNotFoundError for an unknown name."""
    bad = set(changes) - EDITABLE
    if bad:
        raise KeyError(", ".join(sorted(bad)))
    data = store.read_companion(root, name)
    for key, value in changes.items():
        data[key] = store.normalize_tags(value) if key.endswith("tags") else value
    return index(conn, name, data, store.write_companion(root, name, data))


def remove(conn, root, name):
    """Move the file and its companion to the trash and drop the row."""
    store.move_to_trash(root, name)
    conn.execute("DELETE FROM media WHERE name = ?", (name,))


def scan(conn, root):
    """Bring the index in step with the folder. Returns counts of what happened:
    indexed (a new or edited companion), taken (a new file taken in), restored (a lost companion
    written back from the index), dropped (a row whose file is gone), waiting (a new file not yet
    the same size twice)."""
    store.ensure(root)
    counts = dict.fromkeys(("indexed", "taken", "restored", "dropped", "waiting"), 0)
    entries = {e.name: e for e in os.scandir(root) if e.is_file() and not e.name.startswith(".")}
    media = {n for n in entries if store.kind_of(n)}
    rows = {r["name"]: r for r in conn.execute("SELECT name, companion_mtime, data_json FROM media")}

    for name in sorted(media):
        companion = entries.get(name + store.SUFFIX)
        row = rows.get(name)
        if companion is not None:
            if row is None or row["companion_mtime"] != companion.stat().st_mtime_ns:
                counts["indexed"] += index_companion(conn, root, name)
        elif row is not None:
            data = json.loads(row["data_json"])
            index(conn, name, data, store.write_companion(root, name, data))
            counts["restored"] += 1
        elif _settled(conn, name, entries[name].stat()):
            intake(conn, root, name, source="folder")
            counts["taken"] += 1
        else:
            counts["waiting"] += 1

    gone = [n for n in rows if n not in media]
    with tx(conn):
        conn.executemany("DELETE FROM media WHERE name = ?", [(n,) for n in gone])
        pending = [r[0] for r in conn.execute("SELECT name FROM media_pending")]
        conn.executemany("DELETE FROM media_pending WHERE name = ?", [(n,) for n in pending if n not in media])
    counts["dropped"] = len(gone)
    return counts


def _settled(conn, name, st):
    """True when the file had the same size and mtime at the previous scan; otherwise remembers
    what it has now."""
    seen = conn.execute("SELECT bytes, mtime_ns FROM media_pending WHERE name = ?", (name,)).fetchone()
    if seen is not None and (seen["bytes"], seen["mtime_ns"]) == (st.st_size, st.st_mtime_ns):
        return True
    conn.execute("INSERT INTO media_pending (name, bytes, mtime_ns) VALUES (?, ?, ?) "
                 "ON CONFLICT (name) DO UPDATE SET bytes = excluded.bytes, mtime_ns = excluded.mtime_ns",
                 (name, st.st_size, st.st_mtime_ns))
    return False


def reindex(conn, root):
    """Rebuild the index from the folder alone. Files without a companion are left for the next
    scans to take in, as usual."""
    with tx(conn):
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM media_pending")
    return scan(conn, root)
