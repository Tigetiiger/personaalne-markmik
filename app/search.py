"""Full-text search over notes (notes_fts) and the `[[` link picker's title suggestions.

Snippets are plain text; the matched words sit between HL_START and HL_END (the control characters
STX and ETX, which never occur in note text). The UI escapes the snippet, then wraps each
HL_START...HL_END run in <mark>.
"""

import re
import unicodedata

HL_START, HL_END = "\x02", "\x03"


def search(conn, q, limit=20):
    """[{slug, title, snippet}] best first. Every word of q must match the start of a word in the
    note; diacritics fold both ways ("too" finds "töö"). q is never read as FTS syntax."""
    words = re.findall(r"\w+", q)
    if not words:
        return []
    # Each word quoted as an FTS string: '"', '*', NEAR, AND, '-' in q stay plain text.
    query = " ".join(f'"{w}"*' for w in words)
    rows = conn.execute(
        "SELECT n.slug, n.title, snippet(notes_fts, 1, ?, ?, '…', 12) AS snippet "
        "FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid "
        "WHERE notes_fts MATCH ? AND n.deleted_at IS NULL "
        "ORDER BY bm25(notes_fts, 10.0, 1.0) LIMIT ?", (HL_START, HL_END, query, limit))
    return [dict(r) for r in rows]


def suggest(conn, q, limit=10):
    """[{slug, title, folder}] by title: titles starting with q, then a word starting with q, then
    containing q; ties most recently updated first. Empty q: the most recently updated notes."""
    rows = conn.execute("SELECT slug, title, folder FROM notes WHERE deleted_at IS NULL ORDER BY updated_at DESC, id DESC")
    needle = _fold(q)
    ranked = []
    for r in rows:
        title = _fold(r["title"])
        if title.startswith(needle):
            rank = 0
        elif re.search(r"(?<!\w)" + re.escape(needle), title):
            rank = 1
        elif needle in title:
            rank = 2
        else:
            continue
        ranked.append((rank, len(ranked), dict(r)))
    return [r for _, _, r in sorted(ranked)[:limit]]


def _fold(text):
    """Lowercase without diacritics, whitespace collapsed: 'Töö  Plaan' -> 'too plaan'."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())
