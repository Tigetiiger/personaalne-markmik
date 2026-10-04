"""Notes JSON API (app/views/notes_api.py), docs/CONTRACTS.md §5 rows B."""

from datetime import date

import pytest

from app import collab, util

NOTE_KEYS = {"id", "slug", "title", "body", "folder", "tags", "aliases", "pinned", "hidden", "is_daily", "daily_date", "rev",
             "updated_at"}
BUSY = {"error": "Märget muudetakse teises aknas."}


def _new(owner, **fields):
    r = owner.post("/api/notes", json=fields)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


@pytest.mark.parametrize("method, path", [
    ("get", "/api/notes/tree"),
    ("get", "/api/notes/suggest?q=a"),
    ("get", "/api/notes/x"),
    ("post", "/api/notes"),
    ("patch", "/api/notes/x"),
    ("delete", "/api/notes/x"),
    ("get", "/api/notes/x/links"),
    ("get", "/api/notes/x/mentions"),
    ("post", "/api/notes/x/link-mention"),
    ("get", "/api/daily/2026-09-24"),
    ("get", "/api/tasks"),
    ("post", "/api/tasks/1/toggle"),
    ("get", "/api/search?q=a"),
])
def test_anonymous_gets_401(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401


def test_create_get_patch_delete(owner):
    note = _new(owner, title="Matemaatika", folder="kool")
    assert set(note) == NOTE_KEYS
    assert (note["slug"], note["title"], note["body"], note["folder"], note["tags"], note["pinned"]) == (
        "matemaatika", "Matemaatika", "# Matemaatika\n", "kool", [], False)
    assert owner.get("/api/notes/matemaatika").get_json() == note

    r = owner.patch("/api/notes/matemaatika", json={"folder": "kool/reaal/", "tags": ["#kool"], "aliases": ["Mata"],
                                                     "pinned": True})
    got = r.get_json()
    assert (got["folder"], got["tags"], got["aliases"], got["pinned"], got["rev"]) == (
        "kool/reaal", ["kool"], ["Mata"], True, 1)

    assert owner.delete("/api/notes/matemaatika").status_code == 204
    assert owner.get("/api/notes/matemaatika").status_code == 404
    assert owner.get("/api/notes/tree").get_json() == []


def test_create_empty_and_with_body(owner):
    assert _new(owner)["title"] == "Pealkirjata"
    note = _new(owner, body="# Plaan\n- [ ] asi")
    assert (note["slug"], note["title"]) == ("marge-2", "Plaan")


@pytest.mark.parametrize("payload", [None, [], {"title": 5}, {"folder": ["a"]}, {"body": 3}, {"folder": "../x"}])
def test_create_bad_input(owner, payload):
    r = owner.post("/api/notes", json=payload) if payload is not None else owner.post("/api/notes", data="x")
    assert r.status_code == 400 and r.get_json()["error"]


@pytest.mark.parametrize("payload", [{"tags": "kool"}, {"tags": [1]}, {"pinned": 1}, {"folder": None},
                                     {"folder": "a/.."}, {"tags": ["x"], "folder": ".."}])
def test_patch_bad_input(owner, payload):
    _new(owner, title="X")
    r = owner.patch("/api/notes/x", json=payload)
    assert r.status_code == 400 and r.get_json()["error"]
    assert owner.get("/api/notes/x").get_json()["tags"] == []  # nothing half-applied


def test_unknown_note_404(owner):
    for r in (owner.get("/api/notes/puudub"), owner.patch("/api/notes/puudub", json={}),
              owner.delete("/api/notes/puudub"), owner.get("/api/notes/puudub/links")):
        assert r.status_code == 404 and r.get_json() == {"error": "Märget ei leitud."}


def test_tree_and_suggest(owner):
    _new(owner, title="Matemaatika", folder="kool")
    _new(owner, title="Füüsika")
    tree = owner.get("/api/notes/tree").get_json()
    assert [(n["folder"], n["title"]) for n in tree] == [("", "Füüsika"), ("kool", "Matemaatika")]
    assert owner.get("/api/notes/suggest?q=fuu").get_json() == [{"slug": "fuusika", "title": "Füüsika", "folder": ""}]
    assert len(owner.get("/api/notes/suggest").get_json()) == 2


def test_links_and_mentions(owner):
    _new(owner, title="Siht")
    _new(owner, title="Viitaja", body="# Viitaja\n[Siht](/m/siht) ja [Puudub](/m/puudub)")
    _new(owner, title="Mainija", body="# Mainija\nräägime Siht teemast")
    got = owner.get("/api/notes/viitaja/links").get_json()
    assert got["outgoing"] == [{"slug": "siht", "title": "Siht", "exists": True},
                               {"slug": "puudub", "title": "Puudub", "exists": False}]
    got = owner.get("/api/notes/siht/links").get_json()
    assert got["backlinks"] == [{"slug": "viitaja", "title": "Viitaja", "snippet": "Siht ja Puudub"}]
    assert owner.get("/api/notes/siht/mentions").get_json() == [
        {"slug": "mainija", "title": "Mainija", "snippet": "räägime Siht teemast"}]

    assert owner.post("/api/notes/siht/link-mention", json={"src_slug": "mainija"}).status_code == 204
    assert owner.get("/api/notes/mainija").get_json()["body"] == "# Mainija\nräägime [Siht](/m/siht) teemast"
    assert owner.get("/api/notes/siht/mentions").get_json() == []
    r = owner.post("/api/notes/siht/link-mention", json={"src_slug": "mainija"})
    assert r.status_code == 404 and r.get_json() == {"error": "Mainimist ei leitud."}
    assert owner.post("/api/notes/siht/link-mention", json={"src_slug": "puudub"}).status_code == 404
    assert owner.post("/api/notes/siht/link-mention", json={}).status_code == 404


def test_daily(owner):
    got = owner.get("/api/daily/2026-09-24").get_json()
    assert (got["slug"], got["title"], got["is_daily"], got["daily_date"]) == (
        "2026-09-24", "Neljapäev, 24. september 2026", True, "2026-09-24")
    assert owner.get("/api/daily/2026-09-24").get_json()["id"] == got["id"]
    r = owner.get("/api/daily/eile")
    assert r.status_code == 400 and r.get_json() == {"error": "Vigane kuupäev."}


def test_tasks_list_and_toggle(owner, monkeypatch):
    monkeypatch.setattr(util, "local_today", lambda: date(2026, 9, 24))
    _new(owner, title="Kool", body="# Kool\n- [ ] õpi @reede 14.15\n- [x] tehtud @3.10\n- [ ] kunagi")
    tasks = owner.get("/api/tasks").get_json()
    assert [(t["note_slug"], t["note_title"], t["text"], t["done"], t["due_date"], t["due_time"]) for t in tasks] == [
        ("kool", "Kool", "õpi", False, "2026-09-25", "14:15"),
        ("kool", "Kool", "tehtud", True, "2026-10-03", None),
        ("kool", "Kool", "kunagi", False, None, None),
    ]
    assert [t["text"] for t in owner.get("/api/tasks?from=2026-09-26&to=2026-10-31").get_json()] == ["tehtud"]
    assert [t["text"] for t in owner.get("/api/tasks?done=0").get_json()] == ["õpi", "kunagi"]
    assert [t["text"] for t in owner.get("/api/tasks?done=1").get_json()] == ["tehtud"]
    assert owner.get("/api/tasks?from=eile").status_code == 400

    assert owner.post(f"/api/tasks/{tasks[0]['id']}/toggle").status_code == 204
    assert owner.get("/api/notes/kool").get_json()["body"].startswith("# Kool\n- [x] õpi @reede 14.15\n")
    assert owner.get("/api/tasks?done=1").get_json()[0]["id"] == tasks[0]["id"]
    r = owner.post("/api/tasks/9999/toggle")
    assert r.status_code == 404 and r.get_json()["error"]


def test_busy_is_409(owner, monkeypatch):
    _new(owner, title="Siht")
    _new(owner, title="Kool", body="# Kool\n- [ ] õpi\nSiht")
    [task] = owner.get("/api/tasks").get_json()
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    r = owner.post(f"/api/tasks/{task['id']}/toggle")
    assert (r.status_code, r.get_json()) == (409, BUSY)
    r = owner.post("/api/notes/siht/link-mention", json={"src_slug": "kool"})
    assert (r.status_code, r.get_json()) == (409, BUSY)
    assert owner.get("/api/notes/kool").get_json()["body"] == "# Kool\n- [ ] õpi\nSiht"


def test_search_api(owner):
    _new(owner, title="Töö plaan", body="# Töö plaan\nkodune töö")
    got = owner.get("/api/search?q=too").get_json()
    assert [(g["slug"], g["title"]) for g in got] == [("too-plaan", "Töö plaan")]
    assert "\x02" in got[0]["snippet"]
    assert owner.get('/api/search?q="NEAR*').get_json() == []
    assert owner.get("/api/search").get_json() == []
