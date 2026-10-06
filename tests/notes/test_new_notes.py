"""New notes (docs/MARKMED-PLAN.md 5-8): a blank one left goes, an automatic slug follows the title
once, old slugs still open the note, links follow the rename."""

from datetime import timedelta

from app import collab, links, notes, notes_tidy, shares, util

OP = [0, "text", {"es": [0, "A"]}]
OWNER = {"name": "omanik", "is_guest": False}


def _auto(conn, note_id):
    return conn.execute("SELECT 1 FROM note_auto_slug WHERE note_id = ?", (note_id,)).fetchone() is not None


def _write(conn, note_id, body):
    notes.write_body(conn, note_id, body)
    return notes.get(conn, note_id)


def _age(conn, note_id, **delta):
    conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (util.iso(util.now_utc() - timedelta(**delta)), note_id))


def test_only_a_note_made_without_a_title_is_marked(conn):
    blank = notes.create(conn)
    named = notes.create(conn, "Matemaatika")
    day = notes.daily(conn, util.local_today())
    assert blank["slug"] == "marge" and _auto(conn, blank["id"])
    assert not _auto(conn, named["id"]) and not _auto(conn, day["id"])


def test_a_blank_new_note_left_goes_for_good(conn):
    n = notes.create(conn)
    assert notes.settle(conn, n["id"]) is None
    assert conn.execute("SELECT 1 FROM notes WHERE id = ?", (n["id"],)).fetchone() is None


def test_a_blank_note_stays_after_typing_while_shared_or_when_not_new(conn):
    typed = notes.create(conn)
    s = collab.join(conn, typed["id"], "ka", OWNER)
    collab.sync(conn, s["session"], "ka", OWNER, 0, OP, "a1", 0)
    assert notes.settle(conn, typed["id"]) == "marge"
    shared = notes.create(conn)
    shares.create(conn, shared["id"], "view")
    assert notes.settle(conn, shared["id"]) == "marge-2"
    named = notes.create(conn, "Nimega", body="# \n")
    assert notes.settle(conn, named["id"]) == "nimega"
    # The sweep does not wait for the operations.
    assert notes.settle(conn, typed["id"], typed_ok=True) is None


def test_a_blank_note_git_has_goes_into_the_trash(conn):
    n = notes.create(conn)
    conn.execute("UPDATE notes SET file_path = 'notes/marge.md' WHERE id = ?", (n["id"],))
    assert notes.settle(conn, n["id"]) is None
    assert [t["slug"] for t in notes.trash(conn)] == ["marge"]


def test_the_slug_follows_the_first_title_once(owner, conn):
    n = notes.create(conn)
    _write(conn, n["id"], "# Eesti keel\n\ntekst\n")
    assert notes.settle(conn, n["id"]) == "eesti-keel"
    assert not _auto(conn, n["id"])
    _write(conn, n["id"], "# Kirjandus\n")
    assert notes.settle(conn, n["id"]) == "eesti-keel"
    assert notes.by_slug(conn, "marge")["id"] == n["id"]
    r = owner.get("/m/marge?ettepanek=3")
    assert r.status_code == 302 and r.headers["Location"].endswith("/m/eesti-keel?ettepanek=3")
    assert owner.get("/api/notes/marge").get_json()["slug"] == "eesti-keel"
    # An old slug is never handed out again.
    assert notes.create(conn)["slug"] != "marge"


def test_a_title_that_makes_the_same_slug_keeps_it(conn):
    notes.create(conn)
    n = notes.create(conn)
    _write(conn, n["id"], "# Märge\n")
    assert notes.settle(conn, n["id"]) == "marge-2"
    m = notes.create(conn)
    _write(conn, m["id"], "# Untitled!\n")
    assert notes.settle(conn, m["id"]) == "untitled"


def test_links_follow_the_rename_and_a_busy_note_catches_up_in_the_sweep(conn, monkeypatch):
    n = notes.create(conn)
    a = notes.create(conn, "Allikas", body="# Allikas\n\n[uus](/m/marge) ja [sama \\[x\\]](/m/marge#osa) `[k](/m/marge)`\n")
    b = notes.create(conn, "Hõivatud", body="# Hõivatud\n\n[uus](/m/marge)\n")
    _write(conn, n["id"], "# Füüsika\n")
    real_busy = collab.is_busy
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: note_id == b["id"] or real_busy(conn, note_id))
    assert notes.settle(conn, n["id"]) == "fuusika"
    assert notes.get(conn, a["id"])["body"] == (
        "# Allikas\n\n[uus](/m/fuusika) ja [sama \\[x\\]](/m/fuusika#osa) `[k](/m/marge)`\n")
    assert "/m/marge" in notes.get(conn, b["id"])["body"]
    assert {x["slug"] for x in links.backlinks(conn, notes.get(conn, n["id"]))} == {"hoivatud", "allikas"}
    monkeypatch.setattr(collab, "is_busy", real_busy)
    notes.tidy(conn)
    assert notes.get(conn, b["id"])["body"] == "# Hõivatud\n\n[uus](/m/fuusika)\n"


def test_the_sweep_waits_and_leaves_busy_notes_alone(conn, monkeypatch):
    young = notes.create(conn)
    old_blank = notes.create(conn)
    named = notes.create(conn)
    busy = notes.create(conn)
    _write(conn, named["id"], "# Ajalugu\n")
    _write(conn, busy["id"], "# Keemia\n")
    _age(conn, old_blank["id"], hours=2)
    _age(conn, named["id"], minutes=11)
    _age(conn, busy["id"], minutes=11)
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: note_id == busy["id"])
    notes.tidy(conn)
    left = {r["id"]: r["slug"] for r in conn.execute("SELECT id, slug FROM notes")}
    assert left == {young["id"]: young["slug"], named["id"]: "ajalugu", busy["id"]: busy["slug"]}


def test_lahkun_endpoint(owner):
    n = owner.post("/api/notes", json={}).get_json()
    assert owner.post(f"/api/notes/{n['slug']}/lahkun").get_json() == {"slug": None}
    assert owner.post("/api/notes/pole/lahkun").get_json() == {"slug": None}
    m = owner.post("/api/notes", json={}).get_json()
    assert owner.post(f"/api/notes/{m['slug']}/lahkun", json={}).get_json() == {"slug": None}


def test_the_one_off_tidy_for_notes_made_before(conn):
    named = notes.create(conn)
    blank = notes.create(conn)
    marge = notes.create(conn, "Märge")
    conn.execute("DELETE FROM note_auto_slug")
    _write(conn, named["id"], "# Gustav Adolfi Gümnaasiumi ajalugu\n")
    conn.execute("UPDATE notes SET file_path = 'notes/marge-2.md' WHERE id = ?", (blank["id"],))
    assert notes_tidy.run(conn) == [("marge", "gustav-adolfi-gumnaasiumi-ajalugu"), ("marge-2", None)]
    assert [t["slug"] for t in notes.trash(conn)] == ["marge-2"]
    assert notes.get(conn, marge["id"])["slug"] == "marge-3"
