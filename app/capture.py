"""Quick capture from the command palette: one line of text becomes a calendar event, a task in
today's daily note, or a new note (docs/PLAN-OVERVIEW.md §12, "mate kontrolltöö reedel 10.15").

Order of the rules: task first ("- [ ] tee ülesanded @reede" names a date but is a task), then a
date or time anywhere makes an event, and anything else is the title of a new note.
"""

import re
from datetime import datetime

from . import etdates, events, notes, tasks, util

# "- [ ] ...", "[ ] ...", "ülesanne: ...", "todo ..."
_TASK_PREFIX = re.compile(r"^\s*(?:-\s*)?\[\s?\]\s*|^\s*(?:ülesanne|todo)(?!\w)[\s:,-]*", re.I)
_KINDS = (
    (re.compile(r"(?<!\w)(?:kontrolltöö\w*|kt|test\w*)(?!\w)", re.I), "kontrolltöö"),
    (re.compile(r"(?<!\w)(?:kodutöö\w*|ülesan\w*)(?!\w)", re.I), "kodutöö"),
)
# etdates reads a bare "10.15" as a date (it cannot tell it from "9.10"); right after a date that
# has no time, the palette's own example "reedel 10.15" means a time.
_BARE_TIME = re.compile(r"[ \t]+(\d{1,2})[.:](\d{2})(?![\w.:])")
# A time with no date ("trenn kell 18") is today; etdates.find only reports times next to dates.
_TIME = re.compile(r"(?<![\w.:])(?:(?:kella|kell|kl)\.?\s*\d{1,2}(?:[.:]\d{2})?|\d{1,2}:\d{2})(?!\w|[.:]\d)", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def capture(conn, text, *, preview=False):
    """What text becomes, created unless preview: {"kind": "event", id, title, start} |
    {"kind": "task", slug, text} | {"kind": "note", slug, title}. NoteBusy when today's daily note
    is open in an editor."""
    text = " ".join(text.split())
    today = util.local_today()
    task = _task_text(text, today)
    if task is not None:
        return _add_task(conn, task, today, preview)
    found = etdates.find(text, today)
    if found:
        f = found[0]
        return _add_event(conn, text, f["date"], f["time"], f["start"], f["end"], preview)
    m = _TIME.search(text)
    if m and (clock := etdates.parse_time(m[0])):
        return _add_event(conn, text, today, clock, m.start(), m.end(), preview)
    if preview:
        return {"kind": "note", "slug": None, "title": text}
    note = notes.create(conn, text)
    return {"kind": "note", "slug": note["slug"], "title": note["title"]}


def kind_of(text):
    return next((kind for pattern, kind in _KINDS if pattern.search(text)), "sündmus")


def _task_text(text, today):
    """The task's text when text is a task, else None."""
    rest = _TASK_PREFIX.sub("", text, count=1)
    if rest != text and rest:
        return rest
    item = tasks.parse(f"- [ ] {text}", today)
    return text if item and item[0]["due_date"] else None


def _add_task(conn, task, today, preview):
    line = f"- [ ] {task}"
    if preview:
        return {"kind": "task", "slug": None, "text": task}
    note = notes.daily(conn, today)
    body = note["body"].rstrip("\n")
    last = body.rsplit("\n", 1)[-1]
    # Right under another list item it joins that list; otherwise it starts one.
    sep = "\n" if _LIST_ITEM.match(last) else "\n\n"
    body = f"{body}{sep}{line}\n"
    notes.write_body(conn, note["id"], body)
    return {"kind": "task", "slug": note["slug"], "text": task}


def _add_event(conn, text, day, clock, start, end, preview):
    if clock is None and (m := _BARE_TIME.match(text, end)) and int(m[1]) < 24 and int(m[2]) < 60:
        clock, end = f"{int(m[1]):02d}:{m[2]}", m.end()
    title = " ".join((text[:start] + " " + text[end:]).split()).strip(" ,.;:-")
    kind = kind_of(text)
    title = title or kind.capitalize()
    fields = {"title": title, "kind": kind}
    if clock:
        at = datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), tzinfo=util.TALLINN)
        fields.update(all_day=0, start_at=util.iso(at))
        local_start = f"{day.isoformat()}T{clock}"
    else:
        fields.update(all_day=1, start_date=day.isoformat())
        local_start = day.isoformat()
    event_id = None if preview else events.create(conn, fields)
    return {"kind": "event", "id": event_id, "title": title, "start": local_start, "event_kind": kind}
