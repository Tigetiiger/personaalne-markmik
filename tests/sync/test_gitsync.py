"""app/gitsync.py against a bare repo, with a second clone as the laptop."""

import base64
import json
import subprocess
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import collab, gitsync, notes, util, vault


def age(conn, minutes=5):
    """Make every note look idle for the export."""
    conn.execute("UPDATE notes SET updated_at = ?", (util.iso(util.now_utc() - timedelta(minutes=minutes)),))


def note(conn, title):
    return conn.execute("SELECT * FROM notes WHERE title = ? AND deleted_at IS NULL", (title,)).fetchone()


def export(conn):
    age(conn)
    return gitsync.export(conn)


def test_export_writes_note_files(conn, laptop):
    notes.create(conn, "Keemia")
    m = notes.create(conn, "Matemaatika", folder="kool", body="# Matemaatika\n\nVaata [Keemia](/m/keemia).\n")
    assert export(conn) == 2
    laptop.pull()
    text = laptop.read("notes/kool/matemaatika.md")
    assert text.startswith("---\ntitle: Matemaatika\ntype: note\ntags: []\n")
    assert f"source: markmik\nid: {m['id']}\n---\n\n# Matemaatika\n\nVaata [[keemia|Keemia]].\n" in text
    assert laptop.exists("notes/keemia.md") and laptop.exists("pages/naide.md")
    assert "markmik: 2 notes" in laptop.git("log", "-1", "--format=%B")
    m = notes.get(conn, m["id"])
    assert (m["exported_rev"], m["file_path"]) == (m["rev"], "notes/kool/matemaatika.md")
    head = laptop.git("rev-parse", "HEAD")
    assert gitsync.export(conn) == 0
    laptop.pull()
    assert laptop.git("rev-parse", "HEAD") == head


def test_only_idle_notes_are_exported(conn, brain):
    n = notes.create(conn, "Värske")
    assert gitsync.export(conn) == 0
    age(conn, minutes=10)
    assert gitsync.export(conn) == 1
    notes.set_tags(conn, n["id"], ["kool"])   # metadata only: updated_at moves, rev does not
    age(conn, minutes=3)
    assert gitsync.export(conn) == 1


def test_link_to_a_name_used_elsewhere_in_the_brain_gets_the_path_form(conn, laptop):
    notes.create(conn, "Näide")  # pages/naide.md already exists in the brain
    notes.create(conn, "Viitaja", body="# Viitaja\n[Näide](/m/naide) ja [Viitaja](/m/viitaja)\n")
    export(conn)
    laptop.pull()
    assert "[[notes/naide|Näide]] ja [[viitaja|Viitaja]]" in laptop.read("notes/viitaja.md")


def test_laptop_edit_updates_the_note(conn, laptop):
    notes.create(conn, "Keemia")
    m = notes.create(conn, "Matemaatika", body="# Matemaatika\n\nesimene\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/matemaatika.md", laptop.read("notes/matemaatika.md").replace(
        "esimene\n", "teine, vaata [[keemia|Keemiat]]\n").replace("tags: []", "tags: [kool]"))
    laptop.push()
    assert gitsync.pull(conn) == (1, 0)
    m = notes.get(conn, m["id"])
    assert m["body"] == "# Matemaatika\n\nteine, vaata [Keemiat](/m/keemia)\n"
    assert json.loads(m["tags_json"]) == ["kool"] and m["exported_rev"] == m["rev"]
    assert gitsync.pull(conn) == (0, 0)
    assert export(conn) == 0  # the database now holds exactly the file


def test_laptop_adds_a_note(conn, laptop):
    laptop.write("notes/kool/fuusika.md", "---\ntitle: Füüsika\ntags: [kool, katse]\nallikas: raamat\n---\n"
                                          "# Füüsika\n\nKatse [[pages/naide|näide]].\n")
    laptop.write("notes/ilma-pealkirjata.md", "lihtsalt tekst\n")
    laptop.push()
    assert gitsync.pull(conn) == (2, 0)
    f = notes.by_slug(conn, "fuusika")
    assert (f["title"], f["folder"], f["body"]) == ("Füüsika", "kool", "# Füüsika\n\nKatse [[pages/naide|näide]].\n")
    assert json.loads(f["tags_json"]) == ["kool", "katse"]
    assert json.loads(f["frontmatter_extra"]) == {"allikas": "raamat"}
    plain = notes.by_slug(conn, "ilma-pealkirjata")
    assert plain["body"] == "# ilma-pealkirjata\n\nlihtsalt tekst\n"
    export(conn)
    laptop.pull()
    text = laptop.read("notes/kool/fuusika.md")
    assert f"id: {f['id']}\nallikas: raamat\n---\n" in text
    assert gitsync.pull(conn) == (0, 0)


