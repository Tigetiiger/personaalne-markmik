"""Aktsiad: the AI package's API (docs/CONTRACTS.md, "stocks-ai"). A stock's research notes and
nightly calls, the Avasta runs, and the two on-demand runs, Uuri kohe and Avasta, streamed as
server-sent events like the assistant's (`event: <type>` + `data: <json>`):
  {"type": "progress", "text": "Loen 3/8 lehte…"}, then {"type": "done", "id"} or
  {"type": "error", "message"}.
One on-demand run at a time (the claim `web-ai`): a second gets 409. The run goes on a thread of its
own, so a closed browser tab does not stop it half way; the thread releases the claim. Owner only
(default deny)."""

import json
import logging
import os
import queue
import threading
import time

from flask import Blueprint, Response, current_app, jsonify, request

from .. import ics
from ..ai import runner
from ..db import connect, get_db, tx
from ..security import rate_limit
from ..stocks import common, explore, research
from ..stocks.common import by_symbol
from ..util import local_today

log = logging.getLogger("markmik.stocks.ai")
bp = Blueprint("stocks_ai", __name__)

CLAIM = "web-ai"
KEEPALIVE_S = 15  # a comment line now and then: the tunnel closes a stream silent for 100 s
ANALYSES_SHOWN = 20
VERDICTS_SHOWN = 30
EXPLORATIONS_SHOWN = 20
BUSY = "Üks AI-uuring juba käib. Oota, kuni see lõpeb."


def _bad(message, status=400):
    return jsonify(error=message), status


def _safe_url(url):
    # From the model: only web addresses become links (never javascript:).
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None


def _stock(symbol):
    row = by_symbol(get_db(), symbol)
    return (row, None) if row is not None else (None, _bad("Sellist aktsiat ei ole.", 404))


# --- Reading ---------------------------------------------------------------------------------------

def analysis_json(r):
    return {
        "id": r["id"], "as_of": r["as_of"], "kind": r["kind"], "created_at": r["created_at"], "model": r["model"],
        "summary": r["summary"], "what_changed": r["what_changed"],
        "bull": research.claims_of(r["bull_json"]), "bear": research.claims_of(r["bear_json"]),
        "catalysts": json.loads(r["catalysts_json"]), "risks": json.loads(r["risks_json"]),
        "what_would_change_my_mind": json.loads(r["change_my_mind_json"]),
        "citations": [{"url": _safe_url(c.get("url")), "title": c.get("title") or c.get("url"), "quote": c.get("quote"),
                       "published_at": c.get("published_at")} for c in json.loads(r["citations_json"]) if isinstance(c, dict)],
    }


def verdict_json(r):
    return {"id": r["id"], "as_of": r["as_of"], "created_at": r["created_at"], "model": r["model"], "verdict": r["verdict"],
            "conviction": r["conviction"], "summary": r["summary"], "reasons_for": json.loads(r["reasons_for_json"]),
            "reasons_against": json.loads(r["reasons_against_json"]),
            "what_would_change": json.loads(r["what_would_change_json"])}


def exploration_json(r):
    ideas = json.loads(r["ideas_json"])
    for idea in ideas:
        idea["links"] = [{**link, "url": _safe_url(link.get("url"))} for link in idea.get("links", [])]
    return {"id": r["id"], "created_at": r["created_at"], "model": r["model"], "focus": r["focus"],
            "requested_ideas": r["requested_ideas"], "market_context": r["market_context"], "ideas": ideas,
            "dropped": json.loads(r["dropped_json"])}


@bp.get("/api/stocks/research/<symbol>")
def analyses(symbol):
    row, error = _stock(symbol)
    if error:
        return error
    conn = get_db()
    rows = conn.execute("SELECT * FROM analyses WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT ?",
                        (row["id"], ANALYSES_SHOWN)).fetchall()
    return jsonify(analyses=[analysis_json(r) for r in rows], running=common.claimed(conn, CLAIM),
                   can_run=row["kind"] == "stock")


