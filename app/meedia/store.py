"""The media folder on disk (docs/MEEDIA-PLAN.md): flat, originals beside their companion files.

    <root>/IMG_1234.jpg
    <root>/IMG_1234.jpg.json     the companion file: the truth about the file
    <root>/.saabumas/            uploads and conversions in progress
    <root>/.prügi/               deleted files with their companions, emptied after TRASH_DAYS

Nothing else is written into the root, so backing it up is one copy of the folder. Names starting
with a dot are never media. This module touches only the filesystem; app/meedia/index.py keeps the
database in step with it."""

import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..util import now_utc

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
INCOMING = ".saabumas"
TRASH = ".prügi"
TRASH_DAYS = 30
TRASH_STAMP = "%Y%m%dT%H%M%SZ"
SUFFIX = ".json"
TAG_MAX = 40

# The companion's keys in the order they are written. Keys a person added by hand follow them,
# kept as they are.
KEYS = (
    "version", "sha256", "kind", "mime", "bytes", "width", "height", "duration", "taken_at", "gps",
    "camera", "added_at", "source", "converted_from", "title", "description", "tags", "auto_tags",
    "rejected_tags", "ocr", "ai",
)
VERSION = 1


def default_root():
    """MEDIA_DIR, else <DATA_DIR>/meedia. Read from the environment: the worker has no Flask app."""
    return Path(os.environ.get("MEDIA_DIR") or Path(os.environ.get("DATA_DIR", "data")) / "meedia").resolve()


def ensure(root):
    for sub in (root, root / INCOMING, root / TRASH):
        sub.mkdir(parents=True, exist_ok=True)


def kind_of(name):
    """'image', 'video', or None for anything that is not a media file we keep."""
    if name.startswith(".") or name.endswith(SUFFIX):
        return None
    ext = Path(name).suffix.lower()
    return "image" if ext in IMAGE_TYPES else "video" if ext in VIDEO_TYPES else None


def companion_path(root, name):
    return root / (name + SUFFIX)


def clean_name(original):
    """A safe file name from an uploaded or imported one: no folders, no leading dot, no control
    characters or slashes. Keeps the user's own name otherwise (õäöü too)."""
    base = Path(original.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f/]", "", base).strip().lstrip(".").strip()
    stem, ext = os.path.splitext(base)
    return (stem[:120] or "fail") + ext.lower()


def free_name(root, wanted):
    """`wanted`, or wanted with -2, -3, ... before the extension when the file or its companion
    already exists in the root."""
    stem, ext = os.path.splitext(wanted)
    name, n = wanted, 1
    while (root / name).exists() or companion_path(root, name).exists():
        n += 1
        name = f"{stem}-{n}{ext}"
    return name


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def normalize_tag(tag):
    """'  #Sügis  Õhtu ' -> 'sügis õhtu'; '' when nothing is left."""
    return " ".join(str(tag).casefold().lstrip("#").split())[:TAG_MAX].strip()


def normalize_tags(tags):
    """Normalized, without empties or repeats, in the given order."""
    return list(dict.fromkeys(t for t in map(normalize_tag, tags or ()) if t))


def new_companion(path, *, source, added_at):
    """The companion of a file being taken in: what the file itself says, nothing from a person yet."""
    name = path.name
    return {
        "version": VERSION,
        "sha256": sha256_of(path),
        "kind": kind_of(name),
        "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "bytes": path.stat().st_size,
        "width": None, "height": None, "duration": None, "taken_at": None, "gps": None, "camera": None,
        "added_at": added_at,
        "source": source,
        "converted_from": None,
        "title": "", "description": "",
        "tags": [], "auto_tags": [], "rejected_tags": [],
        "ocr": "",
        "ai": None,
    }


def read_companion(root, name):
    """The companion as a dict. ValueError when it is not a JSON object."""
    data = json.loads(companion_path(root, name).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("kaasfail pole JSON-objekt")
    return data


def ordered(data):
    out = {k: data[k] for k in KEYS if k in data}
    out.update((k, v) for k, v in data.items() if k not in out)
    return out


def write_companion(root, name, data):
    """Write it whole, in the fixed key order, through a temp file and a rename: a reader or a
    crash never sees half a file. Returns the new file's st_mtime_ns."""
    target = companion_path(root, name)
    tmp = root / f".{name}{SUFFIX}.tmp"
    tmp.write_text(json.dumps(ordered(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target.stat().st_mtime_ns


def move_to_trash(root, name, now=None):
    """Move the file and its companion into .prügi under a time stamp; returns the trash name."""
    trash = root / TRASH
    trash.mkdir(exist_ok=True)
    stamped = f"{(now or now_utc()).strftime(TRASH_STAMP)}_{name}"
    for src, dst in ((root / name, trash / stamped), (companion_path(root, name), trash / (stamped + SUFFIX))):
        if src.exists():
            os.replace(src, dst)
    return stamped


def purge_trash(root, now=None, days=TRASH_DAYS):
    """Delete what has been in the trash longer than `days`. Returns the deleted names. An entry
    without a readable stamp is left alone: it was not put there by move_to_trash."""
    trash = root / TRASH
    if not trash.is_dir():
        return []
    cutoff = (now or now_utc()) - timedelta(days=days)
    gone = []
    for p in sorted(trash.iterdir()):
        stamp = p.name.split("_", 1)[0]
        try:
            when = datetime.strptime(stamp, TRASH_STAMP).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < cutoff and p.is_file():
            p.unlink()
            gone.append(p.name)
    return gone
