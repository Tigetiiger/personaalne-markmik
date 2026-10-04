"""Plots from Python cells (docs/JUPYTER-PLAN.md): a PNG the sandbox made is re-encoded and stored
as a picture of the note; plots no live note shows any more are deleted at night.

The bytes come from code in a note, which a guest may have written: they are opened with Pillow and
written anew, never stored as they came."""

import base64
import binascii
import io
import os
from datetime import timedelta
from pathlib import Path

from PIL import Image

from .. import images
from ..jobs import job
from ..util import iso, now_utc

AUTHOR = "arvutus"  # images.author of a stored plot; people's uploads have their own
MAX_PIXELS = 4000 * 4000
MAX_BASE64 = 3 * 1024 * 1024
KEEP = timedelta(days=1)


def store(conn, encoded, note_id):
    """The stored picture's name, or None when the data is not a PNG we keep."""
    if not isinstance(encoded, str) or len(encoded) > MAX_BASE64:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(data)) as img:
            if img.format != "PNG" or img.width * img.height > MAX_PIXELS:
                return None
            img = img.convert("RGBA")
            out = io.BytesIO()
            img.save(out, "PNG", optimize=True)
    except (binascii.Error, ValueError, OSError, SyntaxError, Image.DecompressionBombError):
        return None
    url = images.store(conn, out.getvalue(), "graafik.png", AUTHOR, note_id)
    return url.rsplit("/", 1)[1] if url else None


@job("arvutus-graafikud", at=("04:10",))
def clean(conn):
    """Delete plots older than KEEP that no live note shows."""
    folder = Path(os.environ.get("DATA_DIR", "data")) / "pildid"
    old = conn.execute("SELECT name FROM images WHERE author = ? AND created_at < ?",
                       (AUTHOR, iso(now_utc() - KEEP))).fetchall()
    for (name,) in old:
        shown = conn.execute("SELECT 1 FROM notes WHERE deleted_at IS NULL AND instr(body, ?) > 0 LIMIT 1",
                             (f"/pildid/{name}",)).fetchone()
        if shown:
            continue
        conn.execute("DELETE FROM note_images WHERE name = ?", (name,))
        conn.execute("DELETE FROM images WHERE name = ? AND author = ?", (name, AUTHOR))
        (folder / name).unlink(missing_ok=True)
