"""Folder rename and link texts after a title change (docs/MARKMED-PLAN.md 10, 12)."""

import json

import pytest

from app import collab, notes


def _new(owner, **fields):
    r = owner.post("/api/notes", json=fields)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


@pytest.mark.parametrize("method, path", [
    ("post", "/api/kaustad/nimeta"),
    ("get", "/api/notes/x/vanad-lingid?pealkiri=a"),
    ("put", "/api/notes/x/body"),
])
def test_anonymous_gets_401(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401


def test_rename_folder_moves_subfolders_and_trash_and_templates(owner, conn):
    a = _new(owner, title="A", folder="kool")
    b = _new(owner, title="B", folder="kool/mata")
    c = _new(owner, title="C", folder="koolitus")
    d = _new(owner, title="D", folder="kool")
    owner.delete(f"/api/notes/{d['slug']}")
    t = _new(owner, title="Mall", folder="Mallid")
    conn.execute("UPDATE notes SET frontmatter_extra = ? WHERE id = ?", (json.dumps({"mall_kaust": "kool/mata"}), t["id"]))
    r = owner.post("/api/kaustad/nimeta", json={"from": "kool", "to": " gümnaasium/ "})
    assert r.get_json() == {"moved": 3}
    folder = lambda n: conn.execute("SELECT folder FROM notes WHERE id = ?", (n["id"],)).fetchone()[0]
    assert [folder(n) for n in (a, b, c, d)] == ["gümnaasium", "gümnaasium/mata", "koolitus", "gümnaasium"]
    assert json.loads(notes.get(conn, t["id"])["frontmatter_extra"]) == {"mall_kaust": "gümnaasium/mata"}


def test_rename_folder_merges_and_refuses_bad_names(owner):
    _new(owner, title="A", folder="vana")
    _new(owner, title="B", folder="uus")
    assert owner.post("/api/kaustad/nimeta", json={"from": "vana", "to": "uus"}).get_json() == {"moved": 1}
    for to, message in [("", "Kausta nimi ei saa olla tühi."), ("uus", "Nimi on sama."),
                        ("uus/all", "Kausta ei saa viia iseenda sisse."), ("../x", "Vigane kaust.")]:
        r = owner.post("/api/kaustad/nimeta", json={"from": "uus", "to": to})
        assert (r.status_code, r.get_json()) == (400, {"error": message})


def test_stale_link_texts_are_found_and_only_the_old_title_changes(owner, conn):
    _new(owner, title="Vana nimi")
    _new(owner, title="Viitaja", body="# Viitaja\n\n[Vana nimi](/m/vana-nimi), [vt siit](/m/vana-nimi), `[Vana nimi](/m/vana-nimi)`\n")
    _new(owner, title="Teine", body="# Teine\n\n[Vana nimi](/m/vana-nimi#osa)\n")
    _new(owner, title="Muu", body="# Muu\n\n[vt](/m/vana-nimi)\n")
    n = notes.by_slug(conn, "vana-nimi")
    notes.write_body(conn, n["id"], "# Uus [nimi]\n")
    found = owner.get("/api/notes/vana-nimi/vanad-lingid?pealkiri=Vana nimi").get_json()
    assert [f["slug"] for f in found] == ["teine", "viitaja"]
    assert found[1]["proposed"] == (
        "# Viitaja\n\n[Uus \\[nimi\\]](/m/vana-nimi), [vt siit](/m/vana-nimi), `[Vana nimi](/m/vana-nimi)`\n")
    assert found[0]["proposed"] == "# Teine\n\n[Uus \\[nimi\\]](/m/vana-nimi#osa)\n"
    assert owner.get("/api/notes/vana-nimi/vanad-lingid?pealkiri=").get_json() == []


def test_put_body_checks_the_rev_and_the_editor(owner, monkeypatch):
    n = _new(owner, title="Keha")
    r = owner.put("/api/notes/keha/body", json={"body": "# Keha\n\nuus\n", "rev": n["rev"]})
    assert r.status_code == 200 and r.get_json()["rev"] == n["rev"] + 1
    r = owner.put("/api/notes/keha/body", json={"body": "# Keha\n", "rev": n["rev"]})
    assert (r.status_code, r.get_json()["reason"]) == (409, "muudetud")
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    r = owner.put("/api/notes/keha/body", json={"body": "# Keha\n", "rev": n["rev"] + 1})
    assert (r.status_code, r.get_json()["reason"]) == (409, "lahti")
    assert owner.put("/api/notes/keha/body", json={"body": 1, "rev": 1}).status_code == 400
