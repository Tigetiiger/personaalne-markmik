"""Assistant chat and inline AI (/api/ai/...), streamed as server-sent events: one
`event: <type>` + `data: <json>` frame per runner event (docs/CONTRACTS.md §4 "Stream events").

Owner: WP-F (AI assistant)."""

import json
import logging
import os

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, render_template_string, request

from .. import notes
from ..ai import chat, runner
from ..db import connect, get_db
from ..security import rate_limit

bp = Blueprint("ai", __name__)
log = logging.getLogger("markmik.ai")

MESSAGE_CAP = 10_000
TEXT_CAP = 20_000


def _error(message, status):
    return make_response(jsonify({"error": message}), status)


def _sse(make_events):
    """make_events(conn) streamed. The stream outlives the request's own connection (closed at
    teardown when the view returns), so it gets one of its own."""
    path = current_app.config["DATABASE_PATH"]

    def frames():
        conn = connect(path)
        try:
            for ev in make_events(conn):
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception:
            log.exception("AI stream failed")
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': 'AI viga, proovi uuesti.'})}\n\n"
        finally:
            conn.close()
    return Response(frames(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _note(conn, slug):
    note = notes.by_slug(conn, slug)
    if note is None:
        abort(_error("Märget ei leitud.", 404))
    return note


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _field(data, key, cap):
    value = data.get(key)
    return value[:cap] if isinstance(value, str) else ""


@bp.post("/api/ai/chat")
@rate_limit("ai-chat", 20, 60)
def chat_turn():
    data = _body()
    message = _field(data, "message", MESSAGE_CAP).strip()
    if not message:
        return _error("Kirjuta küsimus.", 400)
    conn = get_db()
    note = _note(conn, data["slug"]) if isinstance(data.get("slug"), str) and data["slug"] else None
    selection = _field(data, "selection", chat.SELECTION_CAP).strip()
    return _sse(lambda conn: chat.turn(conn, note, message, selection))


@bp.post("/api/ai/inline")
@rate_limit("ai-inline", 30, 60)
def inline():
    data = _body()
    instruction = _field(data, "instruction", 2_000).strip()
    if not instruction:
        return _error("Kirjuta, mida teha.", 400)
    conn = get_db()
    note = _note(conn, data.get("slug") if isinstance(data.get("slug"), str) else "")
    text = _field(data, "text", TEXT_CAP)
    return _sse(lambda conn: chat.inline(conn, note, instruction, text))


@bp.get("/api/ai/threads/<slug>")
def thread(slug):
    conn = get_db()
    thread_id = chat.thread_id(conn, _note(conn, slug)["id"], create=False)
    return jsonify(chat.messages(conn, thread_id) if thread_id else [])


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) -------------------------------------------

TEST_PAGE = """{% extends "base.html" %}
{% block title %}AI test{% endblock %}
{% block page %}ai-test{% endblock %}
{% block head %}<script type="module" src="{{ asset_url('ai/test-page.js') }}"></script>{% endblock %}
{% block body %}
<div class="app" id="app">
  <main class="pane center" id="main"><div class="ai-test-meta" id="meta"></div><div id="editor"></div></main>
  <aside class="pane right" id="right"><div class="panel-body" id="panel-body"></div></aside>
</div>
{% endblock %}"""


def _test_page(slug):
    conn = get_db()
    note = notes.by_slug(conn, slug) or notes.create(conn, slug)
    return render_template_string(TEST_PAGE, slug=note["slug"])


def _test_fake():
    """Script the fake model: {"reply": str, "tool": {"name", "input"}?}; {} restores the default."""
    data = request.get_json(silent=True) or {}
    if "reply" not in data:
        runner.fake_chat = None
        return "", 204

    def fake(prompt, tools):
        if data.get("tool"):
            spec = data["tool"]
            found = next(t for t in tools if getattr(t, "name", None) == spec["name"])
            yield {"type": "tool", "name": spec["name"], "input": spec["input"],
                   "result": runner.call_tool(found, spec["input"])}
        for i in range(0, len(data["reply"]), 6):
            yield {"type": "delta", "text": data["reply"][i:i + 6]}
        yield {"type": "done", "session_id": "fake-session"}
    runner.fake_chat = fake
    return "", 204


@bp.record
def _test_pages(state):
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/ai/<slug>", "ai.test_page", _test_page)
        state.app.add_url_rule("/_test/ai-fake", "ai.test_fake", _test_fake, methods=["POST"])
