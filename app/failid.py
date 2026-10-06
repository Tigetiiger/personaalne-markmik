"""Data files for Python cells (docs/FAILID-PLAN.md): stored once under their content hash in
<DATA_DIR>/failid, one row per file in `files`. A note uses a file by linking it,
`[andmed.csv](/failid/<name>)`; the link's text is the name its code reads it by.

Every file is checked on the way in, whoever brings it (an upload, a web address, a cell's
output): it is read later inside the sandbox by pandas and openpyxl, and downloaded by guests.

A share-link guest may download only the files in `note_files` for its note, as with pictures: an
edit guest could type any file's name into the note's text."""

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from flask import current_app

from . import ics
from .db import tx
from .jobs import job
from .util import iso, now_iso, now_utc

TYPES = (".csv", ".tsv", ".txt", ".json", ".xlsx", ".parquet")
TEXT_TYPES = (".csv", ".tsv", ".txt", ".json")
MAX_BYTES = 25 * 1024 * 1024
MAX_UNPACKED = 200 * 1024 * 1024  # an .xlsx's members together, as declared in the zip
FETCH_SECONDS = 30
# Some data portals answer an unknown client (httpx's own agent) with 403.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
OWNER = "omanik"     # files.author of an upload or a fetched address
CODE = "arvutus"     # files.author of a file a cell wrote
KEEP = timedelta(days=1)
NAME = re.compile(r"^[0-9a-f]{64}\.(csv|tsv|txt|json|xlsx|parquet)$")
# [text](/failid/<name>), not a picture's ![…]; the text may hold backslash escapes, \] among them.
LINK = re.compile(r"(?<!!)\[((?:[^\[\]\\\n]|\\.)*)\]\(/failid/([0-9a-f]{64}\.(?:csv|tsv|txt|json|xlsx|parquet))\)")
ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
WRONG_TYPE = "Seda failitüüpi ei saa lisada. Lubatud: CSV, TSV, TXT, JSON, Excel (.xlsx) ja Parquet."


def folder():
    # resolve(): send_from_directory reads a relative path against the app package, while writing
    # goes against the working directory.
    return (Path(current_app.config["DATA_DIR"]) / "failid").resolve()


def url(name):
    return f"/failid/{name}"


def check(data, ext):
    """Raise ValueError with the reason, in Estonian, unless `data` is a file of type `ext` we take."""
    if ext not in TYPES:
        raise ValueError(WRONG_TYPE)
    if len(data) > MAX_BYTES:
        raise ValueError(f"Fail on liiga suur (üle {MAX_BYTES // 1024 // 1024} MB).")
    if ext in TEXT_TYPES:
        text = _text(data)
        if ext == ".json":
            try:
                json.loads(text)
            except (ValueError, RecursionError):
                raise ValueError("JSON-fail on vigane.") from None
    elif ext == ".xlsx":
        _check_xlsx(data)
    elif ext == ".parquet" and (len(data) < 12 or data[:4] != b"PAR1" or data[-4:] != b"PAR1"):
        raise ValueError("See pole Parquet-fail.")


