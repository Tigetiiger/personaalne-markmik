"""Hidden notes: the frontmatter key `peidetud`, the tree and tag lists, PATCH, and the git file."""

import json

from app import linkfmt, notes


def test_hide_and_show_through_patch(owner, conn):
    n = notes.create(conn, "Salajane plaan", folder="kool")
    assert owner.get("/api/notes/salajane-plaan").json["hidden"] is False
    r = owner.patch("/api/notes/salajane-plaan", json={"hidden": True})
    assert r.status_code == 200 and r.json["hidden"] is True
    assert json.loads(notes.get(conn, n["id"])["frontmatter_extra"]) == {"peidetud": True}
    [row] = [t for t in owner.get("/api/notes/tree").json if t["slug"] == "salajane-plaan"]
    assert row["hidden"] is True and "frontmatter_extra" not in row
    assert owner.patch("/api/notes/salajane-plaan", json={"hidden": "jah"}).status_code == 400
    owner.patch("/api/notes/salajane-plaan", json={"hidden": False})
    assert json.loads(notes.get(conn, n["id"])["frontmatter_extra"]) == {}


def test_other_frontmatter_keys_stay(conn):
    n = notes.create(conn, "Mall", folder="Mallid")
    conn.execute("UPDATE notes SET frontmatter_extra = ? WHERE id = ?", (json.dumps({"mall_kaust": "x"}), n["id"]))
    notes.set_hidden(conn, n["id"], True)
    notes.set_hidden(conn, n["id"], False)
    assert json.loads(notes.get(conn, n["id"])["frontmatter_extra"]) == {"mall_kaust": "x"}


def test_tags_say_which_notes_are_hidden(owner, conn):
    a = notes.create(conn, "Nähtav")
    b = notes.create(conn, "Peidus")
    notes.set_tags(conn, a["id"], ["robot"])
    notes.set_tags(conn, b["id"], ["robot"])
    notes.set_hidden(conn, b["id"], True)
    [robot] = [t for t in owner.get("/api/tags").json if t["tag"] == "robot"]
    assert [(n["title"], n["hidden"]) for n in robot["notes"]] == [("Nähtav", False), ("Peidus", True)]


def test_the_git_file_carries_the_mark_and_hiding_is_a_change_to_export(conn):
    n = notes.create(conn, "Peidus")
    before = notes.get(conn, n["id"])["updated_at"]
    conn.execute("UPDATE notes SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (n["id"],))
    notes.set_hidden(conn, n["id"], True)
    note = notes.get(conn, n["id"])
    assert note["updated_at"] > "2020-01-01T00:00:00Z" and before
    assert "\npeidetud: true\n" in linkfmt.to_file(note, note["body"])
