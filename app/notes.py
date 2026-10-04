"""Notes: create, read, move, tag, delete, daily notes, and the bookkeeping after every body write
(title, links, tasks). Signatures in docs/CONTRACTS.md §4 notes.

The title is always derived from the body (first "# " heading), so the editor, git import and
server-side writers can never disagree about it. Slugs are fixed at creation.
"""

import json
import re

from . import etdates, util
from .db import tx

UNTITLED = "Pealkirjata"
DAILY_FOLDER = "paevik"
TITLE_MAX = 200
# /api/notes/tree and /api/notes/suggest would shadow notes with these slugs.
RESERVED_SLUGS = {"tree", "suggest"}


class NoteBusy(Exception):
    """The note is open in an editor (collab.is_busy); a server-side write must not race it."""


def create(conn, title="", *, folder="", body=None, is_daily=False, daily_date=None):
    """New note. body=None makes "# {title}\n"; a body without a "# " heading gets one from title,
    so the title given is the title kept. Links elsewhere that were waiting for this slug resolve."""
    title = title.strip()
    if body is None:
        body = f"# {title}\n"
    elif title and not _has_h1(body):
        body = f"# {title}\n\n{body}"
    folder = normalize_folder(folder)
    source = daily_date if is_daily and daily_date else title
    with tx(conn):
        base = util.slugify(source)
        taken = {r["slug"] for r in conn.execute(
            "SELECT slug FROM notes WHERE slug = ? OR slug LIKE ? || '-%'", (base, base))}
        slug = util.slugify(source, taken | RESERVED_SLUGS)
        note_id = conn.execute(
            "INSERT INTO notes (slug, title, body, folder, is_daily, daily_date) VALUES (?, ?, ?, ?, ?, ?)",
            (slug, title, body, folder, int(is_daily), daily_date),
        ).lastrowid
        conn.execute("UPDATE links SET dst_id = ? WHERE dst_slug = ? AND dst_id IS NULL", (note_id, slug))
        on_saved(conn, note_id)
    return get(conn, note_id)


def get(conn, note_id):
    return conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()


def by_slug(conn, slug):
    return conn.execute("SELECT * FROM notes WHERE slug = ? AND deleted_at IS NULL", (slug,)).fetchone()


def tree(conn):
    rows = conn.execute(
        "SELECT id, slug, title, folder, pinned, is_daily, updated_at, frontmatter_extra FROM notes "
        "WHERE deleted_at IS NULL ORDER BY folder, title COLLATE NOCASE")
    return [{**{k: r[k] for k in r.keys() if k != "frontmatter_extra"}, "pinned": bool(r["pinned"]),
             "is_daily": bool(r["is_daily"]), "hidden": is_hidden(r)} for r in rows]


# Hidden notes stay out of the sidebar's lists until "Näita peidetuid". The mark is the frontmatter
# key `peidetud: true`, so it goes to git with the note and the other machine sees it too.
HIDDEN_KEY = "peidetud"


def is_hidden(row):
    return json.loads(row["frontmatter_extra"] or "{}").get(HIDDEN_KEY) is True


def normalize_folder(folder):
    """' /kool//matemaatika/ ' -> 'kool/matemaatika'. ValueError for '..'."""
    parts = [p.strip() for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p != "."]
    if ".." in parts:
        raise ValueError("Vigane kaust.")
    return "/".join(parts)


# Metadata changes touch updated_at but not rev: rev counts body writes, and bumping it would end
# the collab session of whoever has the note open.

def move(conn, note_id, folder):
    _set(conn, note_id, "folder", normalize_folder(folder))


def set_tags(conn, note_id, tags):
    _set(conn, note_id, "tags_json", json.dumps(_clean_list(t.lstrip("#") for t in tags), ensure_ascii=False))


def set_aliases(conn, note_id, aliases):
    _set(conn, note_id, "aliases_json", json.dumps(_clean_list(aliases), ensure_ascii=False))


def set_pinned(conn, note_id, pinned):
    _set(conn, note_id, "pinned", int(bool(pinned)))


