"""Real model calls (pytest -m live). Need CLAUDE_CODE_OAUTH_TOKEN in the environment; two calls:
one haiku extraction and one short sonnet chat that must use a tool."""

import os

import pytest

from app import notes
from app.ai import chat, runner

pytestmark = pytest.mark.live


@pytest.fixture
def live(monkeypatch):
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        pytest.skip("CLAUDE_CODE_OAUTH_TOKEN is not set")
    monkeypatch.delenv("AI_FAKE")
    # A developer's shell may run inside Claude Code: its session variables must not reach the CLI.
    for name in list(os.environ):
        if name.startswith("CLAUDE") and name != "CLAUDE_CODE_OAUTH_TOKEN":
            monkeypatch.delenv(name)


def test_structured_extraction(conn, live):
    schema = {"type": "object", "additionalProperties": False, "required": ["subject", "room"],
              "properties": {"subject": {"type": "string"}, "room": {"type": "string"}}}
    out = runner.run_structured(conn, "Matemaatika kontrolltöö toimub reedel ruumis 204. Eralda aine ja ruum.", schema,
                                system="Eralda tekstist andmed. Aine väikeste tähtedega.")
    assert out["room"] == "204" and "matemaatika" in out["subject"].lower()


def test_chat_uses_a_tool(conn, live):
    notes.create(conn, "Matemaatika", body="Kontrolltöö on reedel ruumis 311.")
    events = list(chat.turn(conn, None, "Otsi märkmetest sõna 'kontrolltöö' ja ütle, mis ruumis see on. "
                                        "Vasta ühe lausega."))
    types = [e["type"] for e in events]
    assert types[-1] == "done", events[-3:]
    tools_used = [e for e in events if e["type"] == "tool"]
    assert tools_used and tools_used[0]["name"] in ("otsi_markmeid", "loe_markmet")
    assert "311" in "".join(e["text"] for e in events if e["type"] == "delta")
