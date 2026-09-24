"""Notes core (app/notes.py): create, slugs, dangling links, title rules, metadata, delete, daily."""

import json
from datetime import date

import pytest

from app import collab, notes


def test_create_defaults(conn):
    note = notes.create(conn, "Matemaatika", folder="/kool/")
    assert (note["slug"], note["title"], note["body"], note["folder"], note["rev"]) == (
        "matemaatika", "Matemaatika", "# Matemaatika\n", "kool", 1)
    assert notes.get(conn, note["id"])["id"] == notes.by_slug(conn, "matemaatika")["id"]


@pytest.mark.parametrize("titles, slugs", [
    (["Töö plaan", "Töö plaan", "Too plaan"], ["too-plaan", "too-plaan-2", "too-plaan-3"]),
    (["", ""], ["marge", "marge-2"]),
    (["Tree", "Suggest"], ["tree-2", "suggest-2"]),  # would be shadowed by /api/notes/tree
])
def test_slugs_unique(conn, titles, slugs):
    assert [notes.create(conn, t)["slug"] for t in titles] == slugs


def test_slug_never_changes(conn):
    note = notes.create(conn, "Vana nimi")
    notes.write_body(conn, note["id"], "# Uus nimi\n")
    note = notes.get(conn, note["id"])
    assert (note["slug"], note["title"]) == ("vana-nimi", "Uus nimi")


def test_deleted_slug_stays_taken(conn):
    notes.soft_delete(conn, notes.create(conn, "Plaan")["id"])
    assert notes.by_slug(conn, "plaan") is None
    assert notes.create(conn, "Plaan")["slug"] == "plaan-2"


def test_create_with_body_keeps_title(conn):
    note = notes.create(conn, "Matemaatika", body="Õppeaine märkmed")
    assert note["title"] == "Matemaatika"
    assert note["body"] == "# Matemaatika\n\nÕppeaine märkmed"
    assert notes.create(conn, "X", body="# Päris pealkiri\ntekst")["title"] == "Päris pealkiri"


def test_dangling_link_resolves_on_create(conn):
    src = notes.create(conn, "Allikas", body="# Allikas\n[Tulevik](/m/tulevik)")
    row = conn.execute("SELECT dst_slug, dst_id FROM links WHERE src_id = ?", (src["id"],)).fetchone()
    assert (row["dst_slug"], row["dst_id"]) == ("tulevik", None)
    dst = notes.create(conn, "Tulevik")
    assert conn.execute("SELECT dst_id FROM links WHERE src_id = ?", (src["id"],)).fetchone()[0] == dst["id"]


