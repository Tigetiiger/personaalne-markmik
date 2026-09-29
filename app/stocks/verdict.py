"""The nightly would buy / hold / sell call (stock-oracle's research/verdict.py without the
prediction model): one no-tools call per stock, holdings first, ETFs left out. It sees the latest
research note, the 15 most relevant documents scored in the last 14 days, the price statistics and
trend, the owner's position and its own previous call. The call is stored with a snapshot of what it
was shown. The page shows it as "Ostaks ▲", "Hoiaks ◆" or "Müüks ▼"."""

import json
from datetime import timedelta

from ..util import iso
from . import aicall
from .prompts import load
from .research import (clock, dumps, fence, held_ids, json_lines, market_payload, night_instruments, position_payload,
                       previous_payload, run_night, signals_since, text, texts)

MODEL = "sonnet"  # no tools and a short answer: sonnet spares the subscription
TIMEOUT = 300
SIGNAL_DAYS = 14
MAX_SIGNALS = 15
VERDICTS = ("buy", "hold", "sell")
CONVICTIONS = ("low", "medium", "high")

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "conviction": {"type": "string", "enum": list(CONVICTIONS)},
        "summary": {"type": "string", "description": "the call and its main reason, 1-2 sentences"},
        "reasons_for": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "reasons_against": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "what_would_change": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["verdict", "conviction", "summary", "reasons_for", "reasons_against", "what_would_change"],
    "additionalProperties": False,
}


def latest(conn, instrument_id):
    return conn.execute("SELECT * FROM verdicts WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT 1",
                        (instrument_id,)).fetchone()


def previous_call(conn, instrument_id, today):
    row = conn.execute("SELECT * FROM verdicts WHERE instrument_id = ? AND as_of < ? ORDER BY as_of DESC, id DESC LIMIT 1",
                       (instrument_id, today.isoformat())).fetchone()
    return row and {"as_of": row["as_of"], "verdict": row["verdict"], "conviction": row["conviction"], "summary": row["summary"]}


def gather(conn, row, today, now):
    """What the call is shown: {previous, research, signals, market, position}."""
    note = conn.execute("SELECT * FROM analyses WHERE instrument_id = ? AND as_of <= ? ORDER BY as_of DESC, id DESC LIMIT 1",
                        (row["id"], today.isoformat())).fetchone()
    return {
        "previous": previous_call(conn, row["id"], today),
        "research": previous_payload(note) if note else None,
        "signals": [{"published_at": (s["published_at"] or "")[:10] or None, "source": s["source"], "title": s["title"],
                     "summary": s["summary"], "event_type": s["event_type"], "sentiment": s["sentiment"],
                     "relevance": s["relevance"]}
                    for s in signals_since(conn, row["id"], iso(now - timedelta(days=SIGNAL_DAYS)), MAX_SIGNALS)],
        "market": market_payload(conn, row["id"], today),
        "position": position_payload(conn, row["id"], today),
    }


def prompt_for(row, today, inputs, fence_=None):
    """-> (prompt, prompt_version)"""
    fence_ = fence_ or fence()
    prompt = load("verdict")
    return prompt.render(
        fence=fence_, company=row["name"], symbol=row["symbol"], as_of=today.isoformat(), signal_days=SIGNAL_DAYS,
        previous=dumps(inputs["previous"]), research=dumps(inputs["research"]), signals=json_lines(inputs["signals"]),
        market=dumps(inputs["market"]), position=dumps(inputs["position"])), prompt.version


def verdict_for(conn, row, today, now, *, run_id=None):
    """Ask for one stock's call and store it; returns the verdicts id. Raises like aicall.call."""
    inputs = gather(conn, row, today, now)
    prompt, version = prompt_for(row, today, inputs)
    answer, ai_run_id = aicall.call_logged(conn, "verdict", prompt, SCHEMA, model=MODEL, prompt_version=version,
                                           instrument_id=row["id"], run_id=run_id, timeout=TIMEOUT)
    summary = text(answer.get("summary"), 600)
    if answer.get("verdict") not in VERDICTS or answer.get("conviction") not in CONVICTIONS or summary is None:
        raise RuntimeError("AI vastus on vigane (hinnang puudub)")
    snapshot = {"previous": inputs["previous"], "research_as_of": inputs["research"] and inputs["research"]["as_of"],
                "signals": len(inputs["signals"]), "market": inputs["market"], "position": inputs["position"]}
    return conn.execute(
        "INSERT INTO verdicts (instrument_id, ai_run_id, run_id, as_of, verdict, conviction, summary, reasons_for_json, "
        "reasons_against_json, what_would_change_json, inputs_json, prompt_version, model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["id"], ai_run_id, run_id, today.isoformat(), answer["verdict"], answer["conviction"], summary,
         json.dumps(texts(answer.get("reasons_for"), 5, 500), ensure_ascii=False),
         json.dumps(texts(answer.get("reasons_against"), 5, 500), ensure_ascii=False),
         json.dumps(texts(answer.get("what_would_change"), 4, 500), ensure_ascii=False),
         dumps(snapshot), version, MODEL)).lastrowid


def verdict_night(conn, run_id, today, now, deadline):
    """Night step "verdict": every followed stock (not ETFs), holdings first, then by symbol. A
    stock with a call for `today` gets no second one."""
    now = clock(now)
    held = held_ids(conn)
    rows = sorted((r for r in night_instruments(conn, held) if r["kind"] == "stock"),
                  key=lambda r: (r["id"] not in held, r["symbol"]))

    def done(row):
        return conn.execute("SELECT 1 FROM verdicts WHERE instrument_id = ? AND as_of = ?",
                            (row["id"], today.isoformat())).fetchone() is not None

    return run_night(rows, deadline, now, lambda row: verdict_for(conn, row, today, now(), run_id=run_id),
                     done_already=done)