@bp.get("/api/stocks/verdicts/<symbol>")
def verdicts(symbol):
    row, error = _stock(symbol)
    if error:
        return error
    rows = get_db().execute("SELECT * FROM verdicts WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT ?",
                            (row["id"], VERDICTS_SHOWN)).fetchall()
    return jsonify(verdicts=[verdict_json(r) for r in rows], kind=row["kind"])


@bp.get("/api/stocks/explore")
def explorations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM explorations ORDER BY created_at DESC, id DESC LIMIT ?", (EXPLORATIONS_SHOWN,)).fetchall()
    return jsonify(explorations=[exploration_json(r) for r in rows], running=common.claimed(conn, CLAIM))


# --- On-demand runs --------------------------------------------------------------------------------

@bp.post("/api/stocks/research/<symbol>")
@rate_limit("stocks-research", 10, 3600)
def research_now(symbol):
    row, error = _stock(symbol)
    if error:
        return error
    if row["kind"] != "stock":
        return _bad("Uuri kohe on ainult aktsiatele, mitte ETF-idele.")
    if not common.claim(get_db(), CLAIM):
        return _bad(BUSY, 409)
    iid = row["id"]
    return _stream(lambda conn, progress: research.research(conn, iid, local_today(), kind="on_demand", progress=progress),
                   "Uuring")


@bp.post("/api/stocks/explore")
@rate_limit("stocks-explore", 5, 3600)
def explore_now():
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    focus, count = data.get("focus") or "", data.get("count", explore.DEFAULT_IDEAS)
    if not isinstance(focus, str) or len(focus) > explore.FOCUS_MAX:
        return _bad(f"Fookus võib olla kuni {explore.FOCUS_MAX} märki.")
    if isinstance(count, bool) or not isinstance(count, int) or not explore.MIN_IDEAS <= count <= explore.MAX_IDEAS:
        return _bad(f"Ideede arv peab olema {explore.MIN_IDEAS}–{explore.MAX_IDEAS}.")
    if not common.claim(get_db(), CLAIM):
        return _bad(BUSY, 409)
    return _stream(lambda conn, progress: explore.explore(conn, focus, count, local_today(), progress=progress), "Avastamine")


