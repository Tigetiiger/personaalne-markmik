"""Calendar events: create/update with validation, sync upserts, removal, and the range query that
expands recurring series and adds dated tasks. Signatures in docs/CONTRACTS.md §4 events.

Storage (app/schema.sql): timed events are start_at/end_at in UTC plus tzid; all-day events are
start_date/end_date (end exclusive). A series has rrule and exdates_json; an overridden
occurrence is its own row with series_id and recurrence_id.

Occurrence keys (exdates_json entries, recurrence_id, event_done.occurrence) are the
occurrence's original start: 'YYYY-MM-DDTHH:MM:SSZ' (UTC) for a timed series, 'YYYY-MM-DD' for
an all-day one; '' for a non-recurring event. Writes normalise them to that form.
"""

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from icalendar import vRecur

from .db import tx
from .util import TALLINN, iso, now_iso, parse_iso

KINDS = ("tund", "kontrolltöö", "kodutöö", "sündmus", "tähtaeg", "isiklik")
STATUSES = ("kinnitatud", "pakutud", "eemaldatud")
SOURCES = ("käsitsi", "ekool", "gmail", "ics", "ai")
CONFIDENCES = (None, "low", "med", "high")
MAX_TITLE = 200
MAX_LOCATION = 500
MAX_DESCRIPTION = 5000
# Finer rules can make thousands of occurrences a day; a school calendar never needs them.
TOO_FREQUENT = {"SECONDLY", "MINUTELY", "HOURLY"}
SUB_DAY_PARTS = {"BYHOUR", "BYMINUTE", "BYSECOND"}

log = logging.getLogger("markmik.events")

COLUMNS = (
    "uid", "title", "start_at", "end_at", "start_date", "end_date", "all_day", "tzid", "kind",
    "status", "source", "source_ref", "source_quote", "confidence", "location", "description",
    "note_id", "rrule", "exdates_json", "series_id", "recurrence_id", "user_edited",
)
DEFAULTS = {"all_day": 0, "tzid": "Europe/Tallinn", "kind": "sündmus", "status": "kinnitatud",
            "source": "käsitsi", "location": "", "description": "", "exdates_json": "[]", "user_edited": 0}


