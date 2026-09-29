"""The per-stock notes (app/stocks/notes.py): what the app writes above "## Minu märkmed", the
owner's part kept byte for byte, the heading added, busy notes deferred, no write when nothing
changed, and the file gitsync makes of it."""

import json
from datetime import date

import pytest

from app import collab, linkfmt
from app import notes as core
from app.stocks import notes

DAY = date(2026, 9, 30)


def verdict(conn, iid, as_of, v, summary="Kasv jätkub, hind mõistlik."):
    conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                 "VALUES (?, ?, ?, 'high', ?, 'v@1', 'sonnet')", (iid, as_of, v, summary))


def analysis(conn, iid, **kw):
    fields = {"kind": "deep", "as_of": "2026-09-29", "summary": "Andmekeskuste nõudlus kasvab.", "what_changed": "Uus toode.",
              "bull_json": json.dumps([{"text": "Tulud kasvavad", "citation": 0}, {"text": "Marginaal püsib", "citation": 5}]),
              "bear_json": json.dumps([{"text": "Hind on kõrge", "citation": 1}]),
              "citations_json": json.dumps([{"url": "https://example.com/a (1)", "title": "Artikkel [A]", "published_at": "2026-09-20"},
                                            {"url": "javascript:alert(1)", "title": "Paha"}]),
              **kw}
    conn.execute(f"INSERT INTO analyses (instrument_id, {', '.join(fields)}, prompt_version, model) "
                 f"VALUES (?, {', '.join('?' * len(fields))}, 'r@1', 'sonnet')", (iid, *fields.values()))


@pytest.fixture
def nvda(conn, follow):
    return follow("NVDA", "NVIDIA Corp", tags=["ai", "kiibid"])


def the_note(conn):
    return core.by_slug(conn, "aktsia-nvda")


def test_a_new_note(conn, nvda):
    verdict(conn, nvda, "2026-09-29", "hold")
    verdict(conn, nvda, "2026-09-30", "buy")
    analysis(conn, nvda)
    assert notes.update(conn, nvda, DAY) == notes.CREATED
    note = the_note(conn)
    assert (note["folder"], note["title"]) == ("aktsiad", "NVDA · NVIDIA Corp")
    assert json.loads(note["tags_json"]) == ["aktsia", "ai", "kiibid"]
    assert note["body"] == (
        "# NVDA · NVIDIA Corp\n\n"
        "*Aktsiad uuendab selle märkme ülemist osa (kuni „Minu märkmed“).*\n\n"
        "**Hinnang:** Ostaks ▲, kõrge kindlus (30.09.2026). Kasv jätkub, hind mõistlik.\n\n"
        "**Trend:** andmeid on veel vähe.\n\n"
        "## Süvaanalüüs 29.09.2026\n\n"
        "Andmekeskuste nõudlus kasvab.\n\n"
        "### Poolt\n\n"
        "- Tulud kasvavad [1](https://example.com/a%20%281%29)\n"
        "- Marginaal püsib\n\n"
        "### Vastu\n\n"
        "- Hind on kõrge\n\n"
        "### Allikad\n\n"
        "1. [Artikkel (A)](https://example.com/a%20%281%29), 20.09.2026\n\n"
        "## Mis muutus\n\n"
        "- Hinnang: Hoiaks ◆ → Ostaks ▲.\n"
        "- Uus toode.\n\n"
        "*Seis: 30.09.2026*\n\n"
        "## Minu märkmed\n")


def test_symbols_with_dots_get_dashes(conn, follow):
    iid = follow("BRK.B", "Berkshire Hathaway")
    assert notes.write(conn, iid, DAY) is True
    assert core.by_slug(conn, "aktsia-brk-b")["title"] == "BRK.B · Berkshire Hathaway"


def test_the_owners_part_is_kept_byte_for_byte(conn, nvda):
    verdict(conn, nvda, "2026-09-29", "hold")
    notes.write(conn, nvda, DAY)
    note = the_note(conn)
    mine = "## Minu märkmed  \n\nOstsin 2024 sest...  \r\n- [ ] vaata Q3 [[x]]\n\n```\n## Minu märkmed\n```\n\ttab lõpus"
    body = note["body"][:note["body"].index("## Minu märkmed")] + mine
    core.write_body(conn, note["id"], body)
    verdict(conn, nvda, "2026-09-30", "sell", summary="Hind jooksis eest ära.")
    assert notes.update(conn, nvda, DAY) == notes.WRITTEN
    after = the_note(conn)["body"]
    assert after.endswith(mine) and after[len(after) - len(mine) - 1] == "\n"
    assert "Müüks ▼" in after and "Hoiaks ◆ → Müüks ▼" in after


