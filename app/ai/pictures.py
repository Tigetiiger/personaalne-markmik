"""Pictures for the model (home chat, docs/KODU-PLAN.md): a stored upload opened with Pillow,
turned upright, shrunk and re-encoded as JPEG without metadata. Opening it also proves the bytes
are a picture, which images.store only judged by the file name."""

import io

from PIL import Image, ImageOps

MAX_EDGE = 1568  # longer edges only cost tokens: the model scales them down to this anyway
PER_MESSAGE = 4
FORMATS = {"JPEG", "PNG", "WEBP"}  # no SVG (text that could carry instructions), no GIF


def prepare(path):
    """(media_type, bytes) to send, from the file at path. ValueError (Estonian) when it is not a
    picture we send."""
    try:
        with Image.open(path) as img:
            if img.format not in FORMATS:
                raise NotSent
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, "white")
                flat.paste(img, mask=img.getchannel("A"))
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85)  # no exif= given: the metadata is not written
    except NotSent:
        raise ValueError("Seda pilti ei saa saata: kasuta JPEG-, PNG- või WebP-pilti.") from None
    # A broken file fails in many ways inside Pillow (OSError, SyntaxError for a bad PNG chunk,
    # ValueError, EOFError, DecompressionBombError...): all of them mean it cannot be sent.
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Seda pilti ei saa avada.") from exc
    return "image/jpeg", out.getvalue()


class NotSent(Exception):
    """A picture in a format the model does not get."""
