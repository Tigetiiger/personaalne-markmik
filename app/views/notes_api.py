"""JSON API for notes, links, tasks and search (/api/notes/..., /api/daily, /api/tasks, /api/search).
Owner only (auth default deny). Owner: WP-B; docs/CONTRACTS.md §5."""

import json
from datetime import date

from flask import Blueprint, abort, jsonify, make_response, request

from .. import links, notes, search, tasks
from ..db import get_db, tx

bp = Blueprint("notes_api", __name__)


@bp.errorhandler(notes.NoteBusy)
def busy(_exc):
    return _error("Märget muudetakse teises aknas.", 409)


@bp.get("/api/notes/tree")
def tree():
    return jsonify(notes.tree(get_db()))


@bp.get("/api/notes/suggest")
def suggest():
    return jsonify(search.suggest(get_db(), request.args.get("q", "")))


@bp.get("/api/notes/<slug>")
def get_note(slug):
    return jsonify(note_json(_note(slug)))


@bp.post("/api/notes")
def create():
    data = _body()
    title, folder, body = data.get("title", ""), data.get("folder", ""), data.get("body")
    if not isinstance(title, str) or not isinstance(folder, str) or not isinstance(body, (str, type(None))):
        abort(_error("Vigased andmed.", 400))
    try:
        note = notes.create(get_db(), title, folder=folder, body=body)
    except ValueError as exc:
        abort(_error(str(exc), 400))
    return jsonify(note_json(note)), 201


# PATCH field -> (JSON type, setter)
_PATCH = {
    "folder": (str, notes.move),
    "tags": (list, notes.set_tags),
    "aliases": (list, notes.set_aliases),
    "pinned": (bool, notes.set_pinned),
    "hidden": (bool, notes.set_hidden),
}


@bp.patch("/api/notes/<slug>")
def update(slug):
    conn, note = get_db(), _note(slug)
    changes = {k: v for k, v in _body().items() if k in _PATCH}
    for key, value in changes.items():
        kind = _PATCH[key][0]
        if not isinstance(value, kind) or (kind is list and not all(isinstance(v, str) for v in value)):
            abort(_error("Vigased andmed.", 400))
    try:
        with tx(conn):
            for key, value in changes.items():
                _PATCH[key][1](conn, note["id"], value)
    except ValueError as exc:
        abort(_error(str(exc), 400))
    return jsonify(note_json(notes.get(conn, note["id"])))


@bp.delete("/api/notes/<slug>")
def delete(slug):
    notes.soft_delete(get_db(), _note(slug)["id"])
    return "", 204


@bp.get("/api/notes/<slug>/links")
def note_links(slug):
    conn, note = get_db(), _note(slug)
    return jsonify({"outgoing": links.outgoing(conn, note), "backlinks": links.backlinks(conn, note)})


@bp.get("/api/notes/<slug>/mentions")
def mentions(slug):
    return jsonify(links.unlinked_mentions(get_db(), _note(slug)))


@bp.post("/api/notes/<slug>/link-mention")
def link_mention(slug):
    conn, target = get_db(), _note(slug)
    src_slug = _body().get("src_slug")
    src = notes.by_slug(conn, src_slug) if isinstance(src_slug, str) else None
    if src is None:
        abort(_error("Märget ei leitud.", 404))
    if not links.link_mention(conn, target, src):
        abort(_error("Mainimist ei leitud.", 404))
    return "", 204


@bp.get("/api/daily/<day>")
def daily(day):
    return jsonify(note_json(notes.daily(get_db(), _date(day))))


@bp.get("/api/tasks")
def task_list():
    sql = ("SELECT t.id, n.slug AS note_slug, n.title AS note_title, t.text, t.done, t.due_date, t.due_time "
           "FROM tasks t JOIN notes n ON n.id = t.note_id WHERE n.deleted_at IS NULL")
    args = []
    if request.args.get("from"):
        sql += " AND t.due_date >= ?"
        args.append(_date(request.args["from"]).isoformat())
    if request.args.get("to"):
        sql += " AND t.due_date <= ?"
        args.append(_date(request.args["to"]).isoformat())
    if request.args.get("done") in ("0", "1", "true", "false"):
        sql += " AND t.done = ?"
        args.append(int(request.args["done"] in ("1", "true")))
    sql += " ORDER BY t.due_date IS NULL, t.due_date, t.due_time IS NULL, t.due_time, t.id"
    return jsonify([dict(r, done=bool(r["done"])) for r in get_db().execute(sql, args)])


@bp.post("/api/tasks/<int:task_id>/toggle")
def toggle_task(task_id):
    if tasks.toggle(get_db(), task_id) is None:
        abort(_error("Ülesannet ei leitud.", 404))
    return "", 204


@bp.get("/api/search")
def search_notes():
    return jsonify(search.search(get_db(), request.args.get("q", "")))


def note_json(row):
    return {
        "id": row["id"], "slug": row["slug"], "title": row["title"], "body": row["body"],
        "folder": row["folder"], "tags": json.loads(row["tags_json"]), "aliases": json.loads(row["aliases_json"]),
        "pinned": bool(row["pinned"]), "hidden": notes.is_hidden(row), "is_daily": bool(row["is_daily"]),
        "daily_date": row["daily_date"],
        "rev": row["rev"], "updated_at": row["updated_at"],
    }


def _note(slug):
    note = notes.by_slug(get_db(), slug)
    if note is None:
        abort(_error("Märget ei leitud.", 404))
    return note


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(_error("Vigane päring.", 400))
    return data


def _date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        abort(_error("Vigane kuupäev.", 400))


def _error(message, status):
    return make_response(jsonify({"error": message}), status)
