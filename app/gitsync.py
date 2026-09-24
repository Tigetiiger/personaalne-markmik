"""Git sync of notes with the brain repo (docs/PLAN-OVERVIEW.md §11, docs/CONTRACTS.md §4 gbrain).

The app keeps its own clone in <DATA_DIR>/brain and writes only under notes/. A run is pull
(fetch, reset to the remote, import what changed under notes/) and then export (write the notes
that changed here, one commit, push; when the push is rejected, rebase and push once more).

The database is the source of truth for everything the app writes, so the clone never holds
anything that cannot be rebuilt: a commit that failed to push is dropped by the next pull and
written again by the next export. Nothing is marked exported until the push went through.

Per note, settings "gitsync:state" keeps {"blob": git blob of the file as last synced, "at":
the note's updated_at then}. A blob that differs from the file in git means the file changed
there; notes.exported_rev < rev means the body changed here. Import merges three ways against the
last synced file; when both sides changed the body, the database keeps its version and the file's
version becomes a "(konflikt)" copy. Nothing is lost.

The token (vault "brain_token") reaches git only through GIT_CONFIG_* environment variables built
at call time and scoped to the remote's host: never in .git/config, argv, logs or errors.
"""

import base64
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import collab, connections, linkfmt, notes, util, vault
from .db import tx
from .jobs import job

log = logging.getLogger("markmik.gitsync")

DEFAULT_REMOTE = "https://github.com/Tigetiiger/brain.git"
IDLE = timedelta(minutes=2)
NOTES_DIR = "notes"
IMAGES_DIR = "notes/_pildid"
STATE_KEY = "gitsync:state"
IDENTITY = {"GIT_AUTHOR_NAME": "Märkmik", "GIT_AUTHOR_EMAIL": "markmik@localhost",
            "GIT_COMMITTER_NAME": "Märkmik", "GIT_COMMITTER_EMAIL": "markmik@localhost"}
_H1_RE = re.compile(r" {0,3}#[ \t]+\S")


class NotConnected(RuntimeError):
    """An HTTP(S) remote and no token yet: nothing to do until the owner connects the repo."""


class GitError(RuntimeError):
    pass


def data_dir():
    return Path(os.environ.get("DATA_DIR", "data"))


