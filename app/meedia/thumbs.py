"""Thumbnails and previews, made on first request and kept in the cache folder
(MEDIA_CACHE_DIR, else <DATA_DIR>/meedia-cache): never in the media folder, never backed up.
Named by content, so a renamed file keeps its thumbnail and a changed one gets a new one."""

import io
import os
import secrets
from pathlib import Path

from PIL import Image, ImageOps

from . import probe

SIZES = (400, 1600)  # the grid; the lightbox and link previews
POSTER_AT = 0.10  # a video's poster: this far into it


def cache_root():
    return Path(os.environ.get("MEDIA_CACHE_DIR") or Path(os.environ.get("DATA_DIR", "data")) / "meedia-cache").resolve()


def get(root, row, size):
    """Path of the JPEG at most `size` px on its longer edge. probe.Unreadable when the file
    cannot be read."""
    target = cache_root() / f"{row['sha256'][:40]}-{size}.jpg"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    source = root / row["name"]
    if row["kind"] == "video":
        data = probe.frame(source, (row["duration"] or 0) * POSTER_AT, size)
    else:
        data = _picture(source, size)
    tmp = target.with_name(f".{secrets.token_hex(6)}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)  # two requests at once both write; either result is the same
    return target


def _picture(path, size):
    try:
        with Image.open(path) as img:
            img.seek(0)  # a GIF: its first frame
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size))
            if img.mode != "RGB":
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, "white")
                flat.paste(img, mask=img.getchannel("A"))
                img = flat
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82)
    except Exception as exc:  # noqa: BLE001
        raise probe.Unreadable("Pilti ei saa avada.") from exc
    return out.getvalue()
