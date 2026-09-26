"""Postkast: events the AI proposed but was not sure enough to add (status 'pakutud'). The owner
confirms them (optionally edited first) or rejects them; a rejected uid never comes back.
Competitions the finder proposed (app/voistlused.py) come with their registration deadline on
the same card, and the finder is started and set up here too.

Owner: WP-E2 (Postkast + Ühendused); see docs/CONTRACTS.md."""

import os
import time
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request

from .. import events, voistlused
from ..ai import runner
from ..db import get_db, tx
from ..sources import teaduskool
from ..util import TALLINN, iso, parse_iso

bp = Blueprint("inbox", __name__)

KINDS = ("tund", "kontrolltöö", "kodutöö", "sündmus", "tähtaeg", "isiklik")
MAX_TITLE = 200


def _bad(message, status=400):
    return jsonify(error=message), status


def _local(utc_text):
    return parse_iso(utc_text).astimezone(TALLINN).strftime("%Y-%m-%dT%H:%M") if utc_text else None


def _item(conn, row):
    all_day = bool(row["all_day"])
    item = {
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
    origin = voistlused.origin(row)
    if origin:
        deadline = voistlused.deadline_of(conn, row)
        item.update(origin=origin, link=voistlused.link(row),
                    # The first line is the link, shown as one.
                    description=row["description"].partition("\n")[2],
                    deadline={"id": deadline["id"], "start": deadline["start_date"]} if deadline else None)
        if not row["uid"].endswith(voistlused.DEADLINE_SUFFIX):
            decided, facts = voistlused.eligibility_of(conn, row)
            item.update(eligibility=decided, facts={k: v for k, v in facts.items() if k not in ("qualifier", "page_read", "round")})
    return item


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
    if "all_day" in body and not isinstance(body["all_day"], bool):
        raise ValueError("„Terve päev“ peab olema jah või ei.")
    if any(k in body for k in ("start", "end", "all_day")):
        fields.update(time_columns(body.get("all_day", bool(row["all_day"])), body.get("start"), body.get("end")))
    return fields


def _confirm(conn, row, fields=None):
    """by_user marks the row user_edited, so no later sync turns it back into a proposal. A
    competition's registration deadline goes along, named after the competition's new title."""
    fields = fields or {}
    qualifier = voistlused.qualifier_of(conn, row)  # a final's qualifying round goes along
    events.update(conn, row["id"], {**fields, "status": "kinnitatud"})
    deadline = voistlused.deadline_of(conn, row)
    if deadline is not None:
        title = f"Registreeri: {fields['title']}"[:MAX_TITLE] if "title" in fields else deadline["title"]
        events.update(conn, deadline["id"], {"title": title, "status": "kinnitatud"})
    if qualifier is not None:
        _confirm(conn, qualifier)


def _proposed(conn, event_id):
    """A card's event. A registration deadline waiting with its competition is not one: that card
    is confirmed or rejected as a whole, through the competition's id."""
    return conn.execute(f"SELECT * FROM events WHERE id = ? AND status = 'pakutud' AND NOT {voistlused.ATTACHED_SQL}",
                        (event_id,)).fetchone()


@bp.get("/postkast")
def page():
    return render_template("inbox.html")


@bp.get("/api/inbox")
def list_proposed():
    conn = get_db()
    rows = conn.execute(f"SELECT * FROM events WHERE status = 'pakutud' AND NOT {voistlused.ATTACHED_SQL} "
                        "ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([_item(conn, r) for r in rows])


@bp.get("/api/inbox/count")
def count():
    return jsonify(count=voistlused.postkast_count(get_db()))


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
            _confirm(conn, row, _edits(body, row))
    except ValueError as exc:
        return _bad(str(exc))
    return "", 204


@bp.post("/api/inbox/<int:event_id>/reject")
def reject(event_id):
    conn = get_db()
    with tx(conn):
        row = _proposed(conn, event_id)
        if row is None:
            return _bad("Seda ettepanekut pole enam postkastis.", 404)
        deadline = voistlused.deadline_of(conn, row)
        events.remove(conn, event_id)
        if deadline is not None:
            events.remove(conn, deadline["id"])
    return "", 204


@bp.post("/api/inbox/confirm-all")
def confirm_all():
    """-> {"confirmed": n}. What is under "Ei sobi" stays: it is confirmed only one by one."""
    conn = get_db()
    try:
        with tx(conn):
            unfit = voistlused.unfit(conn)
            rows = [r for r in conn.execute("SELECT * FROM events WHERE status = 'pakutud'").fetchall()
                    if r["uid"].removesuffix(voistlused.DEADLINE_SUFFIX) not in unfit]
            for row in rows:
                _confirm(conn, row)
            confirmed = sum(conn.execute("SELECT status FROM events WHERE id = ?", (r["id"],)).fetchone()["status"]
                            == "kinnitatud" for r in rows)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(confirmed=confirmed)


# --- Võistluste otsija ---------------------------------------------------------------------------

@bp.post("/api/voistlused/run")
def run_search():
    """Start a search in the background -> 202 and the status; 409 when one is already running."""
    conn = get_db()
    cfg = voistlused.settings(conn)
    if not cfg["huvid"] and not cfg["markus"]:
        return _bad("Vali Seadetes vähemalt üks huviala.")
    if not voistlused.start(current_app.config["DATABASE_PATH"]):
        return _bad("Võistluste otsing juba käib.", 409)
    return jsonify(voistlused.status(conn)), 202


@bp.get("/api/voistlused/status")
def search_status():
    return jsonify(voistlused.status(get_db()))


@bp.get("/api/voistlused/settings")
def get_search_settings():
    return jsonify(_search_settings(voistlused.settings(get_db())))


@bp.put("/api/voistlused/settings")
def put_search_settings():
    try:
        return jsonify(_search_settings(voistlused.save_settings(get_db(), request.get_json(silent=True))))
    except ValueError as exc:
        return _bad(str(exc))


def _search_settings(cfg):
    return {**cfg, "choices": [{"key": k, "label": label, "covers": covers}
                               for k, (label, covers) in voistlused.INTERESTS.items()]}


if os.environ.get("MARKMIK_TEST_PAGES") == "1":
    if not hasattr(events, "update"):
        # Browser tests only, while app/events.py is still the Wave 0 stub (WP-D's has update).
        def _stub_update(conn, event_id, fields, *, by_user=True):
            fields = {**fields, **({"user_edited": 1} if by_user else {})}
            conn.execute(f"UPDATE events SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), event_id))
        events.update = _stub_update

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

    _real = {}

    @bp.route("/_test/voistlused/fake", methods=["POST", "DELETE"])
    def fake_search():
        """Browser tests: the finder's outside world, scripted. POST {csv, candidates: [{title,
        url, why}], pages: {url: {text, events}}, delay?} (delay: seconds the discovery call
        takes); DELETE puts the real one back."""
        if not _real:
            _real.update(fetch=teaduskool.fetch, page=voistlused.fetch_page, structured=runner.fake_structured)
        teaduskool.fetch, voistlused.fetch_page, runner.fake_structured = _real["fetch"], _real["page"], _real["structured"]
        if request.method == "DELETE":
            return "", 204
        data = request.get_json()
        pages = data.get("pages", {})

        def page(url):
            if url not in pages:
                raise ValueError("Lehte ei saanud alla laadida.")
            return pages[url]["text"]

        def structured(prompt, schema, model):
            if schema is voistlused.DISCOVERY_SCHEMA:
                time.sleep(data.get("delay", 0))
                return {"candidates": data.get("candidates", [])}
            if schema is voistlused.PAGE_SCHEMA:
                return {"events": next((p["events"] for url, p in pages.items() if f"\n{url}\n" in prompt), [])}
            return _real["structured"](prompt, schema, model)

        teaduskool.fetch = lambda: data.get("csv", "").encode()
        voistlused.fetch_page, runner.fake_structured = page, structured
        return "", 204