def set_hidden(conn, note_id, hidden):
    with tx(conn):
        row = conn.execute("SELECT frontmatter_extra FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return
        extra = json.loads(row["frontmatter_extra"] or "{}")
        if hidden:
            extra[HIDDEN_KEY] = True
        else:
            extra.pop(HIDDEN_KEY, None)
        _set(conn, note_id, "frontmatter_extra", json.dumps(extra, ensure_ascii=False))


def soft_delete(conn, note_id):
    """Hide the note. Links to it dangle again; its own links and tasks go. daily_date is released so
    the day can get a fresh daily note."""
    from . import collab
    with tx(conn):
        conn.execute("UPDATE notes SET deleted_at = ?, daily_date = NULL WHERE id = ? AND deleted_at IS NULL",
                     (util.now_iso(), note_id))
        conn.execute("UPDATE links SET dst_id = NULL WHERE dst_id = ?", (note_id,))
        conn.execute("DELETE FROM links WHERE src_id = ?", (note_id,))
        conn.execute("DELETE FROM tasks WHERE note_id = ?", (note_id,))
        collab.close_all(conn, note_id)


def daily(conn, day):
    """The daily note for day (slug = ISO date), created in "paevik" when missing."""
    with tx(conn):
        row = conn.execute("SELECT * FROM notes WHERE daily_date = ? AND deleted_at IS NULL",
                           (day.isoformat(),)).fetchone()
        return row or create(conn, etdates.long_date(day), folder=DAILY_FOLDER, is_daily=True,
                             daily_date=day.isoformat())


def write_body(conn, note_id, body):
    """Server-side write (task toggle, "Seo", AI tools, git import). NoteBusy while the note is open
    in an editor. Returns the new rev."""
    from . import collab
    with tx(conn):
        if collab.is_busy(conn, note_id):
            raise NoteBusy
        row = conn.execute(
            "UPDATE notes SET body = ?, rev = rev + 1, updated_at = ? WHERE id = ? AND deleted_at IS NULL "
            "RETURNING rev", (body, util.now_iso(), note_id)).fetchone()
        if row is None:
            raise LookupError(note_id)
        on_saved(conn, note_id)
        return row["rev"]


def on_saved(conn, note_id):
    """After any body write: title from the body, then links and tasks rebuilt."""
    from . import links, tasks
    with tx(conn):
        note = get(conn, note_id)
        if note is None:
            return
        title = derive_title(note["body"])
        if title != note["title"]:
            conn.execute("UPDATE notes SET title = ? WHERE id = ?", (title, note_id))
        links.rebuild(conn, note_id, note["body"])
        tasks.rebuild(conn, note)


# --- Markdown helpers shared by links, tasks and search -------------------------------------

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_H1_RE = re.compile(r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_STRIP = [
    (re.compile(r"^\s{0,3}(?:>\s?)+"), ""),                           # blockquote
    (re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?"), ""),   # list item, task box
    (re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)"), ""),                     # heading
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),                  # link, image -> text
    (re.compile(r"<[^>]+>"), ""),                                      # html tag
    (re.compile(r"\*\*|__|~~|`|\*|(?<!\w)_|_(?!\w)"), ""),            # emphasis, code
]


def mask_code(text):
    """text with fenced code blocks and inline code spans blanked to spaces (newlines kept), so
    offsets still match the original. Indented code is not recognised: nested lists use indents."""
    out, fence = [], None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if fence is None:
            # A backtick fence's info string cannot contain backticks: "```a```" is inline code.
            if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
                fence = m.group(1)
                line = " " * len(line)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
                fence = None
            line = " " * len(line)
        out.append(line)
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), "\n".join(out))


def strip_markdown(line):
    """One line of Markdown as plain text: '- [ ] **Tee** [see](/m/x)' -> 'Tee see'."""
    for pattern, repl in _STRIP:
        line = pattern.sub(repl, line)
    return " ".join(line.split())


def derive_title(body):
    """First "# " heading outside code, else the first non-empty line, else "Pealkirjata"."""
    lines = _visible_lines(body)
    for line in lines:
        m = _H1_RE.match(line)
        if m and (title := strip_markdown(m.group(1))):
            return title[:TITLE_MAX]
    for line in lines:
        if title := strip_markdown(line):
            return title[:TITLE_MAX]
    return UNTITLED


def _has_h1(body):
    return any(_H1_RE.match(line) for line in _visible_lines(body))


def _visible_lines(body):
    """Lines with something outside code on them."""
    return [line for line, masked in zip(body.split("\n"), mask_code(body).split("\n")) if masked.strip()]


def _clean_list(items):
    out, seen = [], set()
    for item in items:
        item = " ".join(str(item).split())
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


def _set(conn, note_id, column, value):
    conn.execute(f"UPDATE notes SET {column} = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                 (value, util.now_iso(), note_id))
