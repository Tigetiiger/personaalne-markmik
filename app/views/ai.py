"""Assistant chat and inline AI (/api/ai/...), streamed as server-sent events: one
`event: <type>` + `data: <json>` frame per runner event (docs/CONTRACTS.md §4 "Stream events"),
and the blocks of the review window for AI edits rendered (/api/render/blocks).

Owner: WP-F (AI assistant)."""

import json
import logging
import os

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, render_template_string, request

from .. import images, notes
from ..ai import chat, pictures, runner
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
    sent = _pictures(conn, data.get("images"), note)
    return _sse(lambda conn: chat.turn(conn, note, message, selection, markdown, sent))


def _pictures(conn, names, note):
    """The home chat's pictures, read and prepared before the stream starts, so a bad one is a 400
    and not an error halfway through an answer."""
    if names is None or names == []:
        return []
    if note is not None:
        abort(_error("Pilte saab saata avalehe vestluses.", 400))
    if not isinstance(names, list) or not all(isinstance(n, str) and images.NAME.match(n) for n in names):
        abort(_error("Vigane pilt.", 400))
    names = list(dict.fromkeys(names))
    if len(names) > pictures.PER_MESSAGE:
        abort(_error(f"Ühe sõnumiga saab saata kuni {pictures.PER_MESSAGE} pilti.", 413))
    out = []
    for name in names:
        path = images.folder() / name
        known = conn.execute("SELECT 1 FROM images WHERE name = ?", (name,)).fetchone()
        if not known or not path.is_file():
            abort(_error("Pilti ei leitud.", 404))
        try:
            media_type, data = pictures.prepare(path)
        except ValueError as exc:
            abort(_error(str(exc), 400))
        out.append({"name": name, "media_type": media_type, "data": data})
    return out


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


@bp.get("/api/ai/threads")
def home_thread():
    """The home chat's thread on / (docs/KODU-PLAN.md)."""
    conn = get_db()
    thread_id = chat.thread_id(conn, None, create=False)
    return jsonify(chat.messages(conn, thread_id) if thread_id else [])


@bp.post("/api/ai/threads")
def new_home_thread():
    """"Uus vestlus"."""
    return jsonify({"id": chat.new_home_thread(get_db())}), 201


@bp.get("/api/ai/threads/<slug>")
def thread(slug):
    conn = get_db()
    thread_id = chat.thread_id(conn, _note(conn, slug)["id"], create=False)
    return jsonify(chat.messages(conn, thread_id) if thread_id else [])


@bp.post("/api/ai/proposals/<int:message_id>")
def settle_proposal(message_id):
    """{settled: "applied" | "dropped" | "failed"}: how the review of a proposal ended."""
    state = _body().get("settled")
    if state not in chat.SETTLED:
        return _error("Vigane päring.", 400)
    if not chat.settle(get_db(), message_id, state):
        return _error("Ettepanekut ei leitud.", 404)
    return "", 204


@bp.get("/api/ai/proposals/<int:message_id>")
def get_proposal(message_id):
    """A home chat proposal, for its note's page (/m/<slug>?ettepanek=<id>) when the note was open
    elsewhere: {slug, base, markdown, settled}."""
    proposal = chat.proposal(get_db(), message_id)
    if proposal is None or "slug" not in proposal:
        return _error("Ettepanekut ei leitud.", 404)
    return jsonify({"slug": proposal["slug"], "base": proposal["base"], "markdown": proposal["proposal"],
                    "settled": proposal.get("settled")})


@bp.post("/api/ai/proposals/<int:message_id>/apply")
def apply_proposal(message_id):
    """{markdown, rev} -> {rev}: what the owner kept of a home chat proposal, reviewed on / against
    the note's text at `rev`. 409 {reason: "muudetud"} when the note changed since, {reason:
    "lahti"} when it is open in an editor (then it is reviewed there, as ordinary edits)."""
    data = _body()
    markdown, rev = data.get("markdown"), data.get("rev")
    if not isinstance(markdown, str) or not isinstance(rev, int) or isinstance(rev, bool) or len(markdown) > RENDER_CAP:
        return _error("Vigane päring.", 400)
    conn = get_db()
    proposal = chat.proposal(conn, message_id)
    if proposal is None or "rev" not in proposal:
        return _error("Ettepanekut ei leitud.", 404)
    note = notes.by_slug(conn, proposal["slug"])
    if note is None:
        return _error("Märget ei leitud.", 404)
    if note["rev"] != rev:
        return make_response(jsonify({"error": "Märget muudeti vahepeal.", "reason": "muudetud"}), 409)
    try:
        new_rev = notes.write_body(conn, note["id"], markdown)
    except notes.NoteBusy:
        return make_response(jsonify({"error": "Märge on teises aknas lahti.", "reason": "lahti"}), 409)
    chat.settle(conn, message_id, "applied")
    return jsonify({"rev": new_rev})


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