def remote_url(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'brain_remote'").fetchone()
    return row["value"] if row and row["value"] else DEFAULT_REMOTE


class Repo:
    """The app's clone and how to run git on it."""

    def __init__(self, conn):
        self.url = remote_url(conn)
        self.path = data_dir() / "brain"
        self._secrets = ()
        # Nothing from the host's git config (signing, hooks, credential helpers) applies here.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(IDENTITY, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
        parts = urlsplit(self.url)
        if parts.scheme in ("http", "https"):
            token = vault.get(conn, "brain_token")
            if not token:
                raise NotConnected
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            self._secrets = (token, basic)
            self.env.update(GIT_CONFIG_COUNT="1",
                            GIT_CONFIG_KEY_0=f"http.{parts.scheme}://{parts.netloc}/.extraHeader",
                            GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")

    def run(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.path, env=self.env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def git(self, *args, cwd=None):
        """stdout; GitError (token scrubbed) when git fails."""
        r = self.run(*args, cwd=cwd)
        if r.returncode:
            raise self.error(r, args[0])
        return r.stdout

    def error(self, r, what):
        text = (r.stderr.strip() or r.stdout.strip()).splitlines()
        text = text[-1] if text else f"exit {r.returncode}"
        for secret in self._secrets:
            text = text.replace(secret, "••••")
        return GitError(f"git {what}: {text}")

    def branch(self):
        return self.git("symbolic-ref", "--short", "HEAD").strip()


def ensure_clone(conn):
    """The clone, made on first use and kept pointed at the configured remote."""
    repo = Repo(conn)
    if not (repo.path / ".git").is_dir():
        repo.path.mkdir(parents=True, exist_ok=True)
        repo.git("clone", "--quiet", "--", repo.url, str(repo.path), cwd=repo.path.parent)
    elif repo.git("remote", "get-url", "origin").strip() != repo.url:
        repo.git("remote", "set-url", "origin", repo.url)
    if any((repo.path / ".git" / d).exists() for d in ("rebase-merge", "rebase-apply")):
        repo.run("rebase", "--abort")  # a run killed mid-rebase leaves HEAD detached
    return repo


def pull(conn):
    """Bring the clone to the remote's state and import changes under notes/. -> (pulled, conflicts)"""
    Repo(conn)  # NotConnected before waiting for the lock
    with _locked():
        repo = ensure_clone(conn)
        branch = repo.branch()
        repo.git("fetch", "--quiet", "origin")
        repo.git("reset", "--quiet", "--hard", f"origin/{branch}")  # see the module docstring
        repo.git("clean", "-fdq", "--", NOTES_DIR)
        state = _load_state(conn)
        result = _Import(conn, repo, state).run()
        _save_state(conn, state)
    if any(result):
        log.info("git sync: %d note(s) pulled, %d conflict(s)", *result)
    return result


def export(conn, *, idle=IDLE):
    """Write notes changed here (and idle for `idle`, and not open in an editor) to the clone,
    commit once, push. -> number of notes written or removed."""
    Repo(conn)
    with _locked():
        repo = ensure_clone(conn)
        state = _load_state(conn)
        pushed = _Export(conn, repo, state, idle).run()
        _save_state(conn, state)
    if pushed:
        log.info("git sync: %d note(s) pushed", pushed)
    return pushed


def sync_now(conn):
    """POST /api/sync/now: pull, then export without waiting for notes to go idle."""
    pulled, conflicts = pull(conn)
    return {"pushed": export(conn, idle=timedelta(0)), "pulled": pulled, "conflicts": conflicts}


@job("git-sync", every=300)
def sync_job(conn):
    try:
        pull(conn)
        export(conn)
    except NotConnected:
        log.debug("brain repo not connected yet")


def test_connection(conn):
    try:
        repo = Repo(conn)
    except NotConnected:
        return {"ok": False, "summary": "", "error": "GitHubi token on sisestamata."}
    data_dir().mkdir(parents=True, exist_ok=True)
    r = repo.run("ls-remote", "--heads", "--", repo.url, cwd=data_dir())
    if r.returncode:
        return {"ok": False, "summary": "", "error": str(repo.error(r, "ls-remote"))}
    with _locked():
        repo = ensure_clone(conn)
        repo.git("fetch", "--quiet", "origin")
        day, subject = repo.git("log", "-1", "--format=%cs %s", f"origin/{repo.branch()}").strip().split(" ", 1)
    return {"ok": True, "summary": f"Viimane muudatus {'.'.join(reversed(day.split('-')))}: {subject}", "error": None}


connections.register("brain", "Brain repo", order=40, test=test_connection, secret_names=("brain_token",))


class _Import:
    def __init__(self, conn, repo, state):
        self.conn, self.repo, self.state = conn, repo, state
        self.pulled = self.conflicts = 0

    def run(self):
        conn = self.conn
        tree = _tree(self.repo)
        rows = conn.execute("SELECT * FROM notes WHERE file_path IS NOT NULL").fetchall()
        live = {r["file_path"]: r for r in rows if r["deleted_at"] is None}
        dead = {r["file_path"]: r for r in rows if r["deleted_at"] is not None}
        self.slugs = {r[0] for r in conn.execute("SELECT slug FROM notes WHERE deleted_at IS NULL")}
        self.slugs |= {_stem(p) for p in tree}
        claimed = set()
        for path, blob in sorted(tree.items()):
            note = live.get(path)
            if note is not None and self._blob(note) == blob:
                continue
            if note is None and path in dead and self._blob(dead[path]) == blob:
                continue  # deleted here; the export removes the file
            meta, body = linkfmt.from_file(self.repo.git("cat-file", "blob", blob), note_slugs=self.slugs)
            if note is not None:
                self._changed(note, note["file_path"], path, blob, meta, body)
                continue
            if path in dead:  # deleted here but edited there: the edit comes back as a new note
                self._forget(dead[path])
            note = self._claim(meta, path, tree)
            if note is None:
                self._create(path, blob, meta, body)
            else:
                claimed.add(note["id"])
                self._changed(note, note["file_path"], path, blob, meta, body)
        for path, note in live.items():
            if path not in tree and note["id"] not in claimed:
                self._removed(note)
        return self.pulled, self.conflicts

    def _blob(self, note):
        return (self.state.get(str(note["id"])) or {}).get("blob")

    def _claim(self, meta, path, tree):
        """The existing note a new file belongs to: the note its frontmatter id names when that
        note's own file is gone (moved on the laptop), else a note never exported to this path."""
        try:
            note = notes.get(self.conn, int(meta.get("id")))
        except (TypeError, ValueError):
            note = None
        if note is not None and note["slug"] == _stem(path) and note["file_path"] not in tree:
            return note
        for note in self.conn.execute("SELECT * FROM notes WHERE file_path IS NULL AND deleted_at IS NULL "
                                      "AND slug = ?", (_stem(path),)):
            if _path(note) == path:
                return note
        return None

    def _changed(self, note, old_path, path, blob, meta, body):
        conn, key = self.conn, str(note["id"])
        st = self.state.get(key) or {}
        if st.get("blob"):
            base_meta, base_body = linkfmt.from_file(self.repo.git("cat-file", "blob", st["blob"]),
                                                     note_slugs=self.slugs)
            base = _file_fields(base_meta, old_path)
        else:
            base = base_body = None
        theirs, ours, our_body = _file_fields(meta, path), _note_fields(note), note["body"]
        body_dirty = note["rev"] > note["exported_rev"]
        if body != note["body"] and body_dirty and body != base_body:
            self._conflict(note, body, theirs)
            conn.execute("UPDATE notes SET file_path = ? WHERE id = ?", (path, note["id"]))
            self.state[key] = {"blob": blob, "at": st.get("at", "")}
            return
        try:
            with tx(conn):
                if body != note["body"] and not body_dirty:
                    notes.write_body(conn, note["id"], body)
                for field, value in theirs.items():
                    if base and value != ours[field] and value != base[field] and ours[field] == base[field]:
                        _set_field(conn, note["id"], field, value)
                note = notes.get(conn, note["id"])
                in_sync = note["body"] == body
                conn.execute("UPDATE notes SET file_path = ?, exported_rev = CASE WHEN ? THEN rev ELSE exported_rev END "
                             "WHERE id = ?", (path, in_sync, note["id"]))
        except notes.NoteBusy:
            return  # open in an editor: the next run tries again (the blob is not marked)
        # When the database now holds exactly the file, the file needs no rewrite; otherwise the
        # old "at" makes the export write the merged note.
        same = in_sync and _note_fields(note) == theirs
        self.state[key] = {"blob": blob, "at": note["updated_at"] if same else st.get("at", "")}
        self.pulled += note["body"] != our_body or _note_fields(note) != ours

    def _conflict(self, note, body, theirs):
        title = notes.derive_title(body)
        with tx(self.conn):
            copy = notes.create(self.conn, f"{title} (konflikt)", folder=theirs["folder"],
                                body=_retitle(body, " (konflikt)"))
            notes.set_tags(self.conn, copy["id"], theirs["tags"])
        self.conflicts += 1
        log.warning("git sync: note %s changed on both sides; the file's version is in %s", note["slug"], copy["slug"])

    def _create(self, path, blob, meta, body):
        conn = self.conn
        # With a "# " heading in the body the title comes from it, so the file name can give the
        # slug and the file keeps its name; without one, create() puts the title on top.
        title = _stem(path) if _h1_line(body) is not None else str(meta.get("title") or _stem(path))
        fields = _file_fields(meta, path)
        with tx(conn):
            note = notes.create(conn, title, folder=fields["folder"], body=body)
            for field in ("tags", "aliases", "extra"):
                if fields[field]:
                    _set_field(conn, note["id"], field, fields[field])
            conn.execute("UPDATE notes SET file_path = ?, exported_rev = rev WHERE id = ?", (path, note["id"]))
        # "at" empty: the export rewrites the file once, with the id in its frontmatter.
        self.state[str(note["id"])] = {"blob": blob, "at": ""}
        self.pulled += 1

    def _removed(self, note):
        st = self.state.get(str(note["id"])) or {}
        if note["rev"] > note["exported_rev"] or note["updated_at"] > st.get("at", ""):
            self._forget(note)  # changed here since: kept, and the export writes the file again
            return
        if collab.is_busy(self.conn, note["id"]):
            return
        notes.soft_delete(self.conn, note["id"])
        self._forget(note)
        self.pulled += 1

    def _forget(self, note):
        self.conn.execute("UPDATE notes SET file_path = NULL WHERE id = ?", (note["id"],))
        self.state.pop(str(note["id"]), None)


class _Export:
    def __init__(self, conn, repo, state, idle):
        self.conn, self.repo, self.state = conn, repo, state
        self.cutoff = util.iso(util.now_utc() - idle)

    def run(self):
        conn, repo = self.conn, self.repo
        rows = conn.execute("SELECT * FROM notes WHERE deleted_at IS NULL OR file_path IS NOT NULL").fetchall()
        live = [r for r in rows if r["deleted_at"] is None]
        self.targets = {_path(r) for r in live}
        folders = {r["slug"]: r["folder"] for r in live}
        taken = {slug: folders[slug] for slug in self._taken(live)}
        marks, titles = [], []
        for note in rows:
            if note["deleted_at"] is not None:
                if self._remove(note["file_path"]):
                    titles.append(f"{note['title']} (deleted)")
                marks.append((note, None, None))
                continue
            st, target = self.state.get(str(note["id"])), _path(note)
            if (st and note["rev"] <= note["exported_rev"] and note["updated_at"] <= st["at"]
                    and note["file_path"] == target):
                continue
            if note["updated_at"] > self.cutoff or collab.is_busy(conn, note["id"]):
                continue
            text = linkfmt.to_file(note, note["body"], basename_taken=taken)
            file = repo.path / target
            changed = not file.is_file() or file.read_text(encoding="utf-8", errors="replace") != text
            if changed:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            if note["file_path"] and note["file_path"] != target:
                changed |= self._remove(note["file_path"])
            self._copy_images(note["body"])
            marks.append((note, target, _blob_id(text)))
            if changed:
                titles.append(note["title"])
        if titles:
            repo.git("add", "-A", "--", NOTES_DIR)
            repo.git("commit", "--quiet", "-m", _message(titles))
        branch = repo.branch()
        if int(repo.git("rev-list", "--count", f"origin/{branch}..HEAD")):
            self._push(branch)
        with tx(conn):
            for note, path, blob in marks:
                key = str(note["id"])
                if path is None:
                    conn.execute("UPDATE notes SET file_path = NULL WHERE id = ?", (note["id"],))
                    self.state.pop(key, None)
                else:
                    conn.execute("UPDATE notes SET exported_rev = ?, file_path = ? WHERE id = ?",
                                 (note["rev"], path, note["id"]))
                    self.state[key] = {"blob": blob, "at": note["updated_at"]}
        return len(titles)

    def _push(self, branch):
        repo = self.repo
        if not repo.run("push", "--quiet", "origin", f"HEAD:{branch}").returncode:
            return
        # Rejected, most likely because the laptop pushed meanwhile: rebase onto it and retry once.
        # A rebase conflict is a true conflict; the next pull drops our commit and sorts it out.
        r = repo.run("pull", "--rebase", "--quiet", "origin", branch)
        if r.returncode:
            repo.run("rebase", "--abort")
            raise repo.error(r, "pull --rebase")
        repo.git("push", "--quiet", "origin", f"HEAD:{branch}")

    def _remove(self, path):
        """Delete a note's old file unless another note's file lives there now."""
        file = self.repo.path / path
        if path in self.targets or not file.is_file():
            return False
        file.unlink()
        return True

    def _copy_images(self, body):
        for name in linkfmt.image_names(body):
            src, dst = data_dir() / "pildid" / name, self.repo.path / IMAGES_DIR / name
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)

    def _taken(self, live):
        """Slugs whose file name some other .md in the repo has too: a bare [[slug]] would be
        ambiguous for gbrain, so links to them get the path form."""
        own = {r["slug"]: {r["file_path"], _path(r)} for r in live}
        count = Counter()
        for path in self.repo.git("ls-files", "-z", "--", "*.md").split("\0"):
            stem = util.slugify(Path(path).stem) if path else None
            if stem in own and path not in own[stem]:
                count[stem] += 1
        return set(count)


def _tree(repo):
    """{path: blob} of note files in HEAD."""
    tree = {}
    for entry in repo.git("ls-tree", "-r", "-z", "HEAD", "--", NOTES_DIR).split("\0"):
        if entry:
            info, path = entry.split("\t", 1)
            _mode, kind, sha = info.split()
            if kind == "blob" and path.endswith(".md") and not path.startswith(IMAGES_DIR + "/"):
                tree[path] = sha
    return tree


def _path(note):
    return "/".join(p for p in (NOTES_DIR, note["folder"], f"{note['slug']}.md") if p)


def _stem(path):
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _folder(path):
    return "/".join(path.split("/")[1:-1])


def _note_fields(note):
    return {"folder": note["folder"], "tags": json.loads(note["tags_json"]),
            "aliases": json.loads(note["aliases_json"]), "extra": json.loads(note["frontmatter_extra"])}


def _file_fields(meta, path):
    return {"folder": _folder(path), "tags": _as_list(meta.get("tags"), tags=True),
            "aliases": _as_list(meta.get("aliases"), tags=False),
            "extra": {k: v for k, v in meta.items() if k not in linkfmt.KNOWN_KEYS}}


def _as_list(value, *, tags):
    """Frontmatter list as notes stores it: `tags: a, #b` or a YAML list; cleaned, each once."""
    if value is None:
        return []
    if not isinstance(value, list):
        value = re.split(r"[,\s]+", str(value)) if tags else [value]
    out = []
    for item in value:
        item = " ".join(str(item).lstrip("#" if tags else "").split())
        if item and item.casefold() not in {o.casefold() for o in out}:
            out.append(item)
    return out


def _set_field(conn, note_id, field, value):
    if field == "folder":
        notes.move(conn, note_id, value)
    elif field == "tags":
        notes.set_tags(conn, note_id, value)
    elif field == "aliases":
        notes.set_aliases(conn, note_id, value)
    else:
        conn.execute("UPDATE notes SET frontmatter_extra = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(value, ensure_ascii=False), util.now_iso(), note_id))


def _h1_line(body):
    for i, line in enumerate(notes.mask_code(body).split("\n")):
        if _H1_RE.match(line):
            return i
    return None


def _retitle(body, suffix):
    """body with suffix added to its first "# " heading (create() adds a heading when none)."""
    i = _h1_line(body)
    if i is None:
        return body
    lines = body.split("\n")
    lines[i] = lines[i].rstrip() + suffix
    return "\n".join(lines)


def _blob_id(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _message(titles):
    head = f"markmik: {len(titles)} note{'' if len(titles) == 1 else 's'}"
    return head + "\n\n" + "\n".join(f"- {t}" for t in titles)


def _load_state(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (STATE_KEY,)).fetchone()
    return json.loads(row["value"]) if row else {}


def _save_state(conn, state):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (STATE_KEY, json.dumps(state)))


@contextmanager
def _locked():
    """One sync at a time across the web process and the worker."""
    path = data_dir() / "brain.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
