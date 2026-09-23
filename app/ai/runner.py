"""The one door to Claude. Everything that asks the model goes through run_structured() or
stream_chat(); security options live in one place (see docs/CONTRACTS.md, "AI runner").

AI_FAKE=1 (tests, local UI work): no model is called. run_structured() returns what
`fake_structured` returns; stream_chat() streams a canned answer. Real mode arrives with WP-F.
"""

import os

from ..util import local_today


class AIUnavailable(RuntimeError):
    """No token, over the daily cap, or the model could not be reached."""


DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "300"))


def is_fake():
    return os.environ.get("AI_FAKE") == "1"


def _default_fake_structured(prompt, schema, model):
    """An empty but schema-shaped answer: objects get their required keys as None/[]."""
    def empty(s):
        t = s.get("type")
        if t == "object":
            return {k: empty(s["properties"][k]) for k in s.get("required", [])}
        if t == "array":
            return []
        return None
    return empty(schema)


# Tests replace this: fake_structured = lambda prompt, schema, model: {...}
fake_structured = _default_fake_structured
FAKE_REPLY = "Proovivastus (AI_FAKE): "


def count_call(conn):
    """Count one model call against today's cap; raise AIUnavailable when over it."""
    day = local_today().isoformat()
    conn.execute(
        "INSERT INTO ai_usage (day, calls) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET calls = calls + 1", (day,)
    )
    calls = conn.execute("SELECT calls FROM ai_usage WHERE day = ?", (day,)).fetchone()["calls"]
    if calls > DAILY_CAP:
        raise AIUnavailable(f"päevane AI-päringute piir ({DAILY_CAP}) on täis")


def run_structured(conn, prompt, schema, *, model="haiku", system=None):
    """One model call with no tools whose answer must match `schema` (JSON Schema). Returns the
    parsed object. Raises AIUnavailable."""
    count_call(conn)
    if is_fake():
        return fake_structured(prompt, schema, model)
    raise AIUnavailable("päris AI-režiim pole veel valmis (WP-F)")


def stream_chat(conn, prompt, *, system=None, tools=(), model="sonnet", session_id=None):
    """Chat turn as a sync iterator of events:
        {"type": "delta", "text": str}
        {"type": "tool", "name": str, "input": dict, "result": object}
        {"type": "done", "session_id": str | None}
        {"type": "error", "message": str}
    `tools` are app-defined tool objects (WP-F); the model never gets built-in tools except
    WebSearch."""
    count_call(conn)
    if is_fake():
        text = FAKE_REPLY + prompt[:60]
        for i in range(0, len(text), 8):
            yield {"type": "delta", "text": text[i:i + 8]}
        yield {"type": "done", "session_id": session_id or "fake-session"}
        return
    yield {"type": "error", "message": "päris AI-režiim pole veel valmis (WP-F)"}


def test_connection(conn):
    """Ühendused card: one tiny call."""
    if is_fake():
        return {"ok": True, "summary": "AI_FAKE: mudelit ei kutsuta", "error": None}
    try:
        run_structured(conn, "Vasta: ok", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
    except AIUnavailable as exc:
        return {"ok": False, "summary": "", "error": str(exc)}
    return {"ok": True, "summary": "Claude vastas", "error": None}
