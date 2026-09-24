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

@pytest.mark.parametrize("front", ["foo: !!set {a: null}", "foo: !!timestamp 2026-01-01", "foo: !!binary aGVsbG8="])
def test_yaml_tags_do_not_stop_the_sync(conn, laptop, front):
    notes.create(conn, "Tavaline")
    export(conn)
    laptop.pull()
    laptop.write("notes/uus.md", f"---\n{front}\n---\n\n# Uus\n")
    laptop.push()
    assert gitsync.pull(conn) == (1, 0)
    assert gitsync.pull(conn) == (0, 0)
    assert isinstance(json.loads(notes.by_slug(conn, "uus")["frontmatter_extra"])["foo"], str)
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
