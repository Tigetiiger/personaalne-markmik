"""The nightly clean-up of files cells wrote, and the nightly copy into backups/failid."""

from app import backup, failid, jobs, notes

OLD = "2020-01-01T00:00:00Z"


def test_clean_removes_only_old_unlinked_files_cells_wrote(app, conn, tmp_path):
    with app.app_context():
        linked = failid.store(conn, b"a\n1\n", "a.csv", failid.CODE)
        gone = failid.store(conn, b"a\n2\n", "b.csv", failid.CODE)
        in_deleted = failid.store(conn, b"a\n3\n", "c.csv", failid.CODE)
        fresh = failid.store(conn, b"a\n4\n", "d.csv", failid.CODE)
        uploaded = failid.store(conn, b"a\n5\n", "e.csv", failid.OWNER)
        adopted = failid.store(conn, b"a\n6\n", "f.csv", failid.CODE)
        assert failid.store(conn, b"a\n6\n", "minu.csv", failid.OWNER) == adopted
        n = notes.create(conn, "Väljund", body=f"📎 [a.csv](/failid/{linked})")
        failid.allow(conn, n["id"], [linked, gone])
    deleted = notes.create(conn, "Prügikastis", body=f"📎 [c.csv](/failid/{in_deleted})")
    notes.soft_delete(conn, deleted["id"])
    conn.execute("UPDATE files SET created_at = ? WHERE name != ?", (OLD, fresh))
    failid.clean(conn)
    # A note in the trash still links its file: a restore must find it.
    left = {r["name"] for r in conn.execute("SELECT name FROM files")}
    assert left == {linked, fresh, uploaded, adopted, in_deleted}
    notes.forget(conn, deleted["id"])
    conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (OLD, deleted["id"]))
    notes.purge(conn)
    failid.clean(conn)
    left = {r["name"] for r in conn.execute("SELECT name FROM files")}
    assert left == {linked, fresh, uploaded, adopted}
    on_disk = {p.name for p in (tmp_path / "failid").iterdir()}
    assert on_disk == left
    assert [r["name"] for r in conn.execute("SELECT name FROM note_files")] == [linked]


def test_clean_is_a_registered_nightly_job():
    assert "app.failid" in jobs.JOB_MODULES
    job = jobs.REGISTRY["failid-koristus"]
    assert job.fn is failid.clean and job.at


def test_backup_copies_files_not_yet_copied(app, conn, tmp_path):
    with app.app_context():
        first = failid.store(conn, b"a\n1\n", "a.csv", failid.OWNER)
    (tmp_path / "failid" / "vale.tmp").write_bytes(b"pooleli")
    backup.backup(conn)
    copies = tmp_path / "backups" / "failid"
    assert {p.name for p in copies.iterdir()} == {first}
    assert (copies / first).read_bytes() == b"a\n1\n"
    # A name already there is the same bytes: not copied again. A file deleted since stays.
    (copies / first).write_bytes(b"merk")
    with app.app_context():
        second = failid.store(conn, b"a\n2\n", "b.csv", failid.OWNER)
    (tmp_path / "failid" / first).unlink()
    backup.backup(conn)
    assert {p.name for p in copies.iterdir()} == {first, second}
    assert (copies / first).read_bytes() == b"merk" and (copies / second).read_bytes() == b"a\n2\n"


def test_backup_without_any_files(conn, tmp_path):
    backup.backup(conn)
    assert not (tmp_path / "backups" / "failid").exists()
