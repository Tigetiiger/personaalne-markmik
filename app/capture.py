"""Quick capture from the command palette: one line of text becomes a calendar event, a task in
today's daily note, or a new note (docs/PLAN-OVERVIEW.md §12, "mate kontrolltöö reedel 10.15").

Order of the rules: task first ("- [ ] tee ülesanded @reede" names a date but is a task), then a
date or time anywhere makes an event, and anything else is the title of a new note.
"""

import re
from datetime import datetime, timedelta

from . import etdates, events, notes, tasks, util
from .db import tx

MAX_TEXT = 500
DATE_AHEAD = timedelta(days=56)
EARLIEST_HOUR = 6

# "- [ ] ...", "[ ] ...", "ülesanne: ...", "todo ..."
_TASK_PREFIX = re.compile(r"^\s*(?:-\s*)?\[\s?\]\s*|^\s*(?:ülesanne|todo)(?!\w)[\s:,-]*", re.I)
_KINDS = (
    (re.compile(r"(?<!\w)(?:kontrolltöö\w*|kt|test\w*)(?!\w)", re.I), "kontrolltöö"),
    (re.compile(r"(?<!\w)(?:kodutöö\w*|ülesan\w*)(?!\w)", re.I), "kodutöö"),
)
# etdates reads a bare "10.15" as a date (it cannot tell it from "9.10"). Right after a day word
# ("reedel 8.05", "homme 9.10") it is a time here, unless it makes sense as a date: after a weekday,
# a date on that weekday in the next DATE_AHEAD days ("reedel 16.10"); after "homme" and the like,
# that very day. An hour before 6 is never a time ("reedel 3.10").
_WORD_THEN_CLOCK = re.compile(r"(?<!\w)(?P<word>[^\W\d_]+)(?P<gap>,?[ \t]+)(?P<h>\d{1,2})[.:](?P<m>\d{2})(?![\w.:])")
_BARE_TIME = re.compile(r"[ \t]+(\d{1,2})[.:](\d{2})(?![\w.:])")  # a bare H.MM right after a date
# A time anywhere ("kell 18 trenn homme"); a time with no date is today.
_TIME = re.compile(r"(?<![\w.:])(?:(?:kella|kell|kl)\.?\s*\d{1,2}(?:[.:]\d{2})?|\d{1,2}:\d{2})(?!\w|[.:]\d)", re.I)
# The end of a time range: "14:00-15:00", "kell 9.15–10".
_END = re.compile(r"[ \t]*[-–][ \t]*(?:kell[ \t]*)?(\d{1,2})(?:[.:](\d{2}))?(?![\w.:])", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def capture(conn, text, *, preview=False):
    """What text becomes, created unless preview: {"kind": "event", id, title, start,
    event_kind, untitled} | {"kind": "task", slug, text} | {"kind": "note", slug, title}; untitled:
    the text was nothing but the date ("reede"), the title is the kind's name. NoteBusy when
    today's daily note is open in an editor; ValueError (Estonian) when the event is invalid."""
    text = " ".join(text.split())
    today = util.local_today()
    task = _task_text(text, today)
    if task is not None:
        return _add_task(conn, task, today, preview)
    text = _clock_after_day(text, today)
    when = _when(text, today)
    if when:
        return _add_event(conn, text, *when, preview)
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
    if preview:
        return {"kind": "task", "slug": None, "text": task}
    with tx(conn):
        note = notes.daily(conn, today)
        body = note["body"].rstrip("\n")
        # Right under another list item it joins that list; otherwise it starts one.
        sep = "\n" if _LIST_ITEM.match(body.rsplit("\n", 1)[-1]) else "\n\n"
        notes.write_body(conn, note["id"], f"{body}{sep}- [ ] {task}\n")
    return {"kind": "task", "slug": note["slug"], "text": task}


def _clock_after_day(text, today):
    """'reedel 8.05' -> 'reedel kell 8.05' (see _WORD_THEN_CLOCK)."""
    def fix(m):
        found = etdates.find(m["word"], today)
        if len(found) != 1 or found[0]["end"] != len(m["word"]) or not EARLIEST_HOUR <= int(m["h"]) <= 23 or int(m["m"]) > 59:
            return m[0]
        day = found[0]["date"]
        as_date = etdates.parse_date(f"{m['h']}.{m['m']}", today)
        # A weekday word conflicts with any other weekday; "homme" conflicts with none.
        weekday = etdates.weekday_conflict(m["word"], day + timedelta(days=1))
        if as_date and (as_date == day or (weekday and as_date.weekday() == day.weekday() and today <= as_date <= today + DATE_AHEAD)):
            return m[0]
        return f"{m['word']}{m['gap']}kell {m['h']}.{m['m']}"
    return _WORD_THEN_CLOCK.sub(fix, text)


def _when(text, today):
    """(day, start 'HH:MM' | None, end 'HH:MM' | None, spans to cut from the title) for the first
    date in text and the time that goes with it, or None when text names neither."""
    found = etdates.find(text, today)
    day, clock, spans = today, None, []
    if found:
        first = found[0]
        day, clock, spans = first["date"], first["time"], [(first["start"], first["end"])]
        if clock is None and (m := _BARE_TIME.match(text, first["end"])) and int(m[1]) < 24 and int(m[2]) < 60:
            clock, spans = f"{int(m[1]):02d}:{m[2]}", [(first["start"], m.end())]
    at = spans[0][1] if clock else None  # where the time ends, for a "-15:00" after it
    if clock is None:
        for m in _TIME.finditer(text):
            if not any(s < m.end() and m.start() < e for s, e in spans) and (clock := etdates.parse_time(m[0])):
                spans.append(m.span())
                at = m.end()
                break
    if not spans:
        return None
    end = None
    if clock and (m := _END.match(text, at)):
        candidate = f"{int(m[1]):02d}:{m[2] or '00'}"
        if int(m[1]) < 24 and int(m[2] or 0) < 60 and candidate > clock:
            end = candidate
            spans.append(m.span())
    return day, clock, end, spans


def _add_event(conn, text, day, clock, end, spans, preview):
    title = text
    for s, e in sorted(spans, reverse=True):
        title = f"{title[:s]} {title[e:]}"
    kind = kind_of(text)
    title = " ".join(title.split()).strip(" ,.;:-")
    untitled = not title
    title = title or kind.capitalize()
    fields = {"title": title, "kind": kind}
    if clock:
        fields.update(all_day=0, start_at=_utc(day, clock), end_at=_utc(day, end) if end else None)
        start = f"{day.isoformat()}T{clock}"
    else:
        fields.update(all_day=1, start_date=day.isoformat())
        start = day.isoformat()
    event_id = None if preview else events.create(conn, fields)
    return {"kind": "event", "id": event_id, "title": title, "start": start, "event_kind": kind, "untitled": untitled}


def _utc(day, hhmm):
    return util.iso(datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time(), tzinfo=util.TALLINN))
