"""Postkast: events the AI proposed but was not sure enough to add (status 'pakutud'). The owner
confirms them (optionally edited first) or rejects them; a rejected uid never comes back.

Owner: WP-E2 (Postkast + Ühendused); see docs/CONTRACTS.md."""

import os
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from .. import events
from ..db import get_db, tx
from ..util import TALLINN, iso, parse_iso

bp = Blueprint("inbox", __name__)

KINDS = ("tund", "kontrolltöö", "kodutöö", "sündmus", "tähtaeg", "isiklik")
MAX_TITLE = 200


def _bad(message, status=400):
    return jsonify(error=message), status


def _local(utc_text):
    return parse_iso(utc_text).astimezone(TALLINN).strftime("%Y-%m-%dT%H:%M") if utc_text else None


def _item(row):
    all_day = bool(row["all_day"])
    return {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "all_day": all_day,
        "start": row["start_date"] if all_day else _local(row["start_at"]),
        "end": row["end_date"] if all_day else _local(row["end_at"]),
        "location": row["location"],
        "source": row["source"],
        "source_quote": row["source_quote"],
        "confidence": row["confidence"],
        # Extraction writes here why the event was not added on its own.
        "description": row["description"],
        "created_at": row["created_at"],
    }


def _date(value, message):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None


def _datetime(value, message):
    if not isinstance(value, str) or len(value) != 16:  # 'YYYY-MM-DDTHH:MM'
        raise ValueError(message)
    try:
        return datetime.fromisoformat(value).replace(tzinfo=TALLINN)
    except ValueError:
        raise ValueError(message) from None


def time_columns(all_day, start, end=None):
    """Local wall-clock input -> the events time columns. All-day: 'YYYY-MM-DD' (end exclusive,
    default the next day); timed: 'YYYY-MM-DDTHH:MM' in Europe/Tallinn. ValueError (Estonian)."""
    if all_day:
        start_d = _date(start, "Kuupäev on puudu või vigane.")
        end_d = _date(end, "Vigane lõppkuupäev.") if end else start_d + timedelta(days=1)
        if end_d <= start_d:
            raise ValueError("Lõpp ei saa olla enne algust.")
        return {"all_day": 1, "start_date": start_d.isoformat(), "end_date": end_d.isoformat(),
                "start_at": None, "end_at": None}
    start_dt = _datetime(start, "Algusaeg on puudu või vigane.")
    end_dt = _datetime(end, "Vigane lõpuaeg.") if end else None
    if end_dt and end_dt < start_dt:
        raise ValueError("Lõpp ei saa olla enne algust.")
    return {"all_day": 0, "start_at": iso(start_dt), "end_at": iso(end_dt) if end_dt else None,
            "start_date": None, "end_date": None}


def _edits(body, row):
    """The fields the owner changed in Muuda, as events columns."""
    fields = {}
    if "title" in body:
        title = body["title"].strip() if isinstance(body["title"], str) else ""
        if not title:
            raise ValueError("Pealkiri on puudu.")
        if len(title) > MAX_TITLE:
            raise ValueError(f"Pealkiri on liiga pikk (üle {MAX_TITLE} märgi).")
        fields["title"] = title
    if "kind" in body:
        if body["kind"] not in KINDS:
            raise ValueError("Tundmatu liik.")
        fields["kind"] = body["kind"]
    if any(k in body for k in ("start", "end", "all_day")):
        fields.update(time_columns(bool(body.get("all_day", row["all_day"])), body.get("start"), body.get("end")))
    return fields


def _confirm(conn, row, fields=None):
    """True when the event is now confirmed. user_edited, so no later sync turns it back into a
    proposal; a row the owner already edited in the calendar is left alone by upsert_by_uid."""
    events.upsert_by_uid(conn, row["uid"], {**(fields or {}), "status": "kinnitatud", "user_edited": 1})
    return conn.execute("SELECT status FROM events WHERE id = ?", (row["id"],)).fetchone()["status"] == "kinnitatud"


def _proposed(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ? AND status = 'pakutud'", (event_id,)).fetchone()


@bp.get("/postkast")
def page():
    return render_template("inbox.html")


@bp.get("/api/inbox")
def list_proposed():
    rows = get_db().execute("SELECT * FROM events WHERE status = 'pakutud' ORDER BY created_at DESC, id DESC")
    return jsonify([_item(r) for r in rows])


@bp.get("/api/inbox/count")
def count():
    n = get_db().execute("SELECT COUNT(*) FROM events WHERE status = 'pakutud'").fetchone()[0]
    return jsonify(count=n)


@bp.post("/api/inbox/<int:event_id>/confirm")
def confirm(event_id):
    conn = get_db()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _bad("Vigane päring.")
    try:
        with tx(conn):
            row = _proposed(conn, event_id)
            if row is None:
                return _bad("Seda ettepanekut pole enam postkastis.", 404)
            if not _confirm(conn, row, _edits(body, row)):
                return _bad("Sündmust on vahepeal kalendris muudetud; ava see kalendris.", 409)
    except ValueError as exc:
        return _bad(str(exc))
    return "", 204


@bp.post("/api/inbox/<int:event_id>/reject")
def reject(event_id):
    conn = get_db()
    with tx(conn):
        if _proposed(conn, event_id) is None:
            return _bad("Seda ettepanekut pole enam postkastis.", 404)
        events.remove(conn, event_id)
    return "", 204


@bp.post("/api/inbox/confirm-all")
def confirm_all():
    conn = get_db()
    try:
        with tx(conn):
            for row in conn.execute("SELECT * FROM events WHERE status = 'pakutud'").fetchall():
                _confirm(conn, row)
    except ValueError as exc:
        return _bad(str(exc))
    return "", 204


if os.environ.get("MARKMIK_TEST_PAGES") == "1":
    @bp.post("/_test/inbox/seed")
    def seed():
        """Browser tests: replace every proposed event with [{title, start, end?, all_day?, kind?,
        source?, source_quote?, confidence?, description?}] (local times), created in list order."""
        conn = get_db()
        ids = []
        with tx(conn):
            conn.execute("DELETE FROM events WHERE status = 'pakutud'")
            for e in request.get_json():
                ids.append(events.create(conn, {
                    "title": e["title"], "kind": e.get("kind", "sündmus"), "status": "pakutud",
                    "source": e.get("source", "gmail"), "source_quote": e.get("source_quote"),
                    "confidence": e.get("confidence", "low"), "description": e.get("description", ""),
                    **time_columns(e.get("all_day", False), e["start"], e.get("end")),
                }))
        return jsonify(ids=ids), 201
