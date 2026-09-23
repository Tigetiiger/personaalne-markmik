"""Tasks in notes: `- [ ] tee ülesanded @reede 14.15`, parsed on every save into the tasks table,
and ticked from the calendar or Täna by writing the note (docs/PLAN-OVERVIEW.md §7).

The due token is `@` + one word or date, after other text on the line (a bare "@..." paragraph
opens Muya's block menu), optionally followed by a time: `@2026-10-03`, `@3.10 14:15`, `@reede`,
`@homme kell 9`. It resolves against the note's own day for a daily note, else against the day of
the save that first contained the line: an unchanged line keeps its due date on later saves.
"""

import hashlib
import re
from datetime import date

from . import etdates, notes, util
from .db import tx

_TASK_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[(?P<mark>[ xX])\](?:[ \t][^\n]*)?$", re.M)
_DUE_RE = re.compile(
    r"(?<=\S)[ \t]+@(?P<token>[^\s@]+)"
    r"(?:[ \t]+(?P<time>(?:kella|kell|kl)\.?[ \t]*\d{1,2}(?:[.:]\d{2})?|\d{1,2}[.:]\d{2})(?![\w.:]))?", re.I)


def parse(body, ref):
    """Task items outside code: [{key, text, done, due_date, due_time, mark_pos}]. text is the item
    without its checkbox and due token; mark_pos is the index of the ' '/'x' inside the brackets."""
    masked = notes.mask_code(body)
    items, seen = [], {}
    for m in _TASK_RE.finditer(masked):
        # Text from the original: code spans are blank in the masked copy.
        start, end = m.end("mark") + 1, m.end()
        raw = body[start:end]
        normal = " ".join(raw.split())
        seen[normal] = seen.get(normal, -1) + 1
        text, due_date, due_time = normal, None, None
        for due in _DUE_RE.finditer(masked[start:end]):
            day = _token_date(due["token"], ref)
            if day:
                due_date = day.isoformat()
                if due["time"]:
                    t = due["time"]
                    due_time = etdates.parse_time(t if t[0].isalpha() else t.replace(".", ":"))
                text = " ".join((raw[:due.start()] + raw[due.end():]).split())
                break
        items.append({
            "key": hashlib.sha1(normal.encode()).hexdigest() + f":{seen[normal]}",
            "text": text, "done": m["mark"] != " ", "due_date": due_date, "due_time": due_time,
            "mark_pos": m.start("mark"),
        })
    return items


def rebuild(conn, note):
    """Make the note's rows in tasks match its body."""
    ref = date.fromisoformat(note["daily_date"]) if note["is_daily"] and note["daily_date"] else util.local_today()
    items = parse(note["body"], ref)
    with tx(conn):
        keys = {i["key"] for i in items}
        for row in conn.execute("SELECT id, line_key FROM tasks WHERE note_id = ?", (note["id"],)).fetchall():
            if row["line_key"] not in keys:
                conn.execute("DELETE FROM tasks WHERE id = ?", (row["id"],))
        for i in items:
            conn.execute(
                "INSERT INTO tasks (note_id, line_key, text, done, due_date, due_time) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (note_id, line_key) DO UPDATE SET text = excluded.text, done = excluded.done",
                (note["id"], i["key"], i["text"], int(i["done"]), i["due_date"], i["due_time"]))


def toggle(conn, task_id):
    """Flip the task's checkbox in its note (a server-side write: NoteBusy propagates). Returns the
    new done state, or None when the task no longer exists."""
    with tx(conn):
        row = conn.execute("SELECT note_id, line_key FROM tasks WHERE id = ?", (task_id,)).fetchone()
        note = notes.get(conn, row["note_id"]) if row else None
        if note is None:
            return None
        item = next((i for i in parse(note["body"], util.local_today()) if i["key"] == row["line_key"]), None)
        if item is None:
            return None
        pos, body = item["mark_pos"], note["body"]
        notes.write_body(conn, note["id"], body[:pos] + (" " if item["done"] else "x") + body[pos + 1:])
        return not item["done"]


def _token_date(token, ref):
    """The date a whole @token names ("reede", "3.10.", "2026-10-03"), or None ("@mari")."""
    token = token.rstrip(".,;:!?)")
    found = etdates.find(token, ref)
    if len(found) == 1 and found[0]["start"] == 0 and found[0]["end"] == len(token):
        return found[0]["date"]
    return None