def test_soft_delete_unlinks(conn):
    dst = notes.create(conn, "Siht")
    src = notes.create(conn, "Allikas", body="# Allikas\n- [ ] asi [Siht](/m/siht)")
    notes.soft_delete(conn, dst["id"])
    assert notes.get(conn, dst["id"]) is None
    assert conn.execute("SELECT dst_id FROM links WHERE src_id = ?", (src["id"],)).fetchone()[0] is None
    notes.soft_delete(conn, src["id"])
    assert conn.execute("SELECT COUNT(*) FROM links WHERE src_id = ?", (src["id"],)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE note_id = ?", (src["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("body, title", [
    ("# Tere\ntekst", "Tere"),
    ("Sissejuhatus\n\n# Pealkiri", "Pealkiri"),
    ("## Alam\n# Pea", "Pea"),
    ("# **Tähtis** [asi](/m/asi) #", "Tähtis asi"),
    ("```\n# kood\n```\n# Päris", "Päris"),
    ("~~~md\n# kood\n~~~\ntekst", "tekst"),
    ("```\n# kood lõpuni", "Pealkirjata"),
    ("\n\n- [ ] **Osta** piima", "Osta piima"),
    ("> tsitaat _siin_", "tsitaat siin"),
    ("#silt ja tekst", "#silt ja tekst"),
    ("# \n\ntekst", "tekst"),
    ("# ", "Pealkirjata"),
    ("", "Pealkirjata"),
    ("   \n\n", "Pealkirjata"),
    ("# " + "a" * 300, "a" * 200),
])
def test_derive_title(body, title):
    assert notes.derive_title(body) == title


def test_on_saved_updates_title(conn):
    note = notes.create(conn, "Algne")
    conn.execute("UPDATE notes SET body = '```\n# ei\n```\nesimene rida' WHERE id = ?", (note["id"],))
    notes.on_saved(conn, note["id"])
    assert notes.get(conn, note["id"])["title"] == "esimene rida"


@pytest.mark.parametrize("given, stored", [
    ("kool/matemaatika", "kool/matemaatika"),
    (" /kool//matemaatika/ ", "kool/matemaatika"),
    ("kool\\füüsika", "kool/füüsika"),
    ("./a/./b", "a/b"),
    ("", ""),
    ("/", ""),
])
def test_move(conn, given, stored):
    note = notes.create(conn, "X")
    notes.move(conn, note["id"], given)
    assert notes.get(conn, note["id"])["folder"] == stored


@pytest.mark.parametrize("folder", ["..", "a/../b", "../etc"])
def test_move_rejects_parent(conn, folder):
    note = notes.create(conn, "X", folder="a")
    with pytest.raises(ValueError):
        notes.move(conn, note["id"], folder)
    assert notes.get(conn, note["id"])["folder"] == "a"


def test_tags_aliases_pinned(conn):
    note = notes.create(conn, "X")
    notes.set_tags(conn, note["id"], ["#kool", "kool", " Kool ", "", "töö"])
    notes.set_aliases(conn, note["id"], ["Mata", "mata", "  "])
    notes.set_pinned(conn, note["id"], True)
    row = notes.get(conn, note["id"])
    assert json.loads(row["tags_json"]) == ["kool", "töö"]
    assert json.loads(row["aliases_json"]) == ["Mata"]
    assert row["pinned"] == 1 and row["rev"] == 1


def test_tree(conn):
    notes.create(conn, "B", folder="kool")
    notes.create(conn, "a", folder="kool")
    notes.create(conn, "Juur")
    gone = notes.create(conn, "Kustutatud")
    notes.soft_delete(conn, gone["id"])
    rows = notes.tree(conn)
    assert [(r["folder"], r["title"]) for r in rows] == [("", "Juur"), ("kool", "a"), ("kool", "B")]
    assert set(rows[0]) == {"id", "slug", "title", "folder", "pinned", "is_daily", "updated_at"}
    assert rows[0]["pinned"] is False


def test_daily_get_or_create(conn):
    day = date(2026, 9, 24)
    note = notes.daily(conn, day)
    assert (note["slug"], note["title"], note["folder"], note["is_daily"], note["daily_date"]) == (
        "2026-09-24", "Neljapäev, 24. september 2026", "paevik", 1, "2026-09-24")
    assert notes.daily(conn, day)["id"] == note["id"]
    notes.soft_delete(conn, note["id"])
    again = notes.daily(conn, day)
    assert again["id"] != note["id"] and again["slug"] == "2026-09-24-2"


def test_write_body(conn):
    note = notes.create(conn, "X")
    assert notes.write_body(conn, note["id"], "# Y\n") == 2
    row = notes.get(conn, note["id"])
    assert (row["title"], row["rev"]) == ("Y", 2)


def test_write_body_busy(conn, monkeypatch):
    note = notes.create(conn, "X")
    monkeypatch.setattr(collab, "is_busy", lambda c, note_id: True)
    with pytest.raises(notes.NoteBusy):
        notes.write_body(conn, note["id"], "# Y\n")
    row = notes.get(conn, note["id"])
    assert (row["title"], row["rev"], conn.in_transaction) == ("X", 1, False)


def test_write_body_deleted(conn):
    note = notes.create(conn, "X")
    notes.soft_delete(conn, note["id"])
    with pytest.raises(LookupError):
        notes.write_body(conn, note["id"], "y")


def test_mask_code_keeps_offsets():
    body = "a `b` c\n```py\nx\n```\nd ``e ` f`` g"
    masked = notes.mask_code(body)
    assert len(masked) == len(body) and masked.count("\n") == body.count("\n")
    assert masked == "a     c\n     \n \n   \nd " + " " * len("``e ` f``") + " g"