def test_laptop_deletes_a_note(conn, laptop):
    m = notes.create(conn, "Kustuta mind")
    export(conn)
    laptop.pull()
    laptop.git("rm", "-q", "notes/kustuta-mind.md")
    laptop.push()
    assert gitsync.pull(conn) == (1, 0)
    assert notes.get(conn, m["id"]) is None


def test_laptop_delete_keeps_a_note_changed_here(conn, laptop):
    m = notes.create(conn, "Hoia")
    export(conn)
    laptop.pull()
    laptop.git("rm", "-q", "notes/hoia.md")
    laptop.push()
    notes.write_body(conn, m["id"], "# Hoia\n\nuus tekst\n")
    gitsync.pull(conn)
    assert notes.get(conn, m["id"])["body"] == "# Hoia\n\nuus tekst\n"
    export(conn)
    laptop.pull()
    assert "uus tekst" in laptop.read("notes/hoia.md")


def test_deleted_here_but_edited_there_comes_back(conn, laptop):
    m = notes.create(conn, "Vana")
    export(conn)
    laptop.pull()
    notes.soft_delete(conn, m["id"])
    laptop.write("notes/vana.md", laptop.read("notes/vana.md") + "\nsülearvutis lisatud\n")
    laptop.push()
    gitsync.pull(conn)
    back = note(conn, "Vana")
    assert back["id"] != m["id"] and "sülearvutis lisatud" in back["body"]
    export(conn)
    laptop.pull()
    assert "sülearvutis lisatud" in laptop.read(notes.get(conn, back["id"])["file_path"])


def test_laptop_moves_a_file(conn, laptop):
    m = notes.create(conn, "Rändaja")
    export(conn)
    laptop.pull()
    (laptop.path / "notes/kool").mkdir()
    laptop.git("mv", "notes/randaja.md", "notes/kool/randaja.md")
    laptop.push()
    gitsync.pull(conn)
    m = notes.get(conn, m["id"])
    assert (m["folder"], m["file_path"]) == ("kool", "notes/kool/randaja.md")
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_folder_change_moves_the_file(conn, laptop):
    m = notes.create(conn, "Kolija")
    export(conn)
    notes.move(conn, m["id"], "kool/vana")
    assert export(conn) == 1
    laptop.pull()
    assert laptop.exists("notes/kool/vana/kolija.md") and not laptop.exists("notes/kolija.md")


def test_conflict_keeps_both_versions(conn, laptop):
    m = notes.create(conn, "Matemaatika", body="# Matemaatika\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/matemaatika.md", laptop.read("notes/matemaatika.md").replace("algne", "sülearvuti"))
    laptop.push()
    notes.write_body(conn, m["id"], "# Matemaatika\n\nrakendus\n")
    assert gitsync.pull(conn) == (0, 1)
    assert notes.get(conn, m["id"])["body"] == "# Matemaatika\n\nrakendus\n"
    copy = note(conn, "Matemaatika (konflikt)")
    assert copy["body"] == "# Matemaatika (konflikt)\n\nsülearvuti\n"
    export(conn)
    laptop.pull()
    assert "rakendus" in laptop.read("notes/matemaatika.md")
    assert "sülearvuti" in laptop.read(f"notes/{copy['slug']}.md")
    assert gitsync.pull(conn) == (0, 0)


def test_metadata_only_on_the_laptop_is_not_a_conflict(conn, laptop):
    m = notes.create(conn, "Sildid", body="# Sildid\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/sildid.md", laptop.read("notes/sildid.md").replace("tags: []", "tags: [uus]"))
    laptop.push()
    notes.write_body(conn, m["id"], "# Sildid\n\nrakendus\n")
    assert gitsync.pull(conn) == (1, 0)
    m = notes.get(conn, m["id"])
    assert m["body"] == "# Sildid\n\nrakendus\n" and json.loads(m["tags_json"]) == ["uus"]
    export(conn)
    laptop.pull()
    assert "tags: [uus]" in laptop.read("notes/sildid.md") and "rakendus" in laptop.read("notes/sildid.md")


def test_busy_note_waits(conn, laptop, monkeypatch):
    m = notes.create(conn, "Lahti", body="# Lahti\n\nalgne\n")
    export(conn)
    laptop.pull()
    laptop.write("notes/lahti.md", laptop.read("notes/lahti.md").replace("algne", "muudetud"))
    laptop.push()
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: note_id == m["id"])
    assert gitsync.pull(conn) == (0, 0)
    assert "algne" in notes.get(conn, m["id"])["body"]
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: False)
    assert gitsync.pull(conn) == (1, 0)
    assert "muudetud" in notes.get(conn, m["id"])["body"]


