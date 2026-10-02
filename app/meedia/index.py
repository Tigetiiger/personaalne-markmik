"""The database side of the media folder: the `media` tables follow the companion files.

Every change goes companion first, then index: the companion is written, and the row is made
from what was written. A companion edited by hand is noticed by its mtime on the next scan."""

import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from ..db import tx
from ..util import iso, now_iso
from . import probe, store

log = logging.getLogger("markmik.meedia")

SCAN_BUDGET = 20.0  # seconds one scan spends taking files in; the rest waits for the next scan
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
        "taken_at": _utc(data.get("taken_at")),
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


def _utc(text):
    """The companion's local '2026-09-30T14:02:11+03:00' as the index's UTC '...Z', so dates sort
    as text; None when it is missing or not a date with an offset."""
    try:
        when = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return iso(when) if when.tzinfo else None


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


def intake(conn, root, name, *, source, path=None, trash_original=True):
    """Take in a file: convert it when it is not in the kept format, write its companion, index
    it. Returns the name it is kept under (another suffix when converted, -2 on a clash).
    probe.Unreadable when it cannot be read or converted; the file is then left where it was.

    `path`: where the file is when it is not root/name yet (an upload or an import waiting in
    .saabumas); it gets into the root only with its companion, so a scan never takes it in twice.
    A converted file's original goes to the trash (for TRASH_DAYS it can be had back), unless
    `trash_original` is False (an import: the collection still has it)."""
    path = path or root / name
    kind = store.kind_of(name)
    info = probe.meta(path, kind)
    converted_from = None
    suffix = probe.target_suffix(name, info)
    if suffix:
        work = root / store.INCOMING / f"{secrets.token_hex(8)}{suffix}"
        try:
            probe.convert(path, work, info)
            after = probe.meta(work, kind)
        except BaseException:
            work.unlink(missing_ok=True)
            raise
        for key in ("taken_at", "gps", "camera"):  # what the copy lost, from the original
            after[key] = after[key] or info[key]
        info, converted_from = after, {"name": name, "sha256": store.sha256_of(path)}
        if trash_original:
            store.trash_file(root, path, name)
        else:
            path.unlink()
        conn.execute("DELETE FROM media_pending WHERE name = ?", (name,))
        path, name = work, Path(name).stem + suffix
    final = name if path == root / name else store.free_name(root, name)
    data = store.new_companion(path, name=final, source=source, added_at=now_iso(), info=info)
    data["converted_from"] = converted_from
    # Companion first: a scan in between sees a companion without its file, which it ignores.
    mtime = store.write_companion(root, final, data)
    if path != root / final:
        os.replace(path, root / final)
    index(conn, final, data, mtime)
    return final


def find_duplicate(conn, sha256):
    """The name of a kept file with this content, as it is or as the original it was converted
    from; None when there is none."""
    row = conn.execute(
        "SELECT name FROM media WHERE sha256 = ? OR json_extract(data_json, '$.converted_from.sha256') = ? LIMIT 1",
        (sha256, sha256)).fetchone()
    return row["name"] if row else None


def expect(conn, root, name, *, source):
    """Enter a file just put in the root (a finished upload) as seen once already: the next scan
    takes it in without waiting a scan for it to hold still."""
    st = (root / name).stat()
    conn.execute("INSERT INTO media_pending (name, bytes, mtime_ns, source) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (name) DO UPDATE SET bytes = excluded.bytes, mtime_ns = excluded.mtime_ns, "
                 "source = excluded.source, error = NULL", (name, st.st_size, st.st_mtime_ns, source))


def fail(conn, name, message):
    """Remember why `name` could not be taken in; it is not tried again until the file changes."""
    log.warning("media %s not taken in: %s", name, message)
    conn.execute("UPDATE media_pending SET error = ? WHERE name = ?", (message, name))


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


def set_tags(conn, root, name, wanted):
    """Make the file's tags exactly `wanted`, as a person chose them in the app. A model's tag
    that is kept stays the model's; one that is removed goes to rejected_tags, so tagging again
    does not bring it back; a rejected one typed in again is the person's own."""
    data = store.read_companion(root, name)
    wanted = store.normalize_tags(wanted)
    auto = store.normalize_tags(data.get("auto_tags"))
    rejected = store.normalize_tags(data.get("rejected_tags"))
    shown_auto = [t for t in auto if t not in rejected]
    return update(conn, root, name, {
        "tags": [t for t in wanted if t not in shown_auto],
        "rejected_tags": [t for t in rejected if t not in wanted] + [t for t in shown_auto if t not in wanted],
    })


def remove(conn, root, name):
    """Move the file and its companion to the trash and drop the row (also a file still waiting
    to be taken in, or one that could not be)."""
    store.move_to_trash(root, name)
    conn.execute("DELETE FROM media WHERE name = ?", (name,))
    conn.execute("DELETE FROM media_pending WHERE name = ?", (name,))


def scan(conn, root, *, convert=None, busy=frozenset(), budget=SCAN_BUDGET):
    """Bring the index in step with the folder. Returns counts of what happened:
    indexed (a new or edited companion), taken (a new file taken in), queued (a video handed to
    `convert`), restored (a lost companion written back from the index), dropped (a row whose
    file is gone), waiting (a new file not yet the same twice, or past the time budget),
    failed (a file that could not be taken in), busy (being converted).

    convert(name, source): when given, a video that needs converting goes there (the worker's
    converter thread) instead of being converted here; `busy` holds the names it is working on."""
    store.ensure(root)
    started = time.monotonic()
    counts = dict.fromkeys(("indexed", "taken", "queued", "restored", "dropped", "waiting", "failed", "busy"), 0)
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
        elif name in busy:
            counts["busy"] += 1
        else:
            counts[_take(conn, root, name, entries[name].stat(), convert, time.monotonic() - started < budget)] += 1

    gone = [n for n in rows if n not in media]
    with tx(conn):
        conn.executemany("DELETE FROM media WHERE name = ?", [(n,) for n in gone])
        pending = [r[0] for r in conn.execute("SELECT name FROM media_pending")]
        conn.executemany("DELETE FROM media_pending WHERE name = ?", [(n,) for n in pending if n not in media])
    counts["dropped"] = len(gone)
    return counts


def _take(conn, root, name, st, convert, in_time):
    """One file without a companion; returns the count it falls under. It is taken in only once
    it had the same size and mtime at the previous scan; until then what it has now is kept."""
    seen = conn.execute("SELECT bytes, mtime_ns, source, error FROM media_pending WHERE name = ?", (name,)).fetchone()
    if seen is None or (seen["bytes"], seen["mtime_ns"]) != (st.st_size, st.st_mtime_ns):
        conn.execute("INSERT INTO media_pending (name, bytes, mtime_ns) VALUES (?, ?, ?) "
                     "ON CONFLICT (name) DO UPDATE SET bytes = excluded.bytes, mtime_ns = excluded.mtime_ns, error = NULL",
                     (name, st.st_size, st.st_mtime_ns))
        return "waiting"
    if seen["error"]:
        return "failed"
    if not in_time:
        return "waiting"
    try:
        if convert is not None and store.kind_of(name) == "video" \
                and probe.target_suffix(name, probe.meta(root / name, "video")):
            convert(name, seen["source"])
            return "queued"
        intake(conn, root, name, source=seen["source"])
    except probe.Unreadable as exc:
        fail(conn, name, str(exc))
        return "failed"
    return "taken"


def reindex(conn, root):
    """Rebuild the index from the folder alone. Files without a companion are left for the next
    scans to take in, as usual."""
    with tx(conn):
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM media_pending")
    return scan(conn, root)
