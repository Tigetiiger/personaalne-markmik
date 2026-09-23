"""Tasks in notes (app/tasks.py): parsing, due dates, rebuild, toggle."""

from datetime import date

import pytest

from app import collab, notes, tasks, util

THU = date(2026, 9, 24)


@pytest.mark.parametrize("line, ref, text, done, due_date, due_time", [
    ("- [ ] tee ülesanded @reede", THU, "tee ülesanded", False, "2026-09-25", None),
    ("- [ ] tee @Reedel", THU, "tee", False, "2026-09-25", None),
    ("* [x] valmis @3.10", THU, "valmis", True, "2026-10-03", None),
    ("- [X] suur X @homme kell 9", THU, "suur X", True, "2026-09-25", "09:00"),
    ("    - [ ] alam @2026-10-03 14.15", THU, "alam", False, "2026-10-03", "14:15"),
    ("+ [ ] pluss @3.10 14:15 ruumis 5", THU, "pluss ruumis 5", False, "2026-10-03", "14:15"),
    ("- [ ] punktiga @3.10.", THU, "punktiga", False, "2026-10-03", None),
    ("- [ ] koma @03.10.2026, siis", THU, "koma siis", False, "2026-10-03", None),
    ("- [ ] @reede", THU, "@reede", False, None, None),              # bare token: Muya's menu
    ("- [ ] kirjuta @mari", THU, "kirjuta @mari", False, None, None),
    ("- [ ] kirjuta @mari @ülehomme", THU, "kirjuta @mari", False, "2026-09-26", None),
    ("- [ ] saada foo@reede.ee", THU, "saada foo@reede.ee", False, None, None),
    ("- [ ] `kood @reede`", THU, "`kood @reede`", False, None, None),
    ("- [ ] number @14.15", THU, "number @14.15", False, None, None),
    ("- [ ] aastavahetus @5.01", date(2026, 12, 20), "aastavahetus", False, "2027-01-05", None),
    ("- [ ] eelmine @28.12", date(2027, 1, 3), "eelmine", False, "2026-12-28", None),
    ("- [ ]", THU, "", False, None, None),
])
def test_parse(line, ref, text, done, due_date, due_time):
    [item] = tasks.parse(line, ref)
    assert (item["text"], item["done"], item["due_date"], item["due_time"]) == (text, done, due_date, due_time)
    assert line[item["mark_pos"]] in " xX"


@pytest.mark.parametrize("body", [
    "-[ ] tühikuta",
    "- [ ]tühikuta",
    "- [y] vale märk",
    "1. [ ] nummerdatud",
    "tekst - [ ] keskel",
    "```\n- [ ] koodis\n```",
    "~~~~\n- [ ] koodis\n~~~\n- [ ] ikka koodis",
])
def test_not_tasks(body):
    assert tasks.parse(body, THU) == []


def test_line_keys_stable():
    first = tasks.parse("- [ ] a\n- [ ] b\n- [ ] a", THU)
    assert [i["key"].split(":")[1] for i in first] == ["0", "0", "1"]
    later = tasks.parse("# Uus rida\n- [x]  a \n\n* [ ] a\n  - [ ] b", THU)
    assert [i["key"] for i in later] == [first[0]["key"], first[2]["key"], first[1]["key"]]


def _rows(conn, note_id):
    return conn.execute("SELECT id, text, done, due_date, due_time FROM tasks WHERE note_id = ? ORDER BY id",
                        (note_id,)).fetchall()


def test_rebuild_keeps_ids_and_first_due(conn, monkeypatch):
    monkeypatch.setattr(util, "local_today", lambda: THU)
    note = notes.create(conn, "T", body="# T\n- [ ] üks @homme\n- [ ] kaks")
    before = _rows(conn, note["id"])
    assert [(r["text"], r["due_date"]) for r in before] == [("üks", "2026-09-25"), ("kaks", None)]

    monkeypatch.setattr(util, "local_today", lambda: date(2026, 10, 1))
    notes.write_body(conn, note["id"], "# T\n- [ ] uus @homme\n- [x] üks @homme\n")
    after = _rows(conn, note["id"])
    assert [(r["id"], r["text"], r["done"], r["due_date"]) for r in after] == [
        (before[0]["id"], "üks", 1, "2026-09-25"),   # unchanged line keeps the day it was written
        (before[1]["id"] + 1, "uus", 0, "2026-10-02"),
    ]


def test_daily_note_uses_its_own_day(conn, monkeypatch):
    monkeypatch.setattr(util, "local_today", lambda: THU)
    note = notes.daily(conn, date(2026, 10, 5))  # a Monday
    notes.write_body(conn, note["id"], note["body"] + "- [ ] trenn @reede kl 18\n- [ ] pood @homme\n")
    assert [(r["due_date"], r["due_time"]) for r in _rows(conn, note["id"])] == [
        ("2026-10-09", "18:00"), ("2026-10-06", None)]


BODY = "# T\n- [ ] üks\n  * [x] kaks @3.10\n```\n- [ ] kood\n```\n- [ ] üks\ntekst"


def test_toggle(conn):
    note = notes.create(conn, "T", body=BODY)
    one, two, one_again = (r["id"] for r in _rows(conn, note["id"]))
    assert tasks.toggle(conn, one_again) is True
    assert notes.get(conn, note["id"])["body"] == BODY.replace("- [ ] üks\ntekst", "- [x] üks\ntekst")
    assert tasks.toggle(conn, two) is False
    assert tasks.toggle(conn, one) is True
    row = notes.get(conn, note["id"])
    assert row["body"] == "# T\n- [x] üks\n  * [ ] kaks @3.10\n```\n- [ ] kood\n```\n- [x] üks\ntekst"
    assert row["rev"] == 4
    assert [(r["id"], r["done"]) for r in _rows(conn, note["id"])] == [(one, 1), (two, 0), (one_again, 1)]


def test_toggle_missing(conn):
    assert tasks.toggle(conn, 999) is None
    note = notes.create(conn, "T", body="# T\n- [ ] a")
    [row] = _rows(conn, note["id"])
    notes.soft_delete(conn, note["id"])
    assert tasks.toggle(conn, row["id"]) is None


def test_toggle_busy(conn, monkeypatch):
    note = notes.create(conn, "T", body="# T\n- [ ] a")
    [row] = _rows(conn, note["id"])
    monkeypatch.setattr(collab, "is_busy", lambda c, note_id: True)
    with pytest.raises(notes.NoteBusy):
        tasks.toggle(conn, row["id"])
    assert notes.get(conn, note["id"])["body"] == "# T\n- [ ] a"
    assert _rows(conn, note["id"])[0]["done"] == 0