def test_busy_note_is_not_exported(conn, brain, monkeypatch):
    notes.create(conn, "Lahti")
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    assert export(conn) == 0


def _push_first(monkeypatch, laptop_change):
    """Let the laptop push right before the app's first push."""
    real, done = gitsync.Repo.run, []

    def run(self, *args, **kw):
        if args[0] == "push" and not done:
            done.append(1)
            laptop_change()
        return real(self, *args, **kw)
    monkeypatch.setattr(gitsync.Repo, "run", run)


def test_rejected_push_rebases_and_retries(conn, laptop, monkeypatch):
    notes.create(conn, "Esimene")
    export(conn)
    laptop.pull()
    notes.create(conn, "Teine")

    def meanwhile():
        laptop.write("pages/samal-ajal.md", "# Samal ajal\n")
        laptop.push()
    _push_first(monkeypatch, meanwhile)
    assert export(conn) == 1
    laptop.pull()
    assert laptop.exists("notes/teine.md") and laptop.exists("pages/samal-ajal.md")
    assert gitsync.pull(conn) == (0, 0)


@pytest.mark.parametrize("killed", [False, True])
def test_rebase_conflict_becomes_a_conflict_copy(conn, laptop, monkeypatch, killed):
    original_run = gitsync.Repo.run
    m = notes.create(conn, "Tüli", body="# Tüli\n\nalgne\n")
    export(conn)
    laptop.pull()
    notes.write_body(conn, m["id"], "# Tüli\n\nrakendus\n")

    def meanwhile():
        laptop.write("notes/tuli.md", laptop.read("notes/tuli.md").replace("algne", "sülearvuti"))
        laptop.push()
    _push_first(monkeypatch, meanwhile)
    if killed:  # the process died mid-rebase: the next run finds the rebase still going
        real = gitsync.Repo.run
        monkeypatch.setattr(gitsync.Repo, "run", lambda self, *a, **kw: None if a == ("rebase", "--abort")
                            else real(self, *a, **kw))
    with pytest.raises(gitsync.GitError):
        export(conn)
    monkeypatch.setattr(gitsync.Repo, "run", original_run)
    assert gitsync.pull(conn) == (0, 1)
    assert "sülearvuti" in note(conn, "Tüli (konflikt)")["body"]
    export(conn)
    laptop.pull()
    assert "rakendus" in laptop.read("notes/tuli.md")


def test_images_are_copied(conn, laptop, tmp_path):
    (tmp_path / "pildid").mkdir()
    (tmp_path / "pildid" / "ab12.png").write_bytes(b"\x89PNG fake")
    m = notes.create(conn, "Pildiga", body="# Pildiga\n\n![joonis](/pildid/ab12.png) ![hiljem](/pildid/cd.png)\n")
    export(conn)
    laptop.pull()
    assert (laptop.path / "notes/_pildid/ab12.png").read_bytes() == b"\x89PNG fake"
    assert "![joonis](/notes/_pildid/ab12.png)" in laptop.read("notes/pildiga.md")
    (tmp_path / "pildid" / "cd.png").write_bytes(b"later")  # arrives after the note went out
    notes.set_pinned(conn, m["id"], True)
    age(conn, minutes=3)  # not the same second as the first export's age()
    assert gitsync.export(conn) == 1
    laptop.pull()
    assert (laptop.path / "notes/_pildid/cd.png").read_bytes() == b"later"


def test_sync_now_endpoint(owner, conn, brain):
    notes.create(conn, "Kohe")  # not idle yet: sync now does not wait
    r = owner.post("/api/sync/now")
    assert r.status_code == 200 and r.get_json() == {"pushed": 1, "pulled": 0, "conflicts": 0}


def test_no_token_is_a_quiet_not_connected_state(owner, conn, monkeypatch):
    def no_git(*a, **kw):
        raise AssertionError("git must not run without a token")
    monkeypatch.setattr(subprocess, "run", no_git)
    assert gitsync.sync_job(conn) is None
    assert gitsync.test_connection(conn) == {"ok": False, "summary": "", "error": "GitHubi token on sisestamata."}
    r = owner.post("/api/sync/now")
    assert r.status_code == 409 and "ühendatud" in r.get_json()["error"]


def test_connection_test_reports_the_last_commit(conn, brain):
    result = gitsync.test_connection(conn)
    assert result["ok"] and result["summary"].endswith(": seed")


