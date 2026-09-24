"""app/ai/tools.py: each tool called the way the SDK calls it (the async handler with the model's
arguments)."""

import asyncio
import json
import sys
import types
from datetime import date

import pytest

from app import collab, events, notes
from app.ai import tools


def call(ctx, name, **args):
    found = {t.name: t for t in tools.build(ctx)}[name]
    out = asyncio.run(found.handler(args))
    text = out["content"][0]["text"]
    if out.get("is_error"):
        return "ERROR: " + text
    try:
        return json.loads(text)
    except ValueError:
        return text


@pytest.fixture
def ctx(conn):
    return tools.Context(conn)


def test_exactly_these_tools(ctx):
    assert tuple(t.name for t in tools.build(ctx)) == tools.NAMES


def test_otsi_markmeid(conn, ctx):
    notes.create(conn, "Matemaatika", body="Kontrolltöö reedel ruumis 204.")
    hits = call(ctx, "otsi_markmeid", query="kontrolltöö")
    assert [h["slug"] for h in hits] == ["matemaatika"]
    assert "\x02" not in hits[0]["snippet"] and "Kontrolltöö" in hits[0]["snippet"]


def test_loe_markmet_caps_long_notes(conn, ctx):
    notes.create(conn, "Pikk", body="a" * 50_000)
    got = call(ctx, "loe_markmet", slug="pikk")
    assert got["title"] == "Pikk"
    assert len(got["body"]) < tools.NOTE_CAP + 100 and "jäi välja" in got["body"]
    assert call(ctx, "loe_markmet", slug="pole") == "ERROR: Märget ei leitud."


def test_loo_markme(conn, ctx):
    got = call(ctx, "loo_markme", title="Ostunimekiri", body="- piim", folder="kodu")
    note = notes.by_slug(conn, got["slug"])
    assert note["folder"] == "kodu" and note["body"] == "# Ostunimekiri\n\n- piim"
    assert call(ctx, "loo_markme", title="X", body="y", folder="../..").startswith("ERROR")


def test_lisa_markmesse_appends(conn, ctx):
    note = notes.create(conn, "Päevik", body="# Päevik\n\nEsimene.\n")
    assert call(ctx, "lisa_markmesse", slug="paevik", text="Teine.") == "Lisatud."
    assert notes.get(conn, note["id"])["body"] == "# Päevik\n\nEsimene.\n\nTeine.\n"


def test_lisa_markmesse_busy_note(conn, ctx, monkeypatch):
    note = notes.create(conn, "Päevik", body="# Päevik\n")
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    assert call(ctx, "lisa_markmesse", slug="paevik", text="Teine.") == "ERROR: Märget muudetakse teises aknas."
    assert notes.get(conn, note["id"])["body"] == "# Päevik\n"


def test_lisa_markmesse_never_writes_the_open_note(conn):
    note = notes.create(conn, "Avatud", body="# Avatud\n")
    ctx = tools.Context(conn, note)
    assert call(ctx, "lisa_markmesse", slug="avatud", text="Uus lõik.") == "Tekst lisatakse kasutaja avatud märkme lõppu."
    assert ctx.edits == ["Uus lõik."]
    assert notes.get(conn, note["id"])["body"] == "# Avatud\n"  # the asker's editor applies it
    assert notes.get(conn, note["id"])["rev"] == note["rev"]


def test_sundmused(conn, ctx, monkeypatch):
    seen = {}

    def occurrences(conn, start, end, **kw):
        seen.update(start=start, end=end)
        return [{"id": 1, "title": "Tund", "start": "2026-10-01T08:00", "end": None, "all_day": False,
                 "kind": "tund", "status": "kinnitatud", "location": "", "source_quote": "x" * 5000}]
    monkeypatch.setattr(events, "occurrences", occurrences)
    got = call(ctx, "sundmused", start="2026-10-01", end="2026-10-07")
    assert got[0]["title"] == "Tund" and "source_quote" not in got[0]
    assert seen == {"start": date(2026, 10, 1), "end": date(2026, 10, 8)}  # end included
    assert call(ctx, "sundmused", start="3.10", end="2026-10-07").startswith("ERROR: Vigane kuupäev")
    assert call(ctx, "sundmused", start="2026-10-07", end="2026-10-01").startswith("ERROR")


def test_loo_sundmus_timed(conn):
    note = notes.create(conn, "Kool")
    ctx = tools.Context(conn, note)
    got = call(ctx, "loo_sundmus", title="Matemaatika kontrolltöö", date="2026-10-02", time="14:15",
               kind="kontrolltöö")
    row = conn.execute("SELECT * FROM events WHERE id = ?", (got["id"],)).fetchone()
    assert row["source"] == "ai" and row["status"] == "kinnitatud" and row["kind"] == "kontrolltöö"
    assert row["start_at"] == "2026-10-02T11:15:00Z" and row["all_day"] == 0  # 14:15 in Tallinn, EEST
    assert row["note_id"] == note["id"]
    assert got == {"id": row["id"], "title": "Matemaatika kontrolltöö", "start": "2026-10-02T14:15"}
    assert ctx.created == [got]


def test_loo_sundmus_all_day_and_errors(conn, ctx):
    got = call(ctx, "loo_sundmus", title="Ekskursioon", date="2026-10-09")
    row = conn.execute("SELECT * FROM events WHERE id = ?", (got["id"],)).fetchone()
    assert (row["all_day"], row["start_date"], row["end_date"]) == (1, "2026-10-09", "2026-10-10")
    assert got["start"] == "2026-10-09"
    assert call(ctx, "loo_sundmus", title="X", date="reedel").startswith("ERROR: Vigane kuupäev")
    assert call(ctx, "loo_sundmus", title="X", date="2026-10-09", time="25:00").startswith("ERROR")
    assert call(ctx, "loo_sundmus", title="X", date="2026-10-09", kind="pidu").startswith("ERROR")
    assert call(ctx, "loo_sundmus", title=" ", date="2026-10-09").startswith("ERROR")
    assert len(ctx.created) == 1


def test_ulesanded(conn, ctx):
    notes.create(conn, "Kool", body="# Kool\n\n- [ ] õpi @2026-10-02\n- [x] tehtud @2026-10-03\n- [ ] kunagi\n")
    open_ = call(ctx, "ulesanded")
    assert [t["text"] for t in open_] == ["õpi", "kunagi"]
    ranged = call(ctx, **{"name": "ulesanded", "from": "2026-10-03", "to": "2026-10-31"})
    assert [(t["text"], t["done"]) for t in ranged] == [("tehtud", True)]


def test_aju_otsing(conn, ctx, monkeypatch):
    fake = types.ModuleType("app.gbrain")
    fake.search = lambda query, limit=8, *, conn=None: [{"slug": "pages/x", "title": query, "snippet": "", "score": 1}]
    monkeypatch.setitem(sys.modules, "app.gbrain", fake)
    assert call(ctx, "aju_otsing", query="proxmox")[0]["title"] == "proxmox"


def test_aju_otsing_without_gbrain(conn, ctx, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.gbrain", None)  # import raises ModuleNotFoundError
    assert call(ctx, "aju_otsing", query="x") == "ERROR: Aju (gbrain) pole saadaval."
