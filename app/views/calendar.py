"""Calendar pages and /api/events, /api/today, /ics/<token>.ics, /api/settings/reminders.

Owner: WP-D (calendar). Contract: docs/CONTRACTS.md §5. The API speaks local wall-clock time
(Europe/Tallinn): start/end are '2026-10-03T14:15', or '2026-10-03' for all-day events (end
exclusive)."""

import os
from datetime import date, timedelta

from flask import Blueprint, Response, abort, jsonify, render_template, request

from .. import auth, events, ics, reminders, vault, voistlused
from ..db import get_db
from ..util import local_today

bp = Blueprint("calendar", __name__)

# What the calendar UI may change directly; times go through events.local_times.
EDITABLE = ("title", "kind", "location", "description")
MAX_RANGE_DAYS = 400


def _bad(message, status=400):
    return jsonify(error=message), status


def _live_event(conn, event_id):
    row = events.get(conn, event_id)
    if row is None or row["status"] == "eemaldatud":
        response = jsonify(error="Sündmust ei leitud.")
        response.status_code = 404
        abort(response)
    return row


def _fields(conn, data, row=None):
    """Request JSON -> events columns. For a PATCH, times missing from the request keep the
    event's current ones."""
    if not isinstance(data, dict) or not isinstance(data.get("all_day", False), bool):
        raise ValueError("Vigane päring.")
    if any(data.get(k) is not None and not isinstance(data[k], str) for k in ("start", "end")):
        raise ValueError("Vigane aeg.")
    fields = {k: data[k] for k in EDITABLE if k in data}
    if row is None or any(k in data for k in ("start", "end", "all_day")):
        try:
            current = events.as_json(conn, row) if row is not None else {}
        except (ValueError, TypeError, KeyError):
            current = {}  # a broken old row: the request's own times must repair it
        all_day = bool(data.get("all_day", current.get("all_day", False)))
        start = data.get("start", current.get("start"))
        end = data["end"] if "end" in data else current.get("end")
        fields.update(events.local_times(all_day, start, end))
    return fields


@bp.get("/kalender")
def page():
    return render_template("calendar.html")


@bp.get("/api/events")
def list_events():
    try:
        start = date.fromisoformat(request.args.get("start", "")[:10])
        end = date.fromisoformat(request.args.get("end", "")[:10])
    except ValueError:
        return _bad("Vigane ajavahemik.")
    if not start < end <= start + timedelta(days=MAX_RANGE_DAYS):
        return _bad("Vigane ajavahemik.")
    conn = get_db()
    return jsonify(events.occurrences(conn, start, end) + [_reminder_occ(r) for r in reminders.items(conn, start, end)])


def _reminder_occ(r):
    """A reminder as a calendar item: a moment with a bell, moved by dragging."""
    return {"id": None, "uid": f"reminder:{r['id']}", "title": r["text"], "start": r["remind_at"], "end": None,
            "all_day": False, "kind": "meeldetuletus", "status": "kinnitatud", "source": r["source"],
            "note_id": None, "occurrence": "", "done": r["done"], "location": "", "description": "",
            "source_quote": None, "confidence": None, "recurring": False, "reminder_id": r["id"],
            "missed": r["missed"], "sent": r["sent"]}


@bp.get("/api/reminders")
def list_reminders():
    try:
        start = date.fromisoformat(request.args.get("start", "")[:10])
        end = date.fromisoformat(request.args.get("end", "")[:10])
    except ValueError:
        return _bad("Vigane ajavahemik.")
    if not start < end <= start + timedelta(days=MAX_RANGE_DAYS):
        return _bad("Vigane ajavahemik.")
    return jsonify(reminders.items(get_db(), start, end))


def _reminder_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Vigane päring.")
    if "text" in data and not isinstance(data["text"], str):
        raise ValueError("Vigane tekst.")
    if "done" in data and not isinstance(data["done"], bool):
        raise ValueError("Vigane päring.")
    at = reminders.local_at(data["remind_at"]) if data.get("remind_at") is not None else None
    return data, at


@bp.post("/api/reminders")
def create_reminder():
    conn = get_db()
    try:
        data, at = _reminder_body()
        if at is None:
            raise ValueError("Aeg on puudu.")
        reminder_id = reminders.create(conn, data.get("text", ""), at)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(reminders.get(conn, reminder_id)), 201


@bp.patch("/api/reminders/<int:reminder_id>")
def update_reminder(reminder_id):
    conn = get_db()
    try:
        data, at = _reminder_body()
        reminders.update(conn, reminder_id, text=data.get("text"), remind_at=at, done=data.get("done"))
    except ValueError as exc:
        return _bad(str(exc))
    except LookupError:
        return _bad("Meeldetuletust ei leitud.", 404)
    return jsonify(reminders.get(conn, reminder_id))