class _AuthRecorder(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        self.seen.append(self.headers.get("Authorization"))
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.end_headers()

    def log_message(self, *args):
        pass


def test_token_never_lands_in_config_logs_or_errors(conn, brain, caplog):
    token = "github_pat_SALAJANE_1234567890"
    vault.set(conn, "brain_token", token)
    gitsync.pull(conn)  # clone from the local remote first
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthRecorder)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/brain.git"
        conn.execute("UPDATE settings SET value = ? WHERE key = 'brain_remote'", (url,))
        caplog.set_level("DEBUG")
        with pytest.raises(gitsync.GitError) as err:
            gitsync.pull(conn)
        result = gitsync.test_connection(conn)
    finally:
        server.shutdown()
    basic = "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    assert basic in _AuthRecorder.seen  # the token did reach the server, as a header
    config = (gitsync.data_dir() / "brain" / ".git" / "config").read_text()
    assert url in config
    for secret in (token, basic.split()[1]):
        assert secret not in config and secret not in caplog.text
        assert secret not in str(err.value) and secret not in json.dumps(result)
    assert not result["ok"]


@pytest.mark.parametrize("url", [
    "https://evil.example/Tigetiiger/brain.git", "https://github.com.evil.example/Tigetiiger/brain.git",
    "http://127.0.0.1:9/brain.git", "https://x:github_pat_abc@github.com/Tigetiiger/brain.git",
    "https://github.com/Tigetiiger/brain.git?x=1", "/srv/brain.git", "file:///srv/brain.git",
])
def test_a_stored_remote_off_github_is_refused_before_git_runs(owner, conn, monkeypatch, url):
    """A value saved before the check existed, or written into the database by hand (N3)."""
    vault.set(conn, "brain_token", "github_pat_SALAJANE_1234567890")
    conn.execute("INSERT INTO settings (key, value) VALUES ('brain_remote', ?)", (url,))

    def no_git(*a, **kw):
        raise AssertionError("git must not run")
    monkeypatch.setattr(subprocess, "run", no_git)
    for run in (gitsync.pull, gitsync.export, gitsync.sync_job):
        with pytest.raises(gitsync.GitError, match="^Repo aadress peab olema kujul https://github.com/"):
            run(conn)
    r = owner.post("/api/connections/brain/test")
    assert not r.get_json()["ok"] and r.get_json()["error"] == gitsync.BAD_REMOTE
    assert owner.post("/api/sync/now").status_code == 502


@pytest.mark.parametrize("url", ["https://github.com/Tigetiiger/brain.git", "https://github.com/Tigetiiger/brain"])
def test_the_token_goes_only_to_github(conn, url):
    vault.set(conn, "brain_token", "github_pat_SALAJANE_1234567890")
    conn.execute("INSERT INTO settings (key, value) VALUES ('brain_remote', ?)", (url,))
    repo = gitsync.Repo(conn)
    config = {repo.env[f"GIT_CONFIG_KEY_{i}"] for i in range(int(repo.env["GIT_CONFIG_COUNT"]))}
    assert repo.url == url and "http.https://github.com/.extraHeader" in config


def test_a_note_restored_from_the_trash_gets_its_file_back(conn, laptop):
    m = notes.create(conn, "Taastatav", folder="kool")
    export(conn)
    notes.soft_delete(conn, m["id"])
    export(conn)
    laptop.pull()
    assert not laptop.exists("notes/kool/taastatav.md")
    assert notes.in_trash(conn, "taastatav")["file_path"] is None
    notes.restore(conn, m["id"])
    export(conn)
    laptop.pull()
    assert laptop.exists("notes/kool/taastatav.md")
    assert notes.get(conn, m["id"])["file_path"] == "notes/kool/taastatav.md"


def test_a_note_deleted_for_good_leaves_the_table_only_after_git_dropped_its_file(conn, laptop):
    m = notes.create(conn, "Lõplik")
    export(conn)
    notes.soft_delete(conn, m["id"])
    notes.forget(conn, m["id"])
    assert conn.execute("SELECT 1 FROM notes WHERE id = ?", (m["id"],)).fetchone() is not None
    assert notes.trash(conn) == []
    export(conn)
    assert notes.purge(conn) == 1
    laptop.pull()
    assert not laptop.exists("notes/loplik.md")
    gitsync.pull(conn)
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


def test_a_new_note_taking_its_title_slug_moves_its_file(conn, laptop):
    n = notes.create(conn)
    notes.write_body(conn, n["id"], "# Ajalugu\n")
    export(conn)
    laptop.pull()
    assert laptop.exists("notes/marge.md")
    notes.settle(conn, n["id"])
    export(conn)
    laptop.pull()
    assert laptop.exists("notes/ajalugu.md") and not laptop.exists("notes/marge.md")
    gitsync.pull(conn)
    assert [r["slug"] for r in conn.execute("SELECT slug FROM notes")] == ["ajalugu"]