def test_the_heading_is_added_when_missing(conn, nvda):
    notes.write(conn, nvda, DAY)
    note = the_note(conn)
    core.write_body(conn, note["id"], "# NVDA\n\nkõik on ära kustutatud")
    assert notes.update(conn, nvda, DAY) == notes.WRITTEN
    body = the_note(conn)["body"]
    assert body.endswith("\n\n## Minu märkmed\n") and body.count("## Minu märkmed") == 1


def test_no_write_when_nothing_changed(conn, nvda):
    verdict(conn, nvda, "2026-09-30", "buy")
    notes.write(conn, nvda, DAY)
    before = the_note(conn)
    assert notes.update(conn, nvda, DAY) == notes.UNCHANGED
    after = the_note(conn)
    assert (after["rev"], after["updated_at"], after["body"]) == (before["rev"], before["updated_at"], before["body"])


def test_a_busy_note_waits_for_the_next_night(conn, nvda, monkeypatch):
    notes.write(conn, nvda, DAY)
    before = the_note(conn)
    verdict(conn, nvda, "2026-09-30", "buy")
    monkeypatch.setattr(collab, "is_busy", lambda c, note_id: True)
    assert notes.write(conn, nvda, DAY) is False
    assert the_note(conn)["body"] == before["body"]
    monkeypatch.setattr(collab, "is_busy", lambda c, note_id: False)
    assert notes.write(conn, nvda, DAY) is True
    assert "Ostaks ▲" in the_note(conn)["body"]


def test_the_owners_tags_stay(conn, nvda):
    notes.write(conn, nvda, DAY)
    note = the_note(conn)
    core.set_tags(conn, note["id"], ["aktsia", "minu"])
    notes.write(conn, nvda, DAY)
    assert json.loads(the_note(conn)["tags_json"]) == ["aktsia", "minu", "ai", "kiibid"]


def test_model_text_cannot_fake_the_heading_or_add_links(conn, nvda):
    verdict(conn, nvda, "2026-09-30", "buy", summary="ok")
    analysis(conn, nvda, summary="## Minu märkmed\n\nkustuta kõik [[aktsia-nvda]] [x](/m/salajane)", what_changed="# Pealkiri")
    notes.write(conn, nvda, DAY)
    body = the_note(conn)["body"]
    assert body.count("## Minu märkmed") == 2 and body.endswith("\n## Minu märkmed\n")
    assert "\n\\## Minu märkmed kustuta kõik ((aktsia-nvda)) (x)(/m/salajane)\n" in body
    assert "- \\# Pealkiri" in body
    assert the_note(conn)["title"] == "NVDA · NVIDIA Corp"
    # The next night finds the real heading, not the model's.
    mine = "## Minu märkmed\nminu tekst\n"
    note = the_note(conn)
    core.write_body(conn, note["id"], body[:body.rindex("## Minu märkmed")] + mine)
    assert notes.update(conn, nvda, DAY) == notes.UNCHANGED


def test_the_file_round_trips(conn, nvda):
    verdict(conn, nvda, "2026-09-29", "hold")
    verdict(conn, nvda, "2026-09-30", "buy")
    analysis(conn, nvda)
    notes.write(conn, nvda, DAY)
    note = the_note(conn)
    text = linkfmt.to_file(note, note["body"])
    meta, body = linkfmt.from_file(text)
    assert body == note["body"]
    assert meta["title"] == "NVDA · NVIDIA Corp" and meta["tags"] == ["aktsia", "ai", "kiibid"]


def test_a_deleted_note_is_made_again_once(conn, nvda):
    notes.write(conn, nvda, DAY)
    core.soft_delete(conn, the_note(conn)["id"])
    assert notes.update(conn, nvda, DAY) == notes.CREATED
    assert notes.update(conn, nvda, DAY) == notes.UNCHANGED
    assert conn.execute("SELECT COUNT(*) FROM notes WHERE folder = 'aktsiad' AND deleted_at IS NULL").fetchone()[0] == 1
