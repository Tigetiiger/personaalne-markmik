"""Full-text search over notes (notes_fts) and the `[[` link picker's title suggestions.

Snippets are plain text; the matched words sit between HL_START and HL_END (the control characters
STX and ETX, which never occur in note text). The UI escapes the snippet, then wraps each
HL_START...HL_END run in <mark>.
"""

import json
import re
import unicodedata

HL_START, HL_END = "\x02", "\x03"


# Filters in the query: "#silt", "-#silt" (not), "kaust:kool" (the folder and below).
_FILTER_RE = re.compile(r"(?<!\S)(-?)#([^\s#]+)|(?<!\S)kaust:(\S+)", re.I)


def search(conn, q, limit=20):
    """[{slug, title, snippet, folder, hidden}] best first. Every word of q must match the start of
    a word in the note; diacritics fold both ways ("too" finds "töö"). q is never read as FTS syntax.
    Filters (_FILTER_RE) narrow it; filters alone list the matching notes, newest first."""
    where, args = ["n.deleted_at IS NULL"], []
    for neg, tag, folder in _FILTER_RE.findall(q):
        if tag:
            where.append(("NOT " if neg else "") + "EXISTS (SELECT 1 FROM json_each(n.tags_json) WHERE lower(value) = lower(?))")
            args.append(tag)
        else:
            folder = folder.strip("/")
            where.append("(lower(n.folder) = lower(?) OR lower(n.folder) LIKE lower(?) || '/%')")
            args += [folder, folder]
    words = re.findall(r"\w+", _FILTER_RE.sub(" ", q))
    if not words:
        if len(where) == 1:
            return []
        rows = conn.execute(
            f"SELECT n.slug, n.title, '' AS snippet, n.folder, n.frontmatter_extra FROM notes n WHERE {' AND '.join(where)} "
            "ORDER BY n.updated_at DESC, n.id DESC LIMIT ?", (*args, limit))
        return [_hit(r) for r in rows]
    # Each word quoted as an FTS string: '"', '*', NEAR, AND, '-' in q stay plain text.
    query = " ".join(f'"{w}"*' for w in words)
    rows = conn.execute(
        "SELECT n.slug, n.title, snippet(notes_fts, 1, ?, ?, '…', 12) AS snippet, n.folder, n.frontmatter_extra "
        f"FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? AND {' AND '.join(where)} "
        "ORDER BY bm25(notes_fts, 10.0, 1.0) LIMIT ?", (HL_START, HL_END, query, *args, limit))
    return [_hit(r) for r in rows]


def _hit(row):
    from . import notes
    return {"slug": row["slug"], "title": row["title"], "snippet": row["snippet"], "folder": row["folder"],
            "hidden": notes.is_hidden(row)}


# Words too common to say two notes are alike.
_STOP = set("""ning kuid aga või ega kui siis see seda selle need nende neid mis mida mille kes keda kelle
kus kust kuhu kuna sest et ka ja on oli olid olen oled oleme olete pole ei ära veel juba ainult
kõik kõike kõiki iga mõni mõned palju vähe väga rohkem hästi siin seal nüüd kord korda
mina sina tema meie teie nemad minu sinu mulle sulle talle meile teile nagu ilma koos järgi kohta
vastu poole üle alla peale enne pärast ümber vahel läbi
this that with from have were been they them their there what when which will would about into""".split())


def similar(conn, note, limit=8):
    """[{slug, title, folder}] of other live notes sharing words with this one (bm25), daily notes
    left out: the note's title words, its tags and its 15 most frequent body words of 4+ letters."""
    from collections import Counter

    from . import notes
    counts = Counter(w for w in re.findall(r"\w{4,}", notes.mask_code(note["body"]).lower())
                     if w not in _STOP and not w.isdigit())
    terms = [*re.findall(r"\w{3,}", note["title"].lower()), *(t.lower() for t in json.loads(note["tags_json"])),
             *(w for w, _ in counts.most_common(15))]
    terms = list(dict.fromkeys(t for t in terms if t not in _STOP and re.search(r"[^\W\d]", t)))
    if not terms:
        return []
    query = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
    rows = conn.execute(
        "SELECT n.slug, n.title, n.folder FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid "
        "WHERE notes_fts MATCH ? AND n.deleted_at IS NULL AND n.is_daily = 0 AND n.id != ? "
        "ORDER BY bm25(notes_fts, 10.0, 1.0) LIMIT ?", (query, note["id"], limit))
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