def get(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def create(conn, fields):
    """Insert an event; `fields` are events columns. Returns the new id. ValueError (Estonian
    message) when the fields are invalid."""
    f = _validate({**DEFAULTS, "uid": f"markmik-{uuid.uuid4()}", **_known(fields)})
    cols = [c for c in COLUMNS if c in f]
    return conn.execute(
        f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [f[c] for c in cols],
    ).lastrowid


def update(conn, event_id, fields, *, by_user=True):
    """Change some columns. by_user marks the row user_edited, so source syncs leave it alone.
    Returns the updated row, or None when there is no such event."""
    fields = _known(fields)
    with tx(conn):
        row = get(conn, event_id)
        if row is None:
            return None
        base = dict(row)
        if "all_day" in fields:
            # Switching between timed and all-day: the caller gives the new side's times.
            base.update(start_at=None, end_at=None, start_date=None, end_date=None)
            fields = {"start_at": None, "end_at": None, "start_date": None, "end_date": None, **fields}
            if bool(fields["all_day"]) != bool(row["all_day"]) and "exdates_json" not in fields:
                fields["exdates_json"] = _flip_keys(_exdates(row["exdates_json"]), fields, row["tzid"])
        merged = _validate({**base, **fields})
        # The whole row, normalised: an old row that had gaps is repaired by any edit.
        changed = {c: merged[c] for c in COLUMNS}
        if by_user:
            changed["user_edited"] = 1
        changed["updated_at"] = now_iso()
        conn.execute(
            f"UPDATE events SET {', '.join(f'{c} = ?' for c in changed)} WHERE id = ?",
            [*changed.values(), event_id],
        )
    return get(conn, event_id)


def upsert_by_uid(conn, uid, fields):
    """Create or refresh the event a source knows as `uid`. Rows the user edited are never
    overwritten; a uid the user removed or rejected is refused: (None, False)."""
    with tx(conn):
        if conn.execute("SELECT 1 FROM rejected_uids WHERE uid = ?", (uid,)).fetchone():
            return None, False
        row = conn.execute("SELECT id, user_edited FROM events WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            return create(conn, {**fields, "uid": uid}), True
        if not row["user_edited"]:
            update(conn, row["id"], {k: v for k, v in fields.items() if k != "uid"}, by_user=False)
        return row["id"], False


def remove(conn, event_id):
    """Status 'eemaldatud', and the uid is remembered so no sync brings it back."""
    with tx(conn):
        row = get(conn, event_id)
        if row is None:
            return
        conn.execute("UPDATE events SET status = 'eemaldatud', updated_at = ? WHERE id = ?", (now_iso(), event_id))
        conn.execute("INSERT OR IGNORE INTO rejected_uids (uid) VALUES (?)", (row["uid"],))


def exclude(conn, event_id, occurrence):
    """Drop one occurrence of a series (an EXDATE); the rest of the series stays. Returns the
    row, or None when there is no such event. ValueError when the event does not repeat."""
    with tx(conn):
        row = get(conn, event_id)
        if row is None:
            return None
        if not row["rrule"]:
            raise ValueError("Sündmus ei kordu.")
        return update(conn, event_id, {"exdates_json": _exdates(row["exdates_json"]) + [occurrence]})


def set_done(conn, event_id, occurrence, done):
    if done:
        conn.execute("INSERT OR IGNORE INTO event_done (event_id, occurrence) VALUES (?, ?)", (event_id, occurrence or ""))
    else:
        conn.execute("DELETE FROM event_done WHERE event_id = ? AND occurrence = ?", (event_id, occurrence or ""))


def local_times(all_day, start, end=None):
    """API input in local wall-clock time -> the time columns. all-day: start/end are
    'YYYY-MM-DD' (end exclusive; a datetime end is ignored); timed: 'YYYY-MM-DDTHH:MM' in
    Europe/Tallinn (an explicit offset is honoured)."""
    if all_day:
        start_d = _parse_date(start[:10] if isinstance(start, str) else start, "Vigane alguskuupäev.")
        end_d = _parse_date(end, "Vigane lõppkuupäev.") if isinstance(end, str) and len(end) == 10 else None
        return {"all_day": 1, "start_at": None, "end_at": None, "start_date": start_d, "end_date": end_d}
    start_at = _local_to_utc(start, "Vigane algusaeg.")
    end_at = _local_to_utc(end, "Vigane lõpuaeg.") if end else None
    return {"all_day": 0, "start_at": start_at, "end_at": end_at, "start_date": None, "end_date": None}


def to_local(dt):
    """Aware datetime -> '2026-10-03T14:15' in Tallinn."""
    return dt.astimezone(TALLINN).strftime("%Y-%m-%dT%H:%M")


def as_json(conn, row):
    """The API's event object: the stored row in occurrence form, plus rrule and user_edited."""
    done = conn.execute("SELECT 1 FROM event_done WHERE event_id = ? AND occurrence = ''", (row["id"],)).fetchone()
    return {**_occurrence(row, _row_start(row), "", bool(done)), "rrule": row["rrule"], "user_edited": bool(row["user_edited"])}


def occurrences(conn, start, end, *, statuses=("kinnitatud", "pakutud"), tasks=True):
    """Everything on the calendar between local dates start (inclusive) and end (exclusive):
    single events, expanded series and, when `tasks`, dated tasks from notes as all-day items.
    Sorted by start."""
    statuses = tuple(statuses)
    marks = ", ".join("?" * len(statuses))
    s_utc = iso(datetime.combine(start, time(), TALLINN))
    e_utc = iso(datetime.combine(end, time(), TALLINN))
    rows = conn.execute(
        f"""SELECT * FROM events
            WHERE status IN ({marks}) AND rrule IS NULL
              AND (series_id IS NULL OR series_id IN (SELECT id FROM events WHERE status IN ({marks})))
              AND ((all_day = 0 AND start_at < ? AND (COALESCE(end_at, start_at) > ? OR start_at >= ?))
                OR (all_day = 1 AND start_date < ? AND end_date > ?))""",
        (*statuses, *statuses, e_utc, s_utc, s_utc, end.isoformat(), start.isoformat()),
    ).fetchall()
    series = conn.execute(
        f"""SELECT * FROM events WHERE status IN ({marks}) AND rrule IS NOT NULL
            AND ((all_day = 0 AND start_at < ?) OR (all_day = 1 AND start_date < ?))""",
        (*statuses, e_utc, end.isoformat()),
    ).fetchall()
    ids = [r["id"] for r in rows] + [r["id"] for r in series]
    done = set()
    overridden = set()
    if ids:
        id_marks = ", ".join("?" * len(ids))
        done = {(r[0], r[1]) for r in conn.execute(
            f"SELECT event_id, occurrence FROM event_done WHERE event_id IN ({id_marks})", ids)}
    if series:
        s_marks = ", ".join("?" * len(series))
        overridden = {(r[0], r[1]) for r in conn.execute(
            f"SELECT series_id, recurrence_id FROM events WHERE series_id IN ({s_marks})", [r["id"] for r in series])}

    out = []
    for r in rows:
        with _skip_bad(r):
            out.append(_occurrence(r, _row_start(r), "", (r["id"], "") in done))
    for r in series:
        with _skip_bad(r):
            skip = set(json.loads(r["exdates_json"] or "[]")) | {rid for sid, rid in overridden if sid == r["id"]}
            out += [_occurrence(r, occ_start, key, (r["id"], key) in done)
                    for occ_start, key in _expand(r, start, end) if key not in skip]
    if tasks:
        out += _task_items(conn, start, end)
    out.sort(key=lambda o: (o["start"], o["title"]))
    return out


@contextmanager
def _skip_bad(row):
    """A row written before validation got stricter (or by hand) must not take the whole calendar
    down: leave it out and say so in the log."""
    try:
        yield
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError) as exc:
        log.warning("event %s skipped: %s", row["id"], exc)


# --- private ---------------------------------------------------------------------------------

def _known(fields):
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Tundmatud väljad: {', '.join(sorted(unknown))}.")
    return dict(fields)


def _validate(f):
    """Check and normalise a full set of columns; raises ValueError with an Estonian message."""
    title = f.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Pealkiri on puudu.")
    if len(title.strip()) > MAX_TITLE:
        raise ValueError(f"Pealkiri on liiga pikk (üle {MAX_TITLE} märgi).")
    f["title"] = title.strip()
    if not isinstance(f.get("uid"), str) or not f["uid"] or any(ch in f["uid"] for ch in "\r\n"):
        raise ValueError("Vigane uid.")
    for key, limit, name in (("location", MAX_LOCATION, "Koht"), ("description", MAX_DESCRIPTION, "Kirjeldus")):
        value = "" if f.get(key) is None else f[key]
        if not isinstance(value, str):
            raise ValueError(f"{name} peab olema tekst.")
        if len(value) > limit:
            raise ValueError(f"{name} on liiga pikk (üle {limit} märgi).")
        f[key] = value
    if f.get("source_quote") is not None and not isinstance(f["source_quote"], str):
        raise ValueError("Allika tsitaat peab olema tekst.")
    for key, allowed, message in (("kind", KINDS, "Tundmatu liik."), ("status", STATUSES, "Tundmatu olek."),
                                  ("source", SOURCES, "Tundmatu allikas."), ("confidence", CONFIDENCES, "Tundmatu kindlus.")):
        if f.get(key) not in allowed:
            raise ValueError(message)
    try:
        ZoneInfo(f["tzid"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Tundmatu ajavöönd.") from None
    f["all_day"] = 1 if f.get("all_day") else 0
    if f["all_day"]:
        if f.get("start_at") or f.get("end_at"):
            raise ValueError("Terve päeva sündmusel on kuupäevad, mitte kellaajad.")
        start = _parse_date(f.get("start_date"), "Alguskuupäev on puudu või vigane.")
        end = _parse_date(f["end_date"], "Vigane lõppkuupäev.") if f.get("end_date") else None
        end = end or (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        if end <= start:
            raise ValueError("Lõpp ei saa olla enne algust.")
        f["start_date"], f["end_date"] = start, end
    else:
        if f.get("start_date") or f.get("end_date"):
            raise ValueError("Kellaajaga sündmusel pole terve päeva kuupäevi.")
        f["start_at"] = _utc(f.get("start_at"), "Algusaeg on puudu või vigane.")
        f["end_at"] = _utc(f["end_at"], "Vigane lõpuaeg.") if f.get("end_at") else None
        if f["end_at"] and f["end_at"] < f["start_at"]:
            raise ValueError("Lõpp ei saa olla enne algust.")
    f["rrule"] = _rrule(f) if f.get("rrule") else None
    f["exdates_json"] = json.dumps(sorted({_key(x, f["all_day"]) for x in _exdates(f.get("exdates_json"))}))
    if f.get("series_id") is not None and not f.get("recurrence_id"):
        raise ValueError("Korduse erandil puudub algne aeg (recurrence_id).")
    if f.get("recurrence_id"):
        f["recurrence_id"] = _key(f["recurrence_id"], len(str(f["recurrence_id"])) == 10)
    return f


def _rrule(f):
    """The rule as stored: one line, what icalendar writes back, expandable by dateutil, and at
    most one occurrence a day."""
    text = f["rrule"]
    if not isinstance(text, str) or "\n" in text or "\r" in text:
        raise ValueError("Vigane kordusreegel.")
    try:
        rule = vRecur.from_ical(text.strip().removeprefix("RRULE:"))
        text = rule.to_ical().decode()
        rrulestr(text, dtstart=_series_dtstart(f))
    except (ValueError, TypeError, KeyError):
        raise ValueError("Vigane kordusreegel.") from None
    if set(rule.get("FREQ", [])) & TOO_FREQUENT or SUB_DAY_PARTS & set(rule):
        raise ValueError("Kordus sagedamini kui kord päevas pole toetatud.")
    return text


def _flip_keys(keys, fields, tzid):
    """Exception keys of a series that switches between timed and all-day: a timed key becomes
    its local date, a date gets the series' new start time."""
    tz = ZoneInfo(tzid)
    if fields["all_day"]:
        return [parse_iso(_utc(k, "Vigane korduse aeg.")).astimezone(tz).date().isoformat() for k in keys]
    at = parse_iso(_utc(fields["start_at"], "Algusaeg on puudu või vigane.")).astimezone(tz).time()
    return [iso(datetime.combine(date.fromisoformat(_parse_date(k, "Vigane korduse kuupäev.")), at, tz)) for k in keys]


def _exdates(value):
    if not value:
        return []
    try:
        items = json.loads(value) if isinstance(value, str) else list(value)
    except (ValueError, TypeError):
        raise ValueError("Vigased erandkuupäevad.") from None
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise ValueError("Vigased erandkuupäevad.")
    return items


def _key(value, all_day):
    """An occurrence key in its one stored form: 'YYYY-MM-DD' or UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if all_day:
        return _parse_date(value, "Vigane korduse kuupäev.")
    return _utc(value, "Vigane korduse aeg.")


def _parse_date(value, message):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError(message) from None


def _utc(value, message):
    """Any ISO datetime with an offset (or Z) -> the canonical UTC form."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(message) from None
    if dt.tzinfo is None:
        raise ValueError(message)
    return iso(dt)


def _local_to_utc(value, message):
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValueError(message) from None
    if len(str(value)) <= 10:
        raise ValueError(message)
    # fold=0: in the repeated autumn hour a local time means its first occurrence.
    return iso(dt if dt.tzinfo else dt.replace(tzinfo=TALLINN))


def _series_dtstart(f):
    if f["all_day"]:
        return datetime.fromisoformat(f["start_date"])
    return parse_iso(f["start_at"]).astimezone(ZoneInfo(f["tzid"]))


def _row_start(row):
    return date.fromisoformat(row["start_date"]) if row["all_day"] else parse_iso(row["start_at"])


def _expand(row, start, end):
    """(start, occurrence key) for each occurrence of a series overlapping [start, end). Timed
    series repeat in their own zone's wall-clock time, so an 08:00 lesson stays at 08:00 across
    a DST change."""
    rule = rrulestr(row["rrule"], dtstart=_series_dtstart(row))
    tz = None if row["all_day"] else TALLINN
    lo, hi = datetime.combine(start, time(), tz), datetime.combine(end, time(), tz)
    if row["all_day"]:
        length = date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])
    else:
        length = _wall_length(row)
    for dt in rule.between(lo - length, hi, inc=True):
        if dt < hi and (dt >= lo or dt + length > lo):
            yield (dt.date(), dt.date().isoformat()) if row["all_day"] else (dt, iso(dt))


def _wall_length(row):
    """Duration in wall-clock time (a 08:00-08:45 lesson stays 45 minutes on the clock)."""
    if not row["end_at"]:
        return timedelta(0)
    tz = ZoneInfo(row["tzid"])
    s = parse_iso(row["start_at"]).astimezone(tz).replace(tzinfo=None)
    e = parse_iso(row["end_at"]).astimezone(tz).replace(tzinfo=None)
    return e - s


def _occurrence(row, occ_start, key, done):
    """The contract's occurrence dict. occ_start: a date (all-day) or an aware datetime."""
    if row["all_day"]:
        length = date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])
        start_s, end_s = occ_start.isoformat(), (occ_start + length).isoformat()
    else:
        start_s = to_local(occ_start)
        if row["end_at"]:
            tz = ZoneInfo(row["tzid"])
            wall_end = occ_start.astimezone(tz).replace(tzinfo=None) + _wall_length(row)
            end_s = to_local(wall_end.replace(tzinfo=tz))
        else:
            end_s = None
    return {
        "id": row["id"], "uid": row["uid"], "title": row["title"], "start": start_s, "end": end_s,
        "all_day": bool(row["all_day"]), "kind": row["kind"], "status": row["status"],
        "source": row["source"], "note_id": row["note_id"], "occurrence": key, "done": done,
        "location": row["location"], "description": row["description"],
        "source_quote": row["source_quote"], "confidence": row["confidence"],
        "recurring": bool(row["rrule"]),
    }


def _task_items(conn, start, end):
    rows = conn.execute(
        """SELECT t.id, t.text, t.done, t.due_date, t.due_time, n.id AS note_id, n.slug
           FROM tasks t JOIN notes n ON n.id = t.note_id
           WHERE n.deleted_at IS NULL AND t.due_date >= ? AND t.due_date < ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [{
        "id": None, "uid": f"task:{t['id']}", "title": t["text"], "start": t["due_date"],
        "end": (date.fromisoformat(t["due_date"]) + timedelta(days=1)).isoformat(), "all_day": True,
        "kind": "tähtaeg", "status": "kinnitatud", "source": "märge", "note_id": t["note_id"],
        "occurrence": "", "done": bool(t["done"]), "location": "", "description": "",
        "source_quote": None, "confidence": None, "recurring": False,
        "task_id": t["id"], "note_slug": t["slug"], "due_time": t["due_time"],
    } for t in rows]
