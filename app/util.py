"""Small helpers shared by every module: time zone, timestamps, slugs."""

import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TALLINN = ZoneInfo("Europe/Tallinn")


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    """Aware datetime -> '2026-09-24T08:15:00Z' (the format every *_at column uses)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(now_utc())


def parse_iso(text):
    """'2026-09-24T08:15:00Z' -> aware UTC datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def local_today():
    return datetime.now(TALLINN).date()


def slug_base(title):
    """The slug a title makes, '' when it has no letter or digit that survives (then "marge")."""
    ascii_text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:60].strip("-")


def slugify(title, taken=()):
    """URL slug from a title: õäöüšž -> oaousz, lowercase, non-alnum -> '-', max 60 chars.

    taken: existing slugs; on a clash '-2', '-3', ... is appended.
    """
    base = slug_base(title) or "marge"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug
