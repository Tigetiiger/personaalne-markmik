"""Python cells in notes (docs/ARVUTUS-PLAN.md): POST /api/arvuta runs a note's cells in the
sandbox and answers with the Markdown the results go into. Owner only (default deny).

The browser sends the editor's Markdown and applies the answer as ordinary edits, so co-editors see
them and Ctrl+Z undoes them; this view writes nothing.
"""

import threading

from flask import Blueprint, jsonify, request

from .. import mouser, notes, shares
from ..arvuta import cells, client, plots
from ..db import get_db
from ..security import rate_limit

bp = Blueprint("arvuta", __name__)

MARKDOWN_CAP = 500_000
_running = set()  # note ids with a run in progress
_running_lock = threading.Lock()


def _error(message, status):
    return jsonify({"error": message}), status


@bp.post("/api/arvuta")
@rate_limit("arvuta", 120, 60)
def run():
    """{slug, markdown, run: "auto" | "all" | <cell index>, hash?} -> {markdown | null, cells: [{ok,
    error?}], seconds, hash} or {skipped: "sama" | "kulalised"}. markdown is null when nothing
    changes; hash is that of the inputs the answer reflects (send it back with the next "auto")."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("markdown"), str) or not isinstance(data.get("slug"), str):
        return _error("Vigane päring.", 400)
    markdown, mode = data["markdown"], data.get("run", "auto")
    if len(markdown) > MARKDOWN_CAP:
        return _error("Märge on arvutamiseks liiga suur.", 413)
    upto = None
    if isinstance(mode, int) and not isinstance(mode, bool):
        upto, mode = mode, "cell"
    elif mode not in ("auto", "all"):
        return _error("Vigane päring.", 400)
    conn = get_db()
    note = notes.by_slug(conn, data["slug"])
    if note is None:
        return _error("Märget ei leitud.", 404)
    doc = cells.parse(markdown)
    if upto is not None and not 0 <= upto < len(doc["cells"]):
        return _error("Sellist arvutusplokki pole.", 400)
    if not doc["cells"] and not doc["inline"]:
        return jsonify({"markdown": None, "cells": [], "seconds": 0, "hash": cells.inputs_hash(doc)})
    if mode == "auto":
        if data.get("hash") == cells.inputs_hash(doc):
            return jsonify({"skipped": "sama"})
        if not notes.runs_by_itself(note):
            return jsonify({"skipped": "kasitsi"})
        # Code a guest may have written runs only when the owner presses ▶ (docs/ARVUTUS-PLAN.md).
        if shares.edit_open(conn, note["id"]):
            return jsonify({"skipped": "kulalised"})
    with _running_lock:
        if note["id"] in _running:
            return _error("Arvutus juba käib.", 409)
        _running.add(note["id"])
    try:
        answer = _run(conn, doc, mode, upto)
        _store_plots(conn, answer, note["id"])
    finally:
        with _running_lock:
            _running.discard(note["id"])
    if answer.get("error"):
        return jsonify({"markdown": None, "error": answer["error"], "cells": [], "seconds": answer.get("seconds", 0),
                        "hash": None})
    try:
        new = cells.apply(doc, answer, upto)
        status = [{"ok": not c.get("error"), **({"error": str(c["error"])} if c.get("error") else {})}
                  for c in answer.get("cells", [])]
    except (TypeError, KeyError, IndexError, AttributeError, ValueError):
        # The sandbox runs the note's code; its answer is checked like any other input.
        return jsonify({"markdown": None, "error": "Arvutus andis vigase vastuse.", "cells": [], "seconds": 0, "hash": None})
    return jsonify({"markdown": new if new != markdown else None, "cells": status, "seconds": answer.get("seconds", 0),
                    "hash": cells.inputs_hash(cells.parse(new))})


def _store_plots(conn, answer, note_id):
    """Each cell's PNGs (base64 from the sandbox) become stored pictures of the note, by name."""
    for result in answer.get("cells") or []:
        if isinstance(result, dict) and isinstance(result.get("images"), list):
            names = [plots.store(conn, b, note_id) for b in result["images"][:4]]
            result["images"] = [n for n in names if n]


def _run(conn, doc, mode, upto):
    """The sandbox's answer. Mouser values in the tables go in from the cache; a run that wants one
    never looked up (or, on ▶, any older than an hour) gets it fetched and runs once more."""
    seconds = client.AUTO_SECONDS if mode == "auto" else client.MANUAL_SECONDS
    links = cells.mouser_links(doc, mouser.is_mouser)
    data = mouser.parts(conn, links, refresh=mode != "auto", fetch_new=False) if links else {}
    req = {**cells.request(doc, upto), "mouser": data}
    answer = client.send(req, seconds)
    wanted = [w for w in answer.get("wanted") or [] if isinstance(w, str) and mouser.is_mouser(w)]
    if wanted and not answer.get("error"):
        data.update(mouser.parts(conn, wanted, refresh=mode != "auto", fetch_new=True))
        answer = client.send({**req, "mouser": data}, seconds)
    return answer


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) -------------------------------------------

def _test_local():
    """The sandbox as a local process (sandbox/kernel.py, no limits) and Mouser answers from the
    request: {"mouser": {value: part}}."""
    import json as _json
    import os as _os
    import subprocess
    import sys

    kernel = _os.path.join(_os.path.dirname(__file__), "..", "..", "sandbox", "kernel.py")

    def send(req, seconds):
        out = subprocess.run([sys.executable, kernel], input=_json.dumps(req), capture_output=True, text=True,
                             timeout=seconds + 20)
        return {**_json.loads(out.stdout.rstrip("\n").rsplit("\n", 1)[-1]), "seconds": 0.1}

    fake = (request.get_json(silent=True) or {}).get("mouser") or {}
    client.send = send
    mouser.parts = lambda conn, values, *, refresh=False, fetch_new=True: {
        v: fake.get(v, {"error": "ei leitud"}) for v in values if fetch_new or refresh or v in fake}
    return "", 204


@bp.record
def _test_pages(state):
    import os as _os
    if _os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/arvuta/kohalik", "arvuta.test_local", _test_local, methods=["POST"])
