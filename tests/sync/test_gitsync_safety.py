"""Regressions from the WP-H review (probes in the review): symlinks from the laptop, files that
break the import, server-side writes racing the import, paths git would not commit, renames and
git timeouts."""

import json
import os
import subprocess
from datetime import timedelta

import pytest

from app import collab, gitsync, linkfmt, notes, util


def age(conn, minutes=5):
    conn.execute("UPDATE notes SET updated_at = ?", (util.iso(util.now_utc() - timedelta(minutes=minutes)),))


def export(conn):
    age(conn)
    return gitsync.export(conn)


def copies(conn):
    return conn.execute("SELECT count(*) FROM notes WHERE title LIKE '%(konflikt)' AND deleted_at IS NULL").fetchone()[0]


# 1. Symlinks never lead outside the clone's notes/.

def test_committed_symlink_file_is_not_followed(conn, laptop, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("PRECIOUS\n")
    (laptop.path / "notes").mkdir()
    os.symlink(outside, laptop.path / "notes" / "evil.md")
    laptop.push()
    assert gitsync.pull(conn) == (0, 0)  # a symlink is not a note
    assert not (gitsync.data_dir() / "brain/notes/evil.md").is_symlink()
    notes.create(conn, "Evil")  # its file would be notes/evil.md
    export(conn)
    assert outside.read_text() == "PRECIOUS\n"


def test_committed_symlink_folder_is_not_followed(conn, laptop, tmp_path):
    outdir = tmp_path / "outdir"
    outdir.mkdir()
    (laptop.path / "notes").mkdir()
    os.symlink(outdir, laptop.path / "notes" / "kool")
    laptop.write("notes/keep.md", "# Keep\n")
    laptop.push()
    gitsync.pull(conn)
    notes.create(conn, "Salajane", folder="kool")
    export(conn)
    assert list(outdir.iterdir()) == [] and notes.by_slug(conn, "keep") is not None


def test_symlink_already_in_the_working_tree_is_refused(conn, brain, tmp_path, caplog):
    gitsync.pull(conn)
    outdir = tmp_path / "outdir"
    outdir.mkdir()
    (gitsync.data_dir() / "brain/notes").mkdir()
    os.symlink(outdir, gitsync.data_dir() / "brain/notes/kool")
    notes.create(conn, "Salajane", folder="kool")
    export(conn)
    assert list(outdir.iterdir()) == [] and "outside notes/" in caplog.text


# 2. One bad file never stops the others, and nothing multiplies.

@pytest.mark.parametrize("front, value", [("foo: !!set {a: null}", ["a"]), ("foo: !!timestamp 2026-01-01", "2026-01-01"),
                                          ("foo: !!binary aGVsbG8=", "aGVsbG8=")])
def test_yaml_tags_do_not_stop_the_sync(conn, laptop, front, value):
    notes.create(conn, "Tavaline")
    export(conn)
    laptop.pull()
    laptop.write("notes/uus.md", f"---\n{front}\n---\n\n# Uus\n")
    laptop.push()
    assert gitsync.pull(conn) == (1, 0)
    assert gitsync.pull(conn) == (0, 0)
    assert json.loads(notes.by_slug(conn, "uus")["frontmatter_extra"])["foo"] == value
    notes.create(conn, "Veel")
    assert export(conn) >= 1


def test_file_that_is_not_utf8_is_skipped_and_left_alone(conn, laptop, caplog):
    m = notes.create(conn, "Katki", body="# Katki\n\nalgne\n")
    export(conn)
    laptop.pull()
    (laptop.path / "notes/katki.md").write_bytes(b"# Katki\n\n\xff\xfe halb\n")
    laptop.write("notes/hea.md", "# Hea\n")
    laptop.push()
    notes.write_body(conn, m["id"], "# Katki\n\nrakendus\n")
    assert gitsync.pull(conn) == (1, 0)
    assert "notes/katki.md" in caplog.text and notes.by_slug(conn, "hea") is not None
    export(conn)  # must not overwrite the file it could not read
    laptop.pull()
    assert (laptop.path / "notes/katki.md").read_bytes() == b"# Katki\n\n\xff\xfe halb\n"


def _conflict_setup(conn, laptop):
    m = notes.create(conn, "Aaa", body="# Aaa\n\nalgne\n")
    export(conn)
    laptop.pull()
    notes.write_body(conn, m["id"], "# Aaa\n\nrakendus\n")
    laptop.write("notes/aaa.md", laptop.read("notes/aaa.md").replace("algne", "laptop"))
    return m


def test_conflict_copies_do_not_multiply(conn, laptop):
    _conflict_setup(conn, laptop)
    laptop.write("notes/zzz.md", "---\nx: !!set {a: null}\n---\n\n# Z\n")
    laptop.push()
    for _ in range(3):
        gitsync.pull(conn)
    assert copies(conn) == 1 and notes.by_slug(conn, "zzz") is not None


def test_sync_state_is_saved_with_the_note(conn, laptop, monkeypatch):
    _conflict_setup(conn, laptop)
    laptop.push()
    real, calls = gitsync._Import._mark, []

    def mark(self, *args):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")  # after the copy was made, before its state was saved
        return real(self, *args)
    monkeypatch.setattr(gitsync._Import, "_mark", mark)
    assert gitsync.pull(conn) == (0, 0)
    assert copies(conn) == 0  # rolled back together
    assert gitsync.pull(conn) == (0, 1)
    assert gitsync.pull(conn) == (0, 0) and copies(conn) == 1


# 3. A server-side write that lands during the import is never overwritten.

def test_task_tick_during_import_is_kept(conn, laptop, monkeypatch):
    m = notes.create(conn, "Kodutöö", body="# Kodutöö\n\n- [ ] ülesanne\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/kodutoo.md", laptop.read("notes/kodutoo.md").replace("algne", "laptop lisas"))
    laptop.push()
    real, fired = linkfmt.from_file, []

    def from_file(text, **kw):
        if not fired:  # a task toggle lands after the run started, before this note's transaction
            fired.append(1)
            notes.write_body(conn, m["id"], "# Kodutöö\n\n- [x] ülesanne\n\nalgne\n")
        return real(text, **kw)
    monkeypatch.setattr(linkfmt, "from_file", from_file)
    assert gitsync.pull(conn) == (0, 1)
    assert notes.get(conn, m["id"])["body"] == "# Kodutöö\n\n- [x] ülesanne\n\nalgne\n"
    assert "laptop lisas" in conn.execute("SELECT body FROM notes WHERE title LIKE '%(konflikt)'").fetchone()[0]


def test_write_during_import_stops_a_laptop_delete(conn, laptop, monkeypatch):
    x = notes.create(conn, "Kustutatav")
    notes.create(conn, "Muudetav")
    export(conn)
    laptop.pull()
    laptop.git("rm", "-q", "notes/kustutatav.md")
    laptop.write("notes/muudetav.md", laptop.read("notes/muudetav.md") + "\nlisa\n")
    laptop.push()
    real, fired = linkfmt.from_file, []

    def from_file(text, **kw):
        if not fired:
            fired.append(1)
            notes.write_body(conn, x["id"], "# Kustutatav\n\nveel vaja\n")
        return real(text, **kw)
    monkeypatch.setattr(linkfmt, "from_file", from_file)
    gitsync.pull(conn)
    assert notes.get(conn, x["id"])["body"] == "# Kustutatav\n\nveel vaja\n"


def test_export_waits_for_a_laptop_change_not_imported_yet(conn, laptop, monkeypatch):
    m = notes.create(conn, "Ootel", body="# Ootel\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/ootel.md", laptop.read("notes/ootel.md").replace("algne", "sülearvuti"))
    laptop.push()
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    gitsync.pull(conn)  # deferred: open in an editor
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: False)
    notes.set_tags(conn, m["id"], ["uus"])
    age(conn, minutes=3)  # not the same second as the first export's age()
    gitsync.export(conn)
    laptop.pull()
    assert "sülearvuti" in laptop.read("notes/ootel.md")
    gitsync.pull(conn)
    m = notes.get(conn, m["id"])
    assert "sülearvuti" in m["body"] and json.loads(m["tags_json"]) == ["uus"]


