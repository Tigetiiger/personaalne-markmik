"""Templates: notes in Mallid/, placeholders, the defaults and the endpoint."""

import json
from datetime import datetime

from app import mallid, notes, util

NOW = datetime(2026, 10, 3, 14, 5, tzinfo=util.TALLINN)


def test_fill():
    assert mallid.fill("{{kuupäev}} / {{ kuupäev_pikk }} / {{nädalapäev}} {{kell}} / {{muu}}", NOW) == \
        "2026-10-03 / 3. oktoober 2026 / laupäev 14:05 / {{muu}}"


def test_only_notes_in_mallid_are_templates(conn):
    t = notes.create(conn, "Mouser tellimus", folder="Mallid", body="# Mouser tellimus\n\nx")
    sub = notes.create(conn, "Alam", folder="mallid/kool")
    other = notes.create(conn, "Mallid tegelikult mitte", folder="kool/Mallid")
    assert mallid.is_template(t) and mallid.is_template(sub) and not mallid.is_template(other)


def test_defaults_and_make(conn):
    t = notes.create(conn, "Mouser tellimus", folder="Mallid", body="# Mouser tellimus\n\nKuupäev: {{kuupäev}}\n")
    conn.execute("UPDATE notes SET frontmatter_extra = ? WHERE id = ?", (json.dumps({"mall_kaust": "tellimused"}), t["id"]))
    t = notes.get(conn, t["id"])
    assert mallid.defaults(t, NOW) == {"title": "Mouser tellimus 2026-10-03", "folder": "tellimused"}
    n = mallid.make(conn, t, now=NOW)
    assert (n["title"], n["folder"]) == ("Mouser tellimus 2026-10-03", "tellimused")
    assert n["body"] == "# Mouser tellimus 2026-10-03\n\nKuupäev: 2026-10-03\n"
    named = mallid.make(conn, t, "Robot", "", now=NOW)
    assert (named["title"], named["folder"], named["body"].splitlines()[0]) == ("Robot", "", "# Robot")


def test_a_title_with_a_placeholder_is_not_dated_twice(conn):
    t = notes.create(conn, "x", folder="Mallid", body="# Päevik {{kuupäev}}\n")
    assert mallid.defaults(t, NOW)["title"] == "Päevik 2026-10-03"


def test_endpoints(owner, conn):
    notes.create(conn, "Mall", folder="Mallid", body="# Mall\n\n{{nädalapäev}}\n")
    notes.create(conn, "Tavaline")
    assert owner.get("/api/mallid/mall").json["folder"] == ""
    assert owner.get("/api/mallid/tavaline").status_code == 404
    r = owner.post("/api/notes/from-template", json={"template": "mall", "title": "Uus asi", "folder": "kool"})
    assert r.status_code == 201 and r.json["title"] == "Uus asi" and r.json["folder"] == "kool"
    assert owner.post("/api/notes/from-template", json={"template": "tavaline"}).status_code == 404
    assert owner.post("/api/notes/from-template", json={"template": "mall", "folder": "../x"}).status_code == 400
    assert owner.post("/api/notes/from-template", json={"template": "mall", "title": 5}).status_code == 400


def test_endpoints_are_the_owners(client):
    assert client.get("/api/mallid/mall").status_code in (302, 401, 403)
    assert client.post("/api/notes/from-template", json={"template": "mall"}).status_code in (302, 401, 403)
