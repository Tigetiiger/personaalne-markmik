"""Links (app/links.py): parsing, outgoing, backlinks, unlinked mentions, link_mention."""

import pytest

from app import collab, links, notes


@pytest.mark.parametrize("body, expected", [
    ("[Mata](/m/matemaatika)", [("matemaatika", "Mata")]),
    ("vt [siit](/m/a#pealkiri) ja [ka](/m/b \"vihje\")", [("a", "siit"), ("b", "ka")]),
    ("[a [b] c](/m/x)", [("x", "a [b] c")]),
    (r"[a \] b](/m/x)", [("x", "a ] b")]),
    ("[[Mata](/m/mata)]", [("mata", "Mata")]),
    ("[](/m/tuhi)", [("tuhi", "")]),
    ("![pilt](/m/x)", []),
    (r"\[pole](/m/x)", []),
    ("[väline](https://x.ee/m/x)", []),
    ("[suur](/m/Suur)", []),
    ("`[kood](/m/x)` ja [päris](/m/y)", [("y", "päris")]),
    ("```\n[kood](/m/x)\n```\n[päris](/m/y)", [("y", "päris")]),
    ("~~~\n[kood](/m/x)", []),
    ("[mitu\nrida](/m/x)", []),
])
def test_parse(body, expected):
    assert [(slug, anchor) for slug, anchor, _, _ in links.parse(body)] == expected


def test_parse_spans():
    body = "a [b](/m/c) d"
    [(_, _, start, end)] = links.parse(body)
    assert body[start:end] == "[b](/m/c)"


def test_outgoing(conn):
    notes.create(conn, "Olemas")
    src = notes.create(conn, "Allikas", body="# Allikas\n[Puudub](/m/puudub) [x](/m/olemas) [y](/m/olemas)")
    assert links.outgoing(conn, src) == [
        {"slug": "puudub", "title": "Puudub", "exists": False},
        {"slug": "olemas", "title": "Olemas", "exists": True},
    ]


def test_backlinks(conn):
    dst = notes.create(conn, "Siht")
    notes.create(conn, "Üks", body="# Üks\nTäna rääkisime teemast [Siht](/m/siht) pikalt.")
    notes.create(conn, "Kaks", body="# Kaks\n`[Siht](/m/siht)` on koodis")
    gone = notes.create(conn, "Kolm", body="# Kolm\n[Siht](/m/siht)")
    notes.soft_delete(conn, gone["id"])
    notes.write_body(conn, dst["id"], "# Siht\n[ise](/m/siht)")
    assert links.backlinks(conn, dst) == [
        {"slug": "uks", "title": "Üks", "snippet": "Täna rääkisime teemast Siht pikalt."}]


def test_backlink_snippet_is_cut():
    body = "x " * 100 + "[Siht](/m/siht)" + " y" * 100
    snippet = links._snippet(body, body.index("["), "Siht")
    assert snippet.startswith("…") and snippet.endswith("…") and "Siht" in snippet
    assert len(snippet) <= 2 * links.SNIPPET_WIDTH + len("Siht") + 2


@pytest.fixture
def mata(conn):
    note = notes.create(conn, "Matemaatika")
    notes.set_aliases(conn, note["id"], ["Mata tund"])
    return notes.get(conn, note["id"])


@pytest.mark.parametrize("body, found", [
    ("Homme on matemaatika kontrolltöö.", True),
    ("MATEMAATIKA!", True),
    ("Pärast mata  tundi", False),               # inflected: not the alias
    ("Pärast mata\ntund", True),                  # alias across a line break
    ("Kõrgem matemaatikaõpik", False),            # not a whole word
    ("`matemaatika` koodis", False),
    ("```\nmatemaatika\n```", False),
    ("[matemaatika](https://x.ee)", False),       # already link text
    ("https://x.ee/matemaatika", False),
    ("Täna kõik", False),
])
def test_unlinked_mentions(conn, mata, body, found):
    notes.create(conn, "Allikas", body="# Allikas\n" + body)
    result = links.unlinked_mentions(conn, mata)
    assert [r["slug"] for r in result] == (["allikas"] if found else [])


def test_unlinked_mentions_skip_linked_and_deleted(conn, mata):
    notes.create(conn, "Juba", body="# Juba\nmatemaatika ja [Mata](/m/matemaatika)")
    gone = notes.create(conn, "Kadunud", body="# Kadunud\nmatemaatika")
    notes.soft_delete(conn, gone["id"])
    notes.create(conn, "Uus", body="# Uus\nTäna oli matemaatika tund, väga pikk.")
    assert links.unlinked_mentions(conn, mata) == [
        {"slug": "uus", "title": "Uus", "snippet": "Täna oli matemaatika tund, väga pikk."}]


def test_unlinked_mentions_diacritics_must_match(conn):
    too = notes.create(conn, "Töö")
    notes.create(conn, "A", body="# A\ntoo siia")  # FTS folds "too" to "töö"; Python refuses it
    notes.create(conn, "B", body="# B\nkodune töö")
    assert [r["slug"] for r in links.unlinked_mentions(conn, too)] == ["b"]


def test_link_mention(conn, mata):
    src = notes.create(conn, "Allikas", body="# Allikas\n`matemaatika` ja Matemaatika [x] ning matemaatika")
    assert links.link_mention(conn, mata, src)
    row = notes.get(conn, src["id"])
    assert row["body"] == "# Allikas\n`matemaatika` ja [Matemaatika](/m/matemaatika) [x] ning matemaatika"
    assert row["rev"] == 2
    assert [b["slug"] for b in links.backlinks(conn, mata)] == ["allikas"]
    assert links.unlinked_mentions(conn, mata) == []
    assert not links.link_mention(conn, mata, row)  # already linked


def test_link_mention_escapes_brackets(conn):
    target = notes.create(conn, "Plaan [v2]")
    src = notes.create(conn, "Allikas", body="# Allikas\nvt Plaan [v2] ka")
    assert links.link_mention(conn, target, src)
    body = notes.get(conn, src["id"])["body"]
    assert body == "# Allikas\nvt [Plaan \\[v2\\]](/m/plaan-v2) ka"
    assert links.parse(body)[0][:2] == ("plaan-v2", "Plaan [v2]")


def test_link_mention_none(conn, mata):
    src = notes.create(conn, "Allikas", body="# Allikas\nmidagi muud")
    assert not links.link_mention(conn, mata, src)
    assert notes.get(conn, src["id"])["rev"] == 1


def test_link_mention_busy(conn, mata, monkeypatch):
    src = notes.create(conn, "Allikas", body="# Allikas\nmatemaatika")
    monkeypatch.setattr(collab, "is_busy", lambda c, note_id: True)
    with pytest.raises(notes.NoteBusy):
        links.link_mention(conn, mata, src)
    assert notes.get(conn, src["id"])["body"] == "# Allikas\nmatemaatika"
