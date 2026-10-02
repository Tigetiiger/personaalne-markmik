"""Finding kept files (docs/MEEDIA-PLAN.md, call 17).

One search box: free words are matched as word prefixes over name, title, description, tags and
the text read from the picture, diacritics folded ("koik" finds "kõik"); `#koer` requires a tag,
`-#koer` excludes one. Chips add kind, a date range, "untagged" and "shared" on top."""

import json
import re
from datetime import date, datetime, time, timedelta

from ..util import TALLINN, iso, now_utc
from . import shares

WHEN = "coalesce(m.taken_at, m.added_at)"
TAG_TOKEN = re.compile(r"(-?)#(\S+)")


def parse(q):
    """'koer -#kass #Park mets' -> (["koer", "mets"], ["park"], ["kass"])."""
    tags, not_tags = [], []
    for minus, tag in TAG_TOKEN.findall(q or ""):
        (not_tags if minus else tags).append(tag.casefold())
    words = re.findall(r"\w+", TAG_TOKEN.sub(" ", q or ""))
    return words, tags, not_tags


def find(conn, *, q="", tags=(), not_tags=(), kind=None, since=None, until=None, untagged=False,
         shared=False, offset=0, limit=100):
    """({total, items}) newest first. since/until: local dates 'YYYY-MM-DD', both inclusive."""
    words, q_tags, q_not = parse(q)
    where, args = [], []
    if words:
        # Each word quoted as an FTS string: '"', '*', NEAR, '-' typed in the box stay plain text.
        where.append("m.id IN (SELECT rowid FROM media_fts WHERE media_fts MATCH ?)")
        args.append(" ".join(f'"{w}"*' for w in words))
    for tag in [*tags, *q_tags]:
        where.append("EXISTS (SELECT 1 FROM media_tags t WHERE t.media_id = m.id AND t.tag = ?)")
        args.append(tag)
    for tag in [*not_tags, *q_not]:
        where.append("NOT EXISTS (SELECT 1 FROM media_tags t WHERE t.media_id = m.id AND t.tag = ?)")
        args.append(tag)
    if kind in ("image", "video"):
        where.append("m.kind = ?")
        args.append(kind)
    if since:
        where.append(f"{WHEN} >= ?")
        args.append(_day_start(since))
    if until:
        where.append(f"{WHEN} < ?")
        args.append(_day_start(until, 1))
    if untagged:
        where.append("NOT EXISTS (SELECT 1 FROM media_tags t WHERE t.media_id = m.id)")
    if shared:
        where.append("m.name IN (SELECT i.name FROM media_share_items i JOIN media_shares s ON s.id = i.share_id "
                     f"WHERE {shares.OPEN})")
        args.append(iso(now_utc()))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT count(*) FROM media m {clause}", args).fetchone()[0]
    rows = conn.execute(f"SELECT m.* FROM media m {clause} ORDER BY {WHEN} DESC, m.id DESC LIMIT ? OFFSET ?",
                        [*args, limit, offset]).fetchall()
    return {"total": total, "items": [item(conn, r) for r in rows]}


def _day_start(text, plus=0):
    """Local midnight of 'YYYY-MM-DD' (+ plus days) as the index's UTC text. ValueError if bad."""
    day = date.fromisoformat(text) + timedelta(days=plus)
    return iso(datetime.combine(day, time(), tzinfo=TALLINN))


def item(conn, row, full=False):
    """A file for the browser. full: also what only the detail view shows."""
    tags = conn.execute("SELECT tag, auto FROM media_tags WHERE media_id = ? ORDER BY auto, tag", (row["id"],))
    out = {k: row[k] for k in ("name", "kind", "mime", "bytes", "width", "height", "duration",
                               "taken_at", "added_at", "title", "description")}
    out["tags"] = [{"tag": t["tag"], "auto": bool(t["auto"])} for t in tags]
    if full:
        data = json.loads(row["data_json"])
        out.update({k: data.get(k) for k in ("gps", "camera", "ocr", "source", "converted_from", "ai")})
        out["taken_local"] = data.get("taken_at")
    return out


def known_tags(conn, limit=500):
    return [{"tag": r[0], "count": r[1]} for r in conn.execute(
        "SELECT tag, count(*) FROM media_tags GROUP BY tag ORDER BY count(*) DESC, tag LIMIT ?", (limit,))]