# 4. Every file the export writes reaches the commit, or the note is not marked.

def test_note_in_a_gitignored_folder_is_committed(conn, laptop):
    laptop.write(".gitignore", "pglite/\n.gbrain/\n")
    laptop.push()
    gitsync.pull(conn)
    a = notes.create(conn, "Ignoreeritud", folder="pglite")
    export(conn)
    assert gitsync.pull(conn) == (0, 0)
    assert notes.get(conn, a["id"]) is not None
    laptop.pull()
    assert laptop.exists("notes/pglite/ignoreeritud.md")


def test_note_in_a_folder_named_pildid_survives(conn, laptop):
    m = notes.create(conn, "Pildikaust", folder="_pildid")
    export(conn)
    assert gitsync.pull(conn) == (0, 0) and notes.get(conn, m["id"]) is not None


def test_note_is_marked_only_once_its_file_is_in_head(conn, brain, monkeypatch, caplog):
    m = notes.create(conn, "Jäi välja")
    real = gitsync.Repo.git
    monkeypatch.setattr(gitsync.Repo, "git", lambda self, *a, **kw: "" if "add" in a else real(self, *a, **kw))
    assert export(conn) == 1  # written, but git took nothing: no commit, and no error
    m = notes.get(conn, m["id"])
    assert (m["exported_rev"], m["file_path"]) == (0, None) and "did not reach the commit" in caplog.text
    monkeypatch.setattr(gitsync.Repo, "git", real)
    export(conn)
    assert notes.get(conn, m["id"])["file_path"] == "notes/jai-valja.md"


