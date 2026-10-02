"""Real model calls (pytest -m live). Need CLAUDE_CODE_OAUTH_TOKEN in the environment; two calls:
one haiku extraction and one short sonnet chat that must use a tool."""

import json
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
    # The CLI's transcripts of both calls (prompt, note text) are gone.
    projects = os.path.join(runner._config_dir(), "projects")
    assert not os.path.isdir(projects) or os.listdir(projects) == []


# --- The home chat (docs/KODU-PLAN.md) -----------------------------------------------------------

def _written(text):
    """A picture of `text` in big letters, prepared as the home chat sends it."""
    import io

    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1600, 600), "white")
    ImageDraw.Draw(img).text((80, 200), text, fill="black", font=ImageFont.load_default(size=110))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return {"name": "a" * 40 + ".jpg", "media_type": "image/jpeg", "data": out.getvalue()}


def test_home_note_from_a_picture(conn, live):
    picture = _written("Osta piima ja leiba")
    conn.execute("INSERT INTO images (name, original_name, author) VALUES (?, 'nimekiri.jpg', 'omanik')", (picture["name"],))
    events = list(chat.turn(conn, None, "Tee sellest pildist märge.", images=[picture]))
    assert events[-1]["type"] == "done", events[-3:]
    made = [e for e in events if e["type"] == "tool" and e["name"] == "loo_markme" and e["result"].startswith("{")]
    assert made, [e for e in events if e["type"] == "tool"]
    note = notes.by_slug(conn, json.loads(made[0]["result"])["slug"])
    assert "piim" in note["body"].lower() and "leib" in note["body"].lower()
    assert f"/pildid/{picture['name']}" in note["body"]  # the picture goes into the note


def test_home_reminder_is_not_an_event(conn, live):
    events = list(chat.turn(conn, None, "Tuleta mulle homme kell 18 meelde, et helistada vanaemale."))
    names = [e["name"] for e in events if e["type"] == "tool"]
    assert "loo_meeldetuletus" in names and "loo_sundmus" not in names, names
    [made] = [e for e in events if e["type"] == "reminder_created"]
    assert made["remind_at"].endswith("T18:00")


def test_home_asks_when_event_or_reminder_is_unclear(conn, live):
    events = list(chat.turn(conn, None, "homme kell 18 helistada vanaemale"))
    names = [e["name"] for e in events if e["type"] == "tool"]
    assert "loo_meeldetuletus" not in names and "loo_sundmus" not in names, names
    assert "?" in "".join(e["text"] for e in events if e["type"] == "delta")
