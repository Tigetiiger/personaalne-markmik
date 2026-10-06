"""Search and title suggestions (app/search.py)."""

from datetime import date

import pytest

from app import notes, search
from app.search import HL_END, HL_START


@pytest.fixture
def corpus(conn):
    notes.create(conn, "Töö plaan", body="# Töö plaan\nKodune töö matemaatikas ja füüsikas.")
    notes.create(conn, "Poenimekiri", body="# Poenimekiri\npiim, leib, NEAR the \"end\" -5*")
    gone = notes.create(conn, "Kustutatud töö", body="# Kustutatud töö\ntöö")
    notes.soft_delete(conn, gone["id"])
    return conn


def _slugs(conn, q):
    return [r["slug"] for r in search.search(conn, q)]


@pytest.mark.parametrize("q, slugs", [
    ("töö", ["too-plaan"]),
    ("too", ["too-plaan"]),              # diacritics fold
    ("TÖÖ", ["too-plaan"]),
    ("matem", ["too-plaan"]),            # prefix
    ("fuusika", ["too-plaan"]),
    ("kodune matem", ["too-plaan"]),     # every word
    ("kodune leib", []),
    ("leib", ["poenimekiri"]),
    ("", []),
    ("   ", []),
])
def test_search(corpus, q, slugs):
    assert _slugs(corpus, q) == slugs


@pytest.mark.parametrize("q", ['"', '""', "*", "NEAR", "NEAR(a b)", "-", "-5*", "a OR", "AND", "NOT töö",
                               "title:töö", "^töö", "(töö", "töö)", "{töö}", "'", "\\", "\x00"])
def test_search_hostile_input(corpus, q):
    search.search(corpus, q)  # never an FTS syntax error


@pytest.mark.parametrize("q, slugs", [
    ("NEAR", ["poenimekiri"]),
    ('"end"', ["poenimekiri"]),
    ("-5*", ["poenimekiri"]),
    ("NOT töö", []),                    # NOT is a word to find, not an operator
])
def test_search_operators_are_words(corpus, q, slugs):
    assert _slugs(corpus, q) == slugs


def test_search_snippet_markers(corpus):
    [hit] = search.search(corpus, "matemaatikas")
    assert hit["title"] == "Töö plaan"
    assert f"{HL_START}matemaatikas{HL_END}" in hit["snippet"]


def test_title_ranks_first(conn):
    notes.create(conn, "Muu", body="# Muu\nkala " * 3)
    notes.create(conn, "Kala", body="# Kala\nmidagi")
    assert _slugs(conn, "kala") == ["kala", "muu"]


def test_limit(conn):
    for i in range(5):
        notes.create(conn, f"Kala {i}")
    assert len(search.search(conn, "kala", limit=3)) == 3


def test_suggest_ranking(conn):
    for title in ["Kontrolltöö: mata", "Mata tund", "Kõrgem matemaatika", "Automatiseerimine", "Füüsika", "Matemaatika"]:
        notes.create(conn, title)
    assert [r["title"] for r in search.suggest(conn, "mat")] == [
        "Matemaatika", "Mata tund",            # title prefix, newest first
        "Kõrgem matemaatika", "Kontrolltöö: mata",  # a word starts with it
        "Automatiseerimine",                    # substring
    ]
    assert [r["title"] for r in search.suggest(conn, "fuu")] == ["Füüsika"]
    assert [r["title"] for r in search.suggest(conn, "KONTROLLTOO")] == ["Kontrolltöö: mata"]
    assert set(search.suggest(conn, "fuu")[0]) == {"slug", "title", "folder"}


def test_suggest_empty_is_recent(conn):
    for i in range(12):
        notes.create(conn, f"N{i}")
    gone = notes.create(conn, "Kustutatud")
    notes.soft_delete(conn, gone["id"])
    conn.execute("UPDATE notes SET updated_at = '2030-01-01T00:00:00Z' WHERE slug = 'n3'")
    result = search.suggest(conn, "")
    assert len(result) == 10 and result[0]["slug"] == "n3"
    assert "kustutatud" not in [r["slug"] for r in result]
    assert len(search.suggest(conn, "n", limit=3)) == 3


@pytest.fixture
def tagged(conn):
    a = notes.create(conn, "Algebra", folder="kool/mata", body="# Algebra\nvõrrandid ja funktsioonid")
    b = notes.create(conn, "Geomeetria", folder="kool", body="# Geomeetria\nkolmnurgad ja võrrandid")
    c = notes.create(conn, "Retsept", folder="koolitus", body="# Retsept\nvõrrandid köögis")
    notes.set_tags(conn, a["id"], ["Kool", "eksam"])
    notes.set_tags(conn, b["id"], ["kool"])
    notes.set_hidden(conn, c["id"], True)
    return conn


@pytest.mark.parametrize("q, slugs", [
    ("võrrandid #kool", {"algebra", "geomeetria"}),
    ("võrrandid #KOOL -#eksam", {"geomeetria"}),
    ("võrrandid kaust:kool", {"algebra", "geomeetria"}),   # and below, not "koolitus"
    ("võrrandid kaust:kool/mata/", {"algebra"}),
    ("#eksam", {"algebra"}),                               # filters alone
    ("kaust:koolitus", {"retsept"}),
    ("-#kool", {"retsept"}),
    ("#", set()),
    ("a#kool", set()),
])
def test_search_filters(tagged, q, slugs):
    assert set(_slugs(tagged, q)) == slugs


def test_search_hits_say_folder_and_hidden(tagged):
    [hit] = search.search(tagged, "köögis")
    assert (hit["folder"], hit["hidden"]) == ("koolitus", True)
    assert search.search(tagged, "kaust:kool/mata") == [
        {"slug": "algebra", "title": "Algebra", "snippet": "", "folder": "kool/mata", "hidden": False}]


def test_similar_notes_share_words_not_stop_words(conn):
    a = notes.create(conn, "Fotosüntees", body="# Fotosüntees\nklorofüll klorofüll valgus lehed ning kuid")
    notes.create(conn, "Taimed", body="# Taimed\nlehed ja klorofüll")
    notes.create(conn, "Sõnad", body="# Sõnad\nning kuid siis")
    notes.daily(conn, date(2026, 10, 1))
    conn.execute("UPDATE notes SET body = body || '\nklorofüll' WHERE is_daily = 1")
    assert [r["slug"] for r in search.similar(conn, notes.get(conn, a["id"]))] == ["taimed"]
    assert search.similar(conn, notes.create(conn, "", body="# \n")) == []