# 6. A rename on the laptop keeps the note.

def test_laptop_rename_keeps_the_note(conn, laptop):
    m = notes.create(conn, "Vana", body="# Vana\n\nsisu\n")
    notes.create(conn, "Viitaja", body="# Viitaja\n\n[Vana](/m/vana)\n")
    export(conn)
    laptop.pull()
    laptop.git("mv", "notes/vana.md", "notes/uus-nimi.md")
    laptop.push()
    gitsync.pull(conn)
    assert notes.get(conn, m["id"]) is not None
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2
    export(conn)
    laptop.pull()
    assert laptop.exists("notes/vana.md") and not laptop.exists("notes/uus-nimi.md")


# 7. A hung or missing git is a GitError (502 from sync now), not a crash.

@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("git", 120), FileNotFoundError("git")])
def test_git_timeout_or_missing_git_is_a_git_error(owner, conn, brain, monkeypatch, error):
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(gitsync.GitError):
        gitsync.pull(conn)
    r = owner.post("/api/sync/now")
    assert r.status_code == 502 and r.get_json()["error"].startswith("Sünkroonimine ebaõnnestus")


# Second review round (probes in test_probe5.py).

def test_link_to_a_deleted_note_survives_a_laptop_edit(conn, laptop):
    b = notes.create(conn, "Bee", body="# Bee\n\nx\n")
    a = notes.create(conn, "Aaa", body="# Aaa\n\nvt [Bee](/m/bee)\n")
    export(conn)
    laptop.pull()
    notes.soft_delete(conn, b["id"])
    export(conn)
    gitsync.pull(conn)
    laptop.pull()
    laptop.write("notes/aaa.md", laptop.read("notes/aaa.md") + "\nlaptop lisas\n")
    laptop.push()
    gitsync.pull(conn)
    assert notes.get(conn, a["id"])["body"] == "# Aaa\n\nvt [Bee](/m/bee)\n\nlaptop lisas\n"


def test_laptop_link_to_a_note_created_later_stays_a_link(conn, laptop):
    g = notes.create(conn, "Ggg", body="# Ggg\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/ggg.md", laptop.read("notes/ggg.md").replace("algne", "vt [[tulevane]]"))
    laptop.push()
    gitsync.pull(conn)
    assert notes.get(conn, g["id"])["body"] == "# Ggg\n\nvt [tulevane](/m/tulevane)\n"
    notes.create(conn, "Tulevane")
    notes.write_body(conn, g["id"], notes.get(conn, g["id"])["body"] + "\nlisa\n")
    age(conn, minutes=3)
    gitsync.export(conn)
    laptop.pull()
    assert "vt [[tulevane]]\n\nlisa" in laptop.read("notes/ggg.md")


@pytest.mark.parametrize("folder", [".git", "kool/.GIT", "a/git~1"])
def test_folder_git_will_not_store_blocks_only_that_note(conn, laptop, folder):
    notes.create(conn, "Hea")
    bad = notes.create(conn, "Halb", folder=folder)
    assert export(conn) == 1  # Hea only; Halb is never counted as pushed
    laptop.pull()
    assert laptop.exists("notes/hea.md")
    assert notes.get(conn, bad["id"])["file_path"] is None and gitsync.problems(conn) == ["Halb"]
    notes.create(conn, "Veel")
    age(conn, minutes=3)
    assert gitsync.export(conn) == 1


