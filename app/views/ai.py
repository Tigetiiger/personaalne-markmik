"""Assistant chat and inline AI (/api/ai/...), streamed as server-sent events: one
`event: <type>` + `data: <json>` frame per runner event (docs/CONTRACTS.md §4 "Stream events"),
and the blocks of the review window for AI edits rendered (/api/render/blocks).

Owner: WP-F (AI assistant)."""

import json
import logging
import os

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, render_template_string, request

from .. import notes
from ..ai import chat, runner
from ..db import connect, get_db
from ..render import render_markdown
from ..security import rate_limit

bp = Blueprint("ai", __name__)
log = logging.getLogger("markmik.ai")

MESSAGE_CAP = 10_000
TEXT_CAP = 20_000
RENDER_CAP = 200_000  # characters of Markdown in one /api/render/blocks request
RENDER_BLOCKS = 2_000


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
    markdown = data["markdown"] if isinstance(data.get("markdown"), str) else None
    return _sse(lambda conn: chat.turn(conn, note, message, selection, markdown))


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


@bp.post("/api/render/blocks")
@rate_limit("render-blocks", 60, 60)
def render_blocks():
    """{blocks: [Markdown]} -> {html: [sanitised HTML]}: the review window's blocks as the note
    shows them, in one request."""
    blocks = _body().get("blocks")
    if not isinstance(blocks, list) or not all(isinstance(b, str) for b in blocks):
        return _error("Vigane päring.", 400)
    if len(blocks) > RENDER_BLOCKS or sum(map(len, blocks)) > RENDER_CAP:
        return _error("Muudatus on eelvaateks liiga suur.", 413)
    return jsonify({"html": [render_markdown(b) for b in blocks]})


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) -------------------------------------------

TEST_PAGE = """{% extends "base.html" %}
{% block title %}AI test{% endblock %}
{% block page %}ai-test{% endblock %}
{% block head %}<script type="module" src="{{ asset_url('ai/test-page.js') }}"></script>{% endblock %}
{% block body %}
{# No #app, #panel-body, ...: the shell (WP-C) would lay those out as its own note page. #}
<div class="app no-left">
  <aside class="pane left"></aside>
  <main class="pane center"><div id="ai-meta-row"></div><div id="editor" data-slug="{{ slug }}"></div></main>
  <aside class="pane right"><div class="panel-body" id="ai-panel"></div></aside>
</div>
{% endblock %}"""


def _test_page(slug):
    conn = get_db()
    note = notes.by_slug(conn, slug) or notes.create(conn, slug)
    return render_template_string(TEST_PAGE, slug=note["slug"])


def _test_fake():
    """Script the fake model: {"reply": str, "tool": {"name", "input"}?} or {"reply": str, "edit":
    {"replace"?, "append"?}} (runner.fake_edit); {} restores the default."""
    data = request.get_json(silent=True) or {}
    if "reply" not in data:
        runner.fake_chat = None
        return "", 204
    if "edit" in data:
        runner.fake_chat = runner.fake_edit(data["reply"], **data["edit"])
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