def _text(data):
    # A NUL byte never appears in a text file of either encoding; nearly every binary has one,
    # and Windows-1257 alone would accept most of them.
    if b"\0" in data:
        raise ValueError("See pole tekstifail.")
    for encoding in ("utf-8", "cp1257"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Faili kodeering pole UTF-8 ega Windows-1257.")


def _check_xlsx(data):
    """A workbook within MAX_UNPACKED. The declared sizes are what counts: Python's zipfile (and so
    openpyxl, in the sandbox) stops a member at its declared size, so a member lying about it fails
    its CRC instead of growing."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = z.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, EOFError):
        raise ValueError("Exceli fail on vigane.") from None
    if "xl/workbook.xml" not in {m.filename for m in members}:
        raise ValueError("See pole Exceli töövihik (.xlsx).")
    if any(m.file_size > MAX_UNPACKED for m in members) or sum(m.file_size for m in members) > MAX_UNPACKED:
        raise ValueError(f"Exceli fail on lahtipakituna liiga suur (üle {MAX_UNPACKED // 1024 // 1024} MB).")


def safe_name(original):
    """A plain file name: the last path part, without control and format characters (a right-to-left
    override could disguise the type), at most 100 characters with the extension kept."""
    base = re.split(r"[\\/]", original or "")[-1]
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C").strip()
    ext = Path(base).suffix
    if len(base) > 100:
        base = base[:100 - len(ext)] + ext
    return base


def store(conn, data, original_name, author, note_id=None, source_url=None):
    """Check and save one file; returns its name (sha256 + extension). ValueError with the reason
    when it is not a file we take. The same bytes twice are one file: the first original name and
    time stay. note_id: that note's guests may download it."""
    original = safe_name(original_name)
    ext = Path(original).suffix.lower()
    check(data, ext)
    name = f"{hashlib.sha256(data).hexdigest()}{ext}"
    target = folder()
    target.mkdir(parents=True, exist_ok=True)
    if not (target / name).is_file():
        # Written aside and renamed: a crash must not leave half a file under a content name that
        # is never written again.
        fd, partial = tempfile.mkstemp(dir=target, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(partial, target / name)
    fetched_at = now_iso() if source_url else None
    # The owner adding bytes a cell once wrote makes them the owner's: the clean-up leaves them.
    # A fetch records its address, so "Värskenda" works whoever brought the bytes first.
    with tx(conn):
        conn.execute(
            """INSERT INTO files (name, original_name, ext, size, author, source_url, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (name) DO UPDATE SET
                 author = CASE WHEN excluded.author = ? THEN files.author ELSE excluded.author END,
                 source_url = coalesce(excluded.source_url, files.source_url),
                 fetched_at = coalesce(excluded.fetched_at, files.fetched_at)""",
            (name, original, ext, len(data), author, source_url, fetched_at, CODE),
        )
        if note_id is not None:
            allow(conn, note_id, [name])
    return name


def info(conn, name):
    """The file's row as JSON for the browser, or None."""
    row = conn.execute("SELECT * FROM files WHERE name = ?", (name,)).fetchone()
    return {**dict(row), "url": url(name)} if row else None


def allow(conn, note_id, names):
    conn.executemany("INSERT OR IGNORE INTO note_files (note_id, name) VALUES (?, ?)", [(note_id, n) for n in names])


def allowed(conn, note_id, name):
    return conn.execute("SELECT 1 FROM note_files WHERE note_id = ? AND name = ?", (note_id, name)).fetchone() is not None


def linked(markdown):
    """[(link text, name)] of every link to a stored file, in order; escapes in the text undone."""
    return [(ESCAPED.sub(r"\1", text), name) for text, name in LINK.findall(markdown)]


def search(conn, q="", limit=50):
    """Files whose original name holds q, newest first, each with the live notes linking it."""
    pattern = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
    rows = conn.execute("SELECT * FROM files WHERE original_name LIKE ? ESCAPE '\\' ORDER BY created_at DESC, rowid DESC LIMIT ?",
                        (pattern, limit)).fetchall()
    out = []
    for r in rows:
        notes = conn.execute("SELECT slug, title FROM notes WHERE deleted_at IS NULL AND instr(body, ?) > 0 ORDER BY title",
                             (url(r["name"]),)).fetchall()
        out.append({**dict(r), "url": url(r["name"]), "notes": [dict(n) for n in notes]})
    return out


def _download(source_url):
    return ics.download(source_url, FETCH_SECONDS, user_agent=USER_AGENT, max_bytes=MAX_BYTES)


def fetch_url(conn, source_url, note_id=None):
    """Download a public address (ics.download's checks) and store it; returns the name. The type
    comes from the address's last path part, which also becomes the file's name."""
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("Sisesta veebiaadress.")
    source_url = source_url.strip()
    original = safe_name(unquote(urlsplit(source_url).path))
    if Path(original).suffix.lower() not in TYPES:
        raise ValueError("Aadress ei lõpe lubatud failitüübiga (.csv, .tsv, .txt, .json, .xlsx või .parquet).")
    return store(conn, _download(source_url), original, OWNER, note_id, source_url)


def refresh(conn, name, note_id=None):
    """Fetch the file's address again; returns the new content's name (the same one when nothing
    changed). It keeps the old file's original name, so the note's link text stays."""
    row = conn.execute("SELECT original_name, source_url FROM files WHERE name = ?", (name,)).fetchone()
    if row is None or not row["source_url"]:
        raise ValueError("Sellel failil pole veebiaadressi, mida värskendada.")
    return store(conn, _download(row["source_url"]), row["original_name"], OWNER, note_id, row["source_url"])


@job("failid-koristus", at=("04:20",))
def clean(conn):
    """Delete files cells wrote, older than KEEP, that no note links (a note in the trash still does)."""
    target = Path(os.environ.get("DATA_DIR", "data")) / "failid"
    old = conn.execute("SELECT name FROM files WHERE author = ? AND created_at < ?",
                       (CODE, iso(now_utc() - KEEP))).fetchall()
    for (name,) in old:
        if not NAME.match(name):
            continue  # only store's own names, which are content hashes
        shown = conn.execute("SELECT 1 FROM notes WHERE instr(body, ?) > 0 LIMIT 1",
                             (f"/failid/{name}",)).fetchone()
        if shown:
            continue
        conn.execute("DELETE FROM note_files WHERE name = ?", (name,))
        conn.execute("DELETE FROM files WHERE name = ? AND author = ?", (name, CODE))
        (target / name).unlink(missing_ok=True)
