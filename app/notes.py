"""Notes: create, read, move, tag, delete, daily notes, and the bookkeeping after every body write
(title, links, tasks). Signatures in docs/CONTRACTS.md §4 notes.

The title is always derived from the body (first "# " heading), so the editor, git import and
server-side writers can never disagree about it. Slugs are fixed at creation.
"""

import json
import re
from datetime import datetime, timedelta

from . import etdates, util
from .db import tx
from .jobs import job

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
        slug = _free_slug(conn, source)
        note_id = conn.execute(
            "INSERT INTO notes (slug, title, body, folder, is_daily, daily_date) VALUES (?, ?, ?, ?, ?, ?)",
            (slug, title, body, folder, int(is_daily), daily_date),
        ).lastrowid
        if not util.slug_base(source):
            conn.execute("INSERT INTO note_auto_slug (note_id) VALUES (?)", (note_id,))
        conn.execute("UPDATE links SET dst_id = ? WHERE dst_slug = ? AND dst_id IS NULL", (note_id, slug))
        on_saved(conn, note_id)
    return get(conn, note_id)


def _free_slug(conn, source):
    """A slug for source that no note has, deleted ones included, and no note had (old_slugs)."""
    base = util.slugify(source)
    taken = {r[0] for r in conn.execute(
        "SELECT slug FROM notes WHERE slug = ?1 OR slug LIKE ?1 || '-%' "
        "UNION SELECT slug FROM old_slugs WHERE slug = ?1 OR slug LIKE ?1 || '-%'", (base,))}
    return util.slugify(source, taken | RESERVED_SLUGS)


def get(conn, note_id):
    return conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()


def by_slug(conn, slug):
    """The live note with this slug, or with it as a slug it had before (old_slugs)."""
    return conn.execute("SELECT * FROM notes WHERE slug = ? AND deleted_at IS NULL", (slug,)).fetchone() or conn.execute(
        "SELECT n.* FROM old_slugs o JOIN notes n ON n.id = o.note_id WHERE o.slug = ? AND n.deleted_at IS NULL",
        (slug,)).fetchone()


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


def rename_folder(conn, old, new):
    """Every note in folder `old` and below moves to `new` (a folder there already: they merge),
    trashed notes too, so a restore lands in the new place; templates whose mall_kaust named it
    follow. Returns the number of notes moved. ValueError (Estonian) for an empty or bad name."""
    old, new = normalize_folder(old), normalize_folder(new)
    if not old or not new:
        raise ValueError("Kausta nimi ei saa olla tühi.")
    if new == old:
        raise ValueError("Nimi on sama.")
    if new.startswith(old + "/"):
        raise ValueError("Kausta ei saa viia iseenda sisse.")

    def moved(folder):
        return new + folder[len(old):] if folder == old or folder.startswith(old + "/") else None

    with tx(conn):
        # substr, not LIKE: a folder name may hold "%" or "_".
        rows = conn.execute("SELECT id, folder FROM notes WHERE folder = ?1 OR substr(folder, 1, length(?1) + 1) = ?1 || '/'",
                            (old,)).fetchall()
        for row in rows:
            conn.execute("UPDATE notes SET folder = ?, updated_at = ? WHERE id = ?", (moved(row["folder"]), util.now_iso(), row["id"]))
        for row in conn.execute("SELECT id, frontmatter_extra FROM notes WHERE frontmatter_extra LIKE '%mall_kaust%'").fetchall():
            target = json.loads(row["frontmatter_extra"]).get("mall_kaust")
            if isinstance(target, str) and moved(normalize_folder(target)) is not None:
                _set_extra(conn, row["id"], "mall_kaust", moved(normalize_folder(target)))
    return len(rows)


def set_tags(conn, note_id, tags):
    _set(conn, note_id, "tags_json", json.dumps(_clean_list(t.lstrip("#") for t in tags), ensure_ascii=False))


def set_aliases(conn, note_id, aliases):
    _set(conn, note_id, "aliases_json", json.dumps(_clean_list(aliases), ensure_ascii=False))


def set_pinned(conn, note_id, pinned):
    _set(conn, note_id, "pinned", int(bool(pinned)))


def set_hidden(conn, note_id, hidden):
    _set_extra(conn, note_id, HIDDEN_KEY, True if hidden else None)


# Python cells run by themselves after a change unless the note says `arvutus: käsitsi`
# (docs/JUPYTER-PLAN.md); in the frontmatter, so it travels with the note.
MANUAL_KEY, MANUAL = "arvutus", "käsitsi"


def runs_by_itself(row):
    return json.loads(row["frontmatter_extra"] or "{}").get(MANUAL_KEY) != MANUAL