@bp.delete("/api/reminders/<int:reminder_id>")
def delete_reminder(reminder_id):
    if not reminders.remove(get_db(), reminder_id):
        return _bad("Meeldetuletust ei leitud.", 404)
    return "", 204


@bp.post("/api/events")
def create_event():
    conn = get_db()
    try:
        event_id = events.create(conn, _fields(conn, request.get_json(silent=True)))
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(events.as_json(conn, events.get(conn, event_id))), 201


@bp.patch("/api/events/<int:event_id>")
def patch_event(event_id):
    conn = get_db()
    row = _live_event(conn, event_id)
    try:
        row = events.update(conn, event_id, _fields(conn, request.get_json(silent=True), row))
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(events.as_json(conn, row))


@bp.delete("/api/events/<int:event_id>")
def delete_event(event_id):
    """The whole event (its uid is never synced back), or with ?occurrence=<key> only that
    occurrence of a series."""
    conn = get_db()
    _live_event(conn, event_id)
    if "occurrence" in request.args:
        # An empty value must not fall through to deleting (and blocking) the whole series.
        if not request.args["occurrence"]:
            return _bad("Vigane päring.")
        try:
            events.exclude(conn, event_id, request.args["occurrence"])
        except ValueError as exc:
            return _bad(str(exc))
    else:
        events.remove(conn, event_id)
    return "", 204


@bp.post("/api/events/<int:event_id>/done")
def event_done(event_id):
    conn = get_db()
    _live_event(conn, event_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("done"), bool):
        return _bad("Vigane päring.")
    occurrence = data.get("occurrence") or ""
    if not isinstance(occurrence, str):
        return _bad("Vigane päring.")
    events.set_done(conn, event_id, occurrence, bool(data.get("done")))
    return "", 204


@bp.get("/api/today")
def today():
    """Täna's data for today, or for ?date=YYYY-MM-DD (Täna's Eile / Homme)."""
    conn = get_db()
    day = local_today()
    if request.args.get("date"):
        try:
            day = date.fromisoformat(request.args["date"])
        except ValueError:
            return _bad("Vigane kuupäev.")
    tasks = conn.execute(
        """SELECT t.id, n.slug AS note_slug, n.title AS note_title, t.text, t.done, t.due_date, t.due_time
           FROM tasks t JOIN notes n ON n.id = t.note_id
           WHERE n.deleted_at IS NULL AND (t.due_date = ? OR (t.due_date < ? AND t.done = 0))
           ORDER BY t.due_date, t.due_time IS NULL, t.due_time, t.id""",
        (day.isoformat(), day.isoformat()),
    ).fetchall()
    tests_soon = [o for o in events.occurrences(conn, day, day + timedelta(days=8), tasks=False) if o["kind"] == "kontrolltöö"]
    daily = conn.execute(
        "SELECT slug, title FROM notes WHERE daily_date = ? AND deleted_at IS NULL", (day.isoformat(),)
    ).fetchone()
    return jsonify({
        "date": day.isoformat(),
        "events": events.occurrences(conn, day, day + timedelta(days=1), tasks=False),
        "tasks": [{**dict(t), "done": bool(t["done"])} for t in tasks],
        "tests_soon": [{**o, "days_left": (date.fromisoformat(o["start"][:10]) - day).days} for o in tests_soon],
        "daily": dict(daily) if daily else None,
        # Today also lists the missed ones of earlier days, until ticked or removed.
        "reminders": reminders.items(conn, day, day + timedelta(days=1), missed=day == local_today()),
        "ntfy": _ntfy_ready(conn),
        # Postkast's cards: a competition's registration deadline shares its card; "Ei sobi" is apart.
        "postkast": voistlused.postkast_count(conn),
    })


def _ntfy_ready(conn):
    try:
        return reminders.ntfy_config(conn) is not None
    except vault.SecretsError:
        return False


@bp.get("/ics/<token>.ics")
@auth.public
def ics_feed(token):
    conn = get_db()
    if not ics.token_ok(conn, token):
        abort(404)
    return Response(ics.feed(conn), mimetype="text/calendar",
                    headers={"Content-Disposition": 'inline; filename="markmik.ics"', "Cache-Control": "no-store"})


@bp.get("/api/settings/reminders")
def get_reminder_rules():
    return jsonify(reminders.rules(get_db()))


@bp.put("/api/settings/reminders")
def put_reminder_rules():
    try:
        return jsonify(reminders.save_rules(get_db(), request.get_json(silent=True)))
    except ValueError as exc:
        return _bad(str(exc))


if os.environ.get("MARKMIK_TEST_PAGES") == "1":
    @bp.post("/_test/calendar/events")
    def test_seed_event():
        """Browser tests need AI and proposed events, which the public API cannot make."""
        conn = get_db()
        try:
            return jsonify(id=events.create(conn, request.get_json(silent=True) or {})), 201
        except ValueError as exc:
            return _bad(str(exc))
