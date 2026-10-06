"""The trash (docs/MARKMED-PLAN.md 1-4, 18): delete, restore, delete for good, purge; .md download."""

from datetime import date, timedelta

import pytest

from app import notes, shares, tasks, util


def _new(owner, **fields):
    r = owner.post("/api/notes", json=fields)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _deleted_days_ago(conn, note_id, days):
    at = util.iso(util.now_utc() - timedelta(days=days))
    conn.execute("UPDATE notes SET deleted_at = ?, updated_at = ? WHERE id = ?", (at, at, note_id))


@pytest.mark.parametrize("method, path", [
    ("get", "/api/prugikast"),
    ("post", "/api/prugikast/x/taasta"),
    ("delete", "/api/prugikast/x"),
    ("get", "/api/notes/x/md"),
])
def test_anonymous_gets_401(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401


def test_delete_then_restore_brings_links_tasks_and_shares_back(owner, conn):
    target = _new(owner, title="Siht")
    src = _new(owner, title="Allikas", body="# Allikas\n\nVaata [Siht](/m/siht).\n")
    gone = _new(owner, title="Kaduv", body="# Kaduv\n\n- [ ] tee ära\n\n[Allikas](/m/allikas)\n")
    _, token = shares.create(conn, gone["id"], "view")
    assert owner.delete("/api/notes/kaduv").status_code == 204
    assert owner.delete("/api/notes/siht").status_code == 204
    assert owner.get("/api/notes/kaduv").status_code == 404
    assert {n["slug"] for n in owner.get("/api/prugikast").get_json()} == {"siht", "kaduv"}
    assert owner.get("/api/notes/allikas/links").get_json()["outgoing"] == [
        {"slug": "siht", "title": "Siht", "exists": False}]
    assert owner.get(f"/j/{token}").status_code == 404

    r = owner.post("/api/prugikast/kaduv/taasta")
    assert r.status_code == 200 and r.get_json()["slug"] == "kaduv"
    owner.post("/api/prugikast/siht/taasta")
    assert owner.get("/api/prugikast").get_json() == []
    assert owner.get("/api/notes/allikas/links").get_json()["outgoing"][0]["exists"] is True
    assert [b["slug"] for b in owner.get("/api/notes/allikas/links").get_json()["backlinks"]] == ["kaduv"]
    assert [t["text"] for t in owner.get("/api/tasks").get_json()] == ["tee ära"]
    assert owner.get(f"/j/{token}").status_code == 200
    assert src["slug"] == "allikas"


def test_restore_of_a_live_note_or_unknown_slug_is_404(owner):
    _new(owner, title="Elus")
    assert owner.post("/api/prugikast/elus/taasta").status_code == 404
    assert owner.post("/api/prugikast/pole/taasta").status_code == 404
    assert owner.delete("/api/prugikast/elus").status_code == 404


def test_a_daily_note_gets_its_day_back_unless_the_day_has_a_new_one(conn):
    day = date(2026, 10, 1)
    first = notes.daily(conn, day)
    notes.soft_delete(conn, first["id"])
    back = notes.restore(conn, first["id"])
    assert (back["is_daily"], back["daily_date"]) == (1, "2026-10-01")
    assert notes.daily(conn, day)["id"] == first["id"]

    notes.soft_delete(conn, first["id"])
    second = notes.daily(conn, day)
    back = notes.restore(conn, first["id"])
    assert (back["is_daily"], back["daily_date"]) == (0, None)
    assert notes.daily(conn, day)["id"] == second["id"]


def test_purge_takes_only_old_notes_git_no_longer_has(conn):
    old = notes.create(conn, "Vana")
    in_git = notes.create(conn, "Gitis")
    recent = notes.create(conn, "Värske")
    tasks_note = notes.create(conn, "Ülesanded", body="# Ülesanded\n\n- [ ] üks\n")
    for n in (old, in_git, recent, tasks_note):
        notes.soft_delete(conn, n["id"])
    for n in (old, in_git, tasks_note):
        _deleted_days_ago(conn, n["id"], notes.TRASH_DAYS + 1)
    _deleted_days_ago(conn, recent["id"], notes.TRASH_DAYS - 1)
    conn.execute("UPDATE notes SET file_path = 'notes/gitis.md' WHERE id = ?", (in_git["id"],))
    assert [n["slug"] for n in notes.trash(conn)] == ["varske"]
    assert notes.purge(conn) == 2
    left = {r["slug"] for r in conn.execute("SELECT slug FROM notes")}
    assert left == {"gitis", "varske"}


def test_delete_for_good_leaves_the_trash_now_and_the_table_after_the_wait(owner, conn):
    n = _new(owner, title="Ära")
    owner.delete("/api/notes/ara")
    assert owner.delete("/api/prugikast/ara").status_code == 204
    assert owner.get("/api/prugikast").get_json() == []
    assert notes.purge(conn) == 0  # an export that began before the delete may still be writing
    conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?",
                 (util.iso(util.now_utc() - notes.PURGE_WAIT - timedelta(seconds=1)), n["id"]))
    assert notes.purge(conn) == 1
    assert conn.execute("SELECT 1 FROM notes WHERE id = ?", (n["id"],)).fetchone() is None


def test_a_restored_note_keeps_its_cells_output_files(conn):
    # The output clean-ups count a note in the trash as still linking (tests/failid, tests/arvuta).
    n = notes.create(conn, "Väljund", body="# Väljund\n\n[a.csv](/failid/x.csv)\n")
    notes.soft_delete(conn, n["id"])
    assert conn.execute("SELECT 1 FROM notes WHERE instr(body, '/failid/x.csv') > 0").fetchone()


def test_download_md(owner):
    _new(owner, title='Õpe: a/b "c"', body='# Õpe: a/b "c"\n\n[x](/m/y) ![](/pildid/z.png)\n')
    r = owner.get("/api/notes/ope-a-b-c/md")
    assert r.status_code == 200
    assert r.data.decode() == '# Õpe: a/b "c"\n\n[x](/m/y) ![](/pildid/z.png)\n'
    assert r.headers["Content-Type"].startswith("text/markdown")
    disposition = r.headers["Content-Disposition"]
    assert disposition.startswith("attachment;") and "filename*=UTF-8''%C3%95pe%20a%20b%20c.md" in disposition