def set_runs_by_itself(conn, note_id, on):
    _set_extra(conn, note_id, MANUAL_KEY, None if on else MANUAL)


def _set_extra(conn, note_id, key, value):
    """One frontmatter key kept for git: set, or removed with value None."""
    with tx(conn):
        row = conn.execute("SELECT frontmatter_extra FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return
        extra = json.loads(row["frontmatter_extra"] or "{}")
        if value is None:
            extra.pop(key, None)
        else:
            extra[key] = value
        _set(conn, note_id, "frontmatter_extra", json.dumps(extra, ensure_ascii=False))


def soft_delete(conn, note_id):
    """Hide the note. Links to it dangle again; its own links and tasks go. daily_date is released so
    the day can get a fresh daily note."""
    from . import collab
    with tx(conn):
        # updated_at too: purge() waits PURGE_WAIT after it, so no export still writing the file is
        # left without a row.
        conn.execute("UPDATE notes SET deleted_at = ?1, updated_at = ?1, daily_date = NULL WHERE id = ?2 AND deleted_at IS NULL",
                     (util.now_iso(), note_id))
        conn.execute("UPDATE links SET dst_id = NULL WHERE dst_id = ?", (note_id,))
        conn.execute("DELETE FROM links WHERE src_id = ?", (note_id,))
        conn.execute("DELETE FROM tasks WHERE note_id = ?", (note_id,))
        collab.close_all(conn, note_id)


# Deleted notes wait in the trash this long, then purge() takes them for good (docs/MARKMED-PLAN.md).
TRASH_DAYS = 30
PURGE_WAIT = timedelta(minutes=10)  # after the delete: longer than any export run that began before it
_EXPIRED = "1970-01-01T00:00:00Z"  # deleted_at of a note deleted for good, waiting for purge()


def trash(conn):
    """[{slug, title, folder, deleted_at}] of deleted notes still in the trash, newest first."""
    rows = conn.execute(
        "SELECT slug, title, folder, deleted_at FROM notes WHERE deleted_at > ? ORDER BY deleted_at DESC, id DESC",
        (_trash_cutoff(),))
    return [dict(r) for r in rows]


def in_trash(conn, slug):
    return conn.execute("SELECT * FROM notes WHERE slug = ? AND deleted_at > ?", (slug, _trash_cutoff())).fetchone()


def restore(conn, note_id):
    """Take the note out of the trash: links to it and its own links and tasks come back. A daily
    note gets its day back unless that day has a new daily note by then (it then stays an ordinary
    note). The git export writes its file again."""
    with tx(conn):
        row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NOT NULL", (note_id,)).fetchone()
        if row is None:
            return None
        day = row["slug"] if row["is_daily"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["slug"]) else None
        if day and conn.execute("SELECT 1 FROM notes WHERE daily_date = ?", (day,)).fetchone():
            day = None
        conn.execute("UPDATE notes SET deleted_at = NULL, updated_at = ?, is_daily = ?, daily_date = ? WHERE id = ?",
                     (util.now_iso(), int(day is not None), day, note_id))
        conn.execute("UPDATE links SET dst_id = ? WHERE dst_slug = ? AND dst_id IS NULL", (note_id, row["slug"]))
        on_saved(conn, note_id)
    return get(conn, note_id)


def forget(conn, note_id):
    """Delete a note in the trash for good: it leaves the trash now and the table with the next
    purge() that finds git without its file."""
    conn.execute("UPDATE notes SET deleted_at = ? WHERE id = ? AND deleted_at IS NOT NULL", (_EXPIRED, note_id))


@job("notes-purge", every=3600)
def purge(conn):
    """Notes longer than TRASH_DAYS in the trash (or deleted for good) leave the table, but only once
    the git export has removed their file (file_path NULL) and PURGE_WAIT has passed since the
    delete: a row gone too early would let the next pull bring the file back as a new note. Their
    links, tasks, shares and the rest go by the foreign keys."""
    with tx(conn):
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM notes WHERE deleted_at <= ? AND updated_at <= ? AND file_path IS NULL",
            (_trash_cutoff(), util.iso(util.now_utc() - PURGE_WAIT)))]
        for note_id in ids:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return len(ids)


def _trash_cutoff():
    return util.iso(util.now_utc() - timedelta(days=TRASH_DAYS))


# --- New notes: a blank one left goes, an automatic slug follows the title once -------------

BLANK_AGE = timedelta(hours=1)   # the sweep's wait before a blank new note goes
NAMED_AGE = timedelta(minutes=10)  # ... and before a named one takes its title's slug


