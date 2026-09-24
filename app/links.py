"""Links between notes: `[anchor](/m/<slug>)` in bodies, rebuilt into the links table on every save;
outgoing links, backlinks, unlinked mentions, and "Seo" (link a mention). docs/PLAN-OVERVIEW.md §5.
"""

import json
import re

from . import notes
from .db import tx

# Link text may hold escaped brackets and one level of balanced ones: [a [b] c](/m/x).
_TEXT = r"(?:[^\[\]\\\n]|\\.|\[(?:[^\[\]\\\n]|\\.)*\])*"
_LINK_RE = re.compile(
    rf"(?<![!\\])\[(?P<anchor>{_TEXT})\]\(\s*/m/(?P<slug>[a-z0-9-]+)(?:#[^)\s]*)?(?:\s+\"[^\"]*\")?\s*\)")
# Any Markdown link or image, and bare URLs: a mention inside these is not plain text.
_ANY_LINK_RE = re.compile(rf"!?\[{_TEXT}\]\([^)\n]*\)|<[a-z]+:[^>\s]*>|\bhttps?://\S+", re.I)
SNIPPET_WIDTH = 60


def parse(body):
    """[(slug, anchor, start, end)] for every /m/ link outside code, in order."""
    masked = notes.mask_code(body)
    return [(m["slug"], re.sub(r"\\(.)", r"\1", body[m.start("anchor"):m.end("anchor")]), m.start(), m.end())
            for m in _LINK_RE.finditer(masked)]


def rebuild(conn, note_id, body):
    """Replace the note's rows in links, one per target slug (first anchor wins)."""
    with tx(conn):
        conn.execute("DELETE FROM links WHERE src_id = ?", (note_id,))
        seen = set()
        for slug, anchor, _, _ in parse(body):
            if slug in seen:
                continue
            seen.add(slug)
            dst = notes.by_slug(conn, slug)
            conn.execute("INSERT INTO links (src_id, dst_slug, dst_id, anchor) VALUES (?, ?, ?, ?)",
                         (note_id, slug, dst["id"] if dst else None, anchor))


def outgoing(conn, note):
    """[{slug, title, exists}] in the order they appear; a missing note shows its link text."""
    rows = conn.execute(
        "SELECT l.dst_slug, l.anchor, d.id AS dst_id, d.title FROM links l "
        "LEFT JOIN notes d ON d.id = l.dst_id AND d.deleted_at IS NULL "
        "WHERE l.src_id = ? ORDER BY l.rowid", (note["id"],))
    return [{"slug": r["dst_slug"], "title": r["title"] if r["dst_id"] else (r["anchor"] or r["dst_slug"]),
             "exists": r["dst_id"] is not None} for r in rows]


def backlinks(conn, note):
    """[{slug, title, snippet}] of live notes linking here, the snippet around the first link."""
    rows = conn.execute(
        "SELECT DISTINCT n.slug, n.title, n.body, n.updated_at FROM links l JOIN notes n ON n.id = l.src_id "
        "WHERE l.dst_id = ? AND n.id != ? AND n.deleted_at IS NULL ORDER BY n.updated_at DESC",
        (note["id"], note["id"]))
    out = []
    for r in rows:
        link = next((x for x in parse(r["body"]) if x[0] == note["slug"]), None)
        snippet = _snippet(r["body"], link[2], link[1]) if link else ""
        out.append({"slug": r["slug"], "title": r["title"], "snippet": snippet})
    return out


def unlinked_mentions(conn, note):
    """[{slug, title, snippet}] of other live notes that name this note's title or an alias as plain
    text (whole words, any case, not in code or link text) and do not link here yet."""
    out = []
    for src in _mention_candidates(conn, note):
        found = _find_mention(note, src["body"])
        if found:
            start, end = found
            out.append({"slug": src["slug"], "title": src["title"],
                        "snippet": _snippet(src["body"], start, src["body"][start:end])})
    return out


def link_mention(conn, target, src):
    """Turn the first plain mention of target in src into a link, as a server-side write (NoteBusy
    propagates). False when src has no such mention (or already links to target)."""
    with tx(conn):
        src = notes.get(conn, src["id"])
        if src is None or _links_to(conn, src["id"], target):
            return False
        found = _find_mention(target, src["body"])
        if found is None:
            return False
        start, end = found
        text = re.sub(r"([\[\]\\])", r"\\\1", src["body"][start:end])
        body = f"{src['body'][:start]}[{text}](/m/{target['slug']}){src['body'][end:]}"
        notes.write_body(conn, src["id"], body)
        return True


def _terms(note):
    """Title and aliases worth searching for: something wordlike, each once."""
    out = []
    for term in [note["title"], *json.loads(note["aliases_json"])]:
        term = " ".join(term.split())
        if re.search(r"\w", term) and term != notes.UNTITLED and term.casefold() not in map(str.casefold, out):
            out.append(term)
    return out


def _mention_candidates(conn, note):
    # FTS finds phrase candidates cheaply (it also folds diacritics); _find_mention decides.
    ids = set()
    for term in _terms(note):
        phrase = '"' + term.replace('"', '""') + '"'
        ids.update(r[0] for r in conn.execute("SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?", (phrase,)))
    ids.discard(note["id"])
    linked = {r[0] for r in conn.execute(
        "SELECT src_id FROM links WHERE dst_id = ? OR dst_slug = ?", (note["id"], note["slug"]))}
    rows = [notes.get(conn, i) for i in ids - linked]
    return sorted((r for r in rows if r is not None), key=lambda r: r["updated_at"], reverse=True)


def _find_mention(note, body):
    """(start, end) of the first plain-text mention of note in body, or None."""
    masked = _ANY_LINK_RE.sub(lambda m: " " * len(m.group(0)), notes.mask_code(body))
    hits = []
    for term in _terms(note):
        words = r"\s+".join(map(re.escape, term.split()))
        m = re.search(rf"(?<!\w){words}(?!\w)", masked, re.I)
        if m:
            hits.append(m.span())
    return min(hits) if hits else None


def _links_to(conn, src_id, target):
    return conn.execute("SELECT 1 FROM links WHERE src_id = ? AND (dst_id = ? OR dst_slug = ?)",
                        (src_id, target["id"], target["slug"])).fetchone() is not None


def _snippet(body, pos, needle):
    """The plain text of the line holding pos, cut to about SNIPPET_WIDTH characters around needle."""
    end = body.find("\n", pos)
    line = body[body.rfind("\n", 0, pos) + 1:end if end >= 0 else len(body)]
    plain = notes.strip_markdown(line)
    i = max(plain.casefold().find(needle.casefold()), 0)
    start, end = max(0, i - SNIPPET_WIDTH), min(len(plain), i + len(needle) + SNIPPET_WIDTH)
    return ("…" if start else "") + plain[start:end].strip() + ("…" if end < len(plain) else "")