def test_one_note_git_refuses_to_add_does_not_block_the_rest(conn, laptop, monkeypatch):
    notes.create(conn, "Hea")
    notes.create(conn, "Halb")
    real = gitsync.Repo.git

    def git(self, *args, **kw):
        if "add" in args and "notes/halb.md" in args:
            raise gitsync.GitError("git add: refused")
        return real(self, *args, **kw)
    monkeypatch.setattr(gitsync.Repo, "git", git)
    assert export(conn) == 1
    laptop.pull()
    assert laptop.exists("notes/hea.md") and not laptop.exists("notes/halb.md")
    assert gitsync.problems(conn) == ["Halb"]


def test_laptop_edit_of_a_note_deleted_here_is_not_deleted_when_it_cannot_be_read(conn, laptop):
    n = notes.create(conn, "Bbb", body="# Bbb\n\nalgne\n")
    export(conn)
    laptop.pull()
    notes.soft_delete(conn, n["id"])
    (laptop.path / "notes/bbb.md").write_bytes(b"# Bbb\n\nlaptop edit \xff\n")
    laptop.push()
    gitsync.pull(conn)
    export(conn)
    laptop.pull()
    assert (laptop.path / "notes/bbb.md").read_bytes() == b"# Bbb\n\nlaptop edit \xff\n"
    assert gitsync.problems(conn) == ["Bbb"]


def test_new_note_does_not_overwrite_an_unimported_laptop_file(conn, laptop):
    notes.create(conn, "Tavaline")
    export(conn)
    laptop.pull()
    (laptop.path / "notes/uus.md").write_bytes(b"# Uus\n\nlaptop tekst \xff\n")
    laptop.push()
    gitsync.pull(conn)
    notes.create(conn, "Uus", body="# Uus\n\nserver\n")
    export(conn)
    laptop.pull()
    assert (laptop.path / "notes/uus.md").read_bytes() == b"# Uus\n\nlaptop tekst \xff\n"
    assert set(gitsync.problems(conn)) == {"Uus", "notes/uus.md"}


def test_notes_that_do_not_sync_are_named_in_the_connection_test(conn, laptop):
    n = notes.create(conn, "Aaa", body="# Aaa\n\nalgne\n")
    export(conn)
    laptop.pull()
    (laptop.path / "notes/aaa.md").write_bytes(laptop.read("notes/aaa.md").encode() + b"\xff\n")
    laptop.push()
    gitsync.pull(conn)
    notes.write_body(conn, n["id"], "# Aaa\n\nserver edit\n")
    export(conn)
    assert gitsync.test_connection(conn)["summary"].endswith(". Sünkroonimata: 1 (Aaa)")
    laptop.write("notes/aaa.md", "---\nid: %d\n---\n\n# Aaa\n\nparandatud\n" % n["id"])
    laptop.push()
    gitsync.pull(conn)
    export(conn)
    assert gitsync.problems(conn) == [] and "Sünkroonimata" not in gitsync.test_connection(conn)["summary"]


def test_rename_plus_copy_gives_the_note_to_the_unchanged_file(conn, laptop):
    n = notes.create(conn, "Fff", body="# Fff\n\nsisu\n")
    export(conn)
    laptop.pull()
    text = laptop.read("notes/fff.md")
    laptop.git("rm", "-q", "notes/fff.md")
    laptop.write("notes/zzz.md", text)
    laptop.write("notes/aaa.md", text.replace("sisu", "koopia"))
    laptop.push()
    gitsync.pull(conn)
    n = notes.get(conn, n["id"])
    assert (n["file_path"], n["body"]) == ("notes/zzz.md", "# Fff\n\nsisu\n")
    assert "koopia" in notes.by_slug(conn, "aaa")["body"]


def test_rename_plus_edited_copies_claims_none(conn, laptop):
    n = notes.create(conn, "Fff", body="# Fff\n\nsisu\n")
    export(conn)
    laptop.pull()
    text = laptop.read("notes/fff.md")
    laptop.git("rm", "-q", "notes/fff.md")
    laptop.write("notes/zzz.md", text.replace("sisu", "üks"))
    laptop.write("notes/aaa.md", text.replace("sisu", "kaks"))
    laptop.push()
    gitsync.pull(conn)
    assert notes.get(conn, n["id"]) is None  # its file is gone and no file is clearly it
    assert {notes.by_slug(conn, s)["body"] for s in ("aaa", "zzz")} == {"# Fff\n\nüks\n", "# Fff\n\nkaks\n"}