def is_blank(body):
    """Nothing but an empty "# " line: a new note nobody wrote in."""
    return re.fullmatch(r"\s*(?:#[ \t]*)?\s*", body) is not None


def settle(conn, note_id, *, swept=False):
    """The writer left the note (POST /api/notes/<slug>/lahkun), or no word came and the sweep asks
    (swept). A blank new note goes: for good (purge() takes the row), or into the trash when git
    already has its file; never while it is shared. Not after any operation in its editor sessions
    (something typed may still be on its way), unless swept; when swept, not while an editor is
    open on it. A note still on its automatic slug takes its title's and keeps it. Returns the
    note's slug, or None when it went."""
    from . import collab, links
    with tx(conn):
        note = get(conn, note_id)
        if note is None:
            return None
        if not conn.execute("SELECT 1 FROM note_auto_slug WHERE note_id = ?", (note_id,)).fetchone():
            return note["slug"]
        if is_blank(note["body"]):
            if (collab.is_open(conn, note_id) if swept else _typed_in(conn, note_id)) or conn.execute(
                    "SELECT 1 FROM shares WHERE note_id = ? AND revoked_at IS NULL", (note_id,)).fetchone():
                return note["slug"]
            in_git = note["file_path"] is not None
            soft_delete(conn, note_id)
            if not in_git:
                forget(conn, note_id)
            return None
        if note["title"] == UNTITLED:
            return note["slug"]
        conn.execute("DELETE FROM note_auto_slug WHERE note_id = ?", (note_id,))
        base = util.slug_base(note["title"])
        if not base or re.fullmatch(rf"{re.escape(base)}(?:-\d+)?", note["slug"]):
            return note["slug"]  # the title makes no other slug ("Märge" on marge-3, "???")
        slug = _free_slug(conn, note["title"])
        conn.execute("UPDATE notes SET slug = ?, updated_at = ? WHERE id = ?", (slug, util.now_iso(), note_id))
        conn.execute("INSERT OR REPLACE INTO old_slugs (slug, note_id) VALUES (?, ?)", (note["slug"], note_id))
    links.retarget(conn, note["slug"], slug)
    return slug


@job("notes-tidy", every=900)
def tidy(conn):
    """settle() for new notes whose tab went without a word (closed, or another page opened): a
    blank one after BLANK_AGE, a named one after NAMED_AGE, never while someone writes in it. Then
    links still pointing at an old slug (their note was busy at the rename) get the new one."""
    from . import collab, links
    now = util.now_utc()
    rows = conn.execute("SELECT n.id, n.body, n.updated_at FROM note_auto_slug a JOIN notes n ON n.id = a.note_id "
                        "WHERE n.deleted_at IS NULL").fetchall()
    for row in rows:
        age = BLANK_AGE if is_blank(row["body"]) else NAMED_AGE
        if row["updated_at"] <= util.iso(now - age) and not collab.is_busy(conn, row["id"]):
            settle(conn, row["id"], swept=True)
    stale = conn.execute("SELECT DISTINCT o.slug, n.slug FROM links l JOIN old_slugs o ON o.slug = l.dst_slug "
                         "JOIN notes n ON n.id = o.note_id").fetchall()
    for old, new in stale:
        links.retarget(conn, old, new)


def _typed_in(conn, note_id):
    return conn.execute("SELECT 1 FROM collab_ops o JOIN collab_sessions s ON s.id = o.session_id "
                        "WHERE s.note_id = ? LIMIT 1", (note_id,)).fetchone() is not None


DAILY_TEMPLATE = "Päevik"  # a note of that title in Mallid/ is the daily note's template


def daily(conn, day):
    """The daily note for day (slug = ISO date), created in "paevik" when missing: from the template
    "Päevik" in Mallid/ when there is one (its placeholders filled for that day), else empty."""
    from . import mallid
    with tx(conn):
        row = conn.execute("SELECT * FROM notes WHERE daily_date = ? AND deleted_at IS NULL",
                           (day.isoformat(),)).fetchone()
        if row:
            return row
        title, body = etdates.long_date(day), None
        template = next((t for t in conn.execute(
            "SELECT * FROM notes WHERE deleted_at IS NULL AND title = ? ORDER BY id", (DAILY_TEMPLATE,))
            if mallid.is_template(t)), None)
        if template is not None:
            when = datetime.now(util.TALLINN).replace(year=day.year, month=day.month, day=day.day)
            body = mallid.fill(template["body"], when)
            body = mallid.H1.sub(f"# {title}", body, count=1) if mallid.H1.search(body) else f"# {title}\n\n{body}"
        return create(conn, title, folder=DAILY_FOLDER, body=body, is_daily=True, daily_date=day.isoformat())


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