def _stream(work, what):
    """work(conn, progress) -> id, on a thread with its own connection; its steps streamed. The
    claim is already taken: the thread releases it when the work ends, whether anyone still reads
    the stream or not."""
    path = current_app.config["DATABASE_PATH"]
    events = queue.Queue()

    def run():
        conn = connect(path)
        try:
            events.put({"type": "done", "id": work(conn, lambda text: events.put({"type": "progress", "text": text}))})
        except runner.AIUnavailable as exc:
            events.put({"type": "error", "message": str(exc)})
        except RuntimeError as exc:
            log.warning("%s failed: %s", what, exc)
            events.put({"type": "error", "message": str(exc)})
        except Exception:
            log.exception("%s failed", what)
            events.put({"type": "error", "message": f"{what} katkes ootamatu vea tõttu."})
        finally:
            common.release(conn, CLAIM)
            conn.close()
            events.put(None)

    threading.Thread(target=run, name="stocks-ai", daemon=True).start()

    def frames():
        while True:
            try:
                ev = events.get(timeout=KEEPALIVE_S)
            except queue.Empty:
                yield ": ootan\n\n"
                continue
            if ev is None:
                return
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(frames(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) ------------------------------------------

_fake = {"answers": {}, "pages": {}, "delay": 0.0, "structured": None, "download": None}


def _fake_structured(prompt, schema, model):
    by_schema = {id(research.DISCOVER_SCHEMA): "discover", id(research.RESEARCH_SCHEMA): "research",
                 id(explore.DISCOVER_SCHEMA): "explore_discover", id(explore.SCHEMA): "explore"}
    name = by_schema.get(id(schema))
    if name is None or name not in _fake["answers"]:
        return _fake["structured"](prompt, schema, model)
    return _fake["answers"][name]


def _fake_download(url, time_limit=60, *, user_agent=None):
    if url not in _fake["pages"]:
        return _fake["download"](url, time_limit, user_agent=user_agent)
    time.sleep(_fake["delay"])
    return _fake["pages"][url].encode()


def _test_fake():
    """POST {answers: {discover, research, explore_discover, explore}, pages: {url: html}, delay}:
    the fake model answers those calls and those pages are "fetched", each after `delay` seconds.
    DELETE: back to the defaults."""
    if _fake["structured"] is None:
        _fake["structured"], _fake["download"] = runner.fake_structured, ics.download
    if request.method == "DELETE":
        runner.fake_structured, ics.download = _fake["structured"], _fake["download"]
        return "", 204
    data = request.get_json()
    _fake.update(answers=data.get("answers", {}), pages=data.get("pages", {}), delay=float(data.get("delay", 0)))
    runner.fake_structured, ics.download = _fake_structured, _fake_download
    return "", 204


def _dump(value):
    return json.dumps(value or [], ensure_ascii=False)


def _test_seed():
    """Replace every stocks row with {instruments: [{symbol, name, kind?, watch?}], analyses:
    [{symbol, as_of, kind?, summary, what_changed?, bull?, bear?, catalysts?, risks?,
    what_would_change_my_mind?, citations?}], verdicts: [{symbol, as_of, verdict, conviction, summary,
    reasons_for?, reasons_against?, what_would_change?}], explorations: [{focus?, requested_ideas?,
    market_context, ideas?, dropped?, created_at?}], claim?: bool (an on-demand run holds web-ai)}."""
    data = request.get_json()
    conn = get_db()
    common.clear_all(conn)
    with tx(conn):
        ids = {}
        for i in data.get("instruments", []):
            ids[i["symbol"]] = conn.execute(
                "INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, ?, ?, ?, ?)",
                (i["symbol"], i.get("exchange", "NMS"), i.get("currency", "USD"), i.get("name", i["symbol"]),
                 i.get("kind", "stock"))).lastrowid
            if i.get("watch", True):
                conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (ids[i["symbol"]],))
        for a in data.get("analyses", []):
            conn.execute(
                "INSERT INTO analyses (instrument_id, kind, as_of, summary, what_changed, bull_json, bear_json, catalysts_json, "
                "risks_json, change_my_mind_json, citations_json, prompt_version, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', 'sonnet')",
                (ids[a["symbol"]], a.get("kind", "deep"), a["as_of"], a["summary"], a.get("what_changed"), _dump(a.get("bull")),
                 _dump(a.get("bear")), _dump(a.get("catalysts")), _dump(a.get("risks")), _dump(a.get("what_would_change_my_mind")),
                 _dump(a.get("citations"))))
        for v in data.get("verdicts", []):
            conn.execute(
                "INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, reasons_for_json, reasons_against_json, "
                "what_would_change_json, prompt_version, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', 'sonnet')",
                (ids[v["symbol"]], v["as_of"], v["verdict"], v["conviction"], v["summary"], _dump(v.get("reasons_for")),
                 _dump(v.get("reasons_against")), _dump(v.get("what_would_change"))))
        for e in data.get("explorations", []):
            conn.execute(
                "INSERT INTO explorations (focus, requested_ideas, market_context, ideas_json, dropped_json, prompt_version, model, "
                "created_at) VALUES (?, ?, ?, ?, ?, 'test', 'sonnet', coalesce(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))",
                (e.get("focus"), e.get("requested_ideas", 6), e["market_context"], _dump(e.get("ideas")), _dump(e.get("dropped")),
                 e.get("created_at")))
    if data.get("claim"):
        common.claim(conn, CLAIM)
    return "", 204


@bp.record
def _test_pages(state):
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/stocks-ai/seed", "stocks_ai.test_seed", _test_seed, methods=["POST"])
        state.app.add_url_rule("/_test/stocks-ai/fake", "stocks_ai.test_fake", _test_fake, methods=["POST", "DELETE"])
