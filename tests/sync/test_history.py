"""A note's history from the brain repo (docs/MARKMED-PLAN.md 17)."""

from datetime import timedelta

from app import gitsync, notes, util


def export(conn):
    conn.execute("UPDATE notes SET updated_at = ?", (util.iso(util.now_utc() - timedelta(minutes=5)),))
    return gitsync.export(conn)


def test_history_follows_moves_and_gives_old_bodies_back(owner, conn, laptop):
    n = notes.create(conn, "Ajalugu", body="# Ajalugu\n\nesimene [Muu](/m/muu)\n")
    assert owner.get("/api/notes/ajalugu/ajalugu").get_json() == {"versions": []}  # not synced yet
    export(conn)
    notes.write_body(conn, n["id"], "# Ajalugu\n\nteine\n")
    export(conn)
    notes.move(conn, n["id"], "kool")
    export(conn)
    laptop.pull()
    laptop.write("notes/kool/ajalugu.md", laptop.read("notes/kool/ajalugu.md").replace("teine", "sülearvutist"))
    laptop.push("laptop edit")
    gitsync.pull(conn)

    versions = owner.get("/api/notes/ajalugu/ajalugu").get_json()["versions"]
    assert [v["message"] for v in versions] == ["laptop edit", "markmik: 1 note", "markmik: 1 note", "markmik: 1 note"]
    assert all(v["at"].endswith("Z") for v in versions)
    bodies = [owner.get(f"/api/notes/ajalugu/ajalugu/{v['sha']}").get_json()["body"] for v in versions]
    assert bodies == ["# Ajalugu\n\nsülearvutist\n", "# Ajalugu\n\nteine\n", "# Ajalugu\n\nteine\n",
                      "# Ajalugu\n\nesimene [Muu](/m/muu)\n"]


def test_unknown_or_bad_versions_are_404(owner, conn, laptop):
    notes.create(conn, "Üks")
    export(conn)
    assert owner.get("/api/notes/uks/ajalugu/" + "0" * 40).status_code == 404
    assert owner.get("/api/notes/uks/ajalugu/HEAD").status_code == 404
    assert owner.get("/api/notes/pole/ajalugu").status_code == 404


def test_history_without_a_connected_repo(owner, conn):
    n = notes.create(conn, "Lahti")
    conn.execute("UPDATE notes SET file_path = 'notes/lahti.md' WHERE id = ?", (n["id"],))
    r = owner.get("/api/notes/lahti/ajalugu")
    assert r.status_code == 409
