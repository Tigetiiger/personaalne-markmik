"""Every model call of the stocks package: runner.run_structured plus a stock_ai_runs row (the
Allikad tab's last AI runs), and for a night's calls the night's own cap.

The night's cap (`night_cap`, counted in `stocks:calls:<run_id>`) sits on top of the day's
AI_DAILY_CAP, so a burst of news cannot use up the day's calls before the owner wakes."""

import time

from ..ai import runner
from ..db import tx
from ..util import now_iso
from . import config
from .common import get_json, set_json

NIGHT_CAP_FULL = "Öine AI-päringute piir on täis"


def call(conn, purpose, prompt, schema, *, model, prompt_version, instrument_id=None, run_id=None,
         web_search=False, timeout=None, max_turns=None, thinking=False):
    """The model's answer (a dict matching `schema`'s required keys; the caller checks the rest).
    Raises runner.AIUnavailable (the night stops its AI steps) or RuntimeError (skip that stock)."""
    return call_logged(conn, purpose, prompt, schema, model=model, prompt_version=prompt_version,
                       instrument_id=instrument_id, run_id=run_id, web_search=web_search, timeout=timeout,
                       max_turns=max_turns, thinking=thinking)[0]


def call_logged(conn, purpose, prompt, schema, *, model, prompt_version, instrument_id=None, run_id=None,
                web_search=False, timeout=None, max_turns=None, thinking=False):
    """call(), and the stock_ai_runs id of the call: (answer, ai_run_id)."""
    ai_run_id = conn.execute(
        "INSERT INTO stock_ai_runs (run_id, purpose, instrument_id, model, prompt_version, started_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'running')",
        (run_id, purpose, instrument_id, model, prompt_version, now_iso())).lastrowid
    started = time.monotonic()
    try:
        if run_id is not None:
            _count_night(conn, run_id)
        answer = runner.run_structured(conn, prompt, schema, model=model, web_search=web_search, timeout=timeout,
                                       max_turns=max_turns, thinking=thinking)
    except runner.AIUnavailable as exc:
        _finish(conn, ai_run_id, started, "unavailable", exc)
        raise
    except BaseException as exc:  # RuntimeError, and anything else: the row must not stay 'running'
        _finish(conn, ai_run_id, started, "failed", exc)
        raise
    _finish(conn, ai_run_id, started, "success", None)
    return answer, ai_run_id


def night_calls(conn, run_id):
    """Model calls the night `run_id` has made so far."""
    return get_json(conn, f"stocks:calls:{run_id}", 0)


def _count_night(conn, run_id):
    key = f"stocks:calls:{run_id}"
    with tx(conn):
        used = get_json(conn, key, 0)
        if used >= config.settings(conn)["night_cap"]:
            raise runner.AIUnavailable(NIGHT_CAP_FULL)
        set_json(conn, key, used + 1)


def _finish(conn, ai_run_id, started, status, exc):
    conn.execute("UPDATE stock_ai_runs SET status = ?, duration_ms = ?, error = ? WHERE id = ?",
                 (status, int((time.monotonic() - started) * 1000), str(exc)[:500] if exc is not None else None, ai_run_id))
