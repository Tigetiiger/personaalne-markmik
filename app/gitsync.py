"""Git sync of notes with the brain repo (docs/PLAN-OVERVIEW.md §11, docs/CONTRACTS.md §4 gbrain).

The app keeps its own clone in <DATA_DIR>/brain and writes only under notes/. A run is pull
(fetch, reset to the remote, import what changed under notes/) and then export (write the notes
that changed here, one commit, push; when the push is rejected, rebase and push once more).

The database is the source of truth for everything the app writes, so the clone never holds
anything that cannot be rebuilt: a commit that failed to push is dropped by the next pull and
written again by the next export. Nothing is marked exported until it is in the pushed HEAD.

Per note, settings "gitsync:note:<id>" keeps {"blob": git blob of the file as last synced, "at":
the note's updated_at then}, written in the same transaction as the note. A blob that differs
from the file in git means the file changed there; notes.exported_rev < rev means the body
changed here. Import merges three ways against the last synced file; when both sides changed the
body, the database keeps its version and the file's version becomes a "(konflikt)" copy. Each
file is imported in its own transaction and a file that fails is logged and left for the next
run, so one bad file never stops the others. Nothing is lost.

The clone is written by git and by this module only. Symlinks are never checked out
(core.symlinks=false), only regular files count as notes, and nothing is written outside notes/.

The token (vault "brain_token") reaches git only through GIT_CONFIG_* environment variables built
at call time and scoped to the remote's host: never in .git/config, argv, logs or errors. The
remote must be https://github.com/<owner>/<repo>, checked where it is saved and again here, so the
token never goes to another host (docs/SECURITY-REVIEW.md N3).
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
import unicodedata
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import collab, connections, linkfmt, notes, util, vault
from .db import tx
from .jobs import job

log = logging.getLogger("markmik.gitsync")

DEFAULT_REMOTE = "https://github.com/Tigetiiger/brain.git"
# No user:token@, ?query or #fragment: the whole URL is this pattern.
GITHUB_REMOTE = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
BAD_REMOTE = "Repo aadress peab olema kujul https://github.com/omanik/repo.git (ilma tokenita)."
IDLE = timedelta(minutes=2)
NOTES_DIR = "notes"
IMAGES_DIR = "notes/_pildid"
STATE_PREFIX = "gitsync:note:"
PROBLEMS_KEY = "gitsync:problems:"  # + "pull" / "export": json list of titles that did not sync
REGULAR = ("100644", "100755")  # tree modes of plain files; symlinks and submodules are not notes
IDENTITY = {"GIT_AUTHOR_NAME": "Märkmik", "GIT_AUTHOR_EMAIL": "markmik@localhost",
            "GIT_COMMITTER_NAME": "Märkmik", "GIT_COMMITTER_EMAIL": "markmik@localhost"}
_H1_RE = re.compile(r" {0,3}#[ \t]+\S")


class NotConnected(RuntimeError):
    """An HTTP(S) remote and no token yet: nothing to do until the owner connects the repo."""


class GitError(RuntimeError):
    pass


class _Later(Exception):
    """This file waits for the next run."""


def data_dir():
    return Path(os.environ.get("DATA_DIR", "data"))


def remote_url(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'brain_remote'").fetchone()
    return row["value"] if row and row["value"] else DEFAULT_REMOTE


def github_remote(url):
    """https://github.com/<owner>/<repo>, with or without .git. Tests that use a local bare repo
    replace this function (tests/sync/conftest.py); nothing in the app can widen it."""
    return GITHUB_REMOTE.fullmatch(url) is not None


class Repo:
    """The app's clone and how to run git on it."""

    def __init__(self, conn):
        self.url = remote_url(conn)
        if not github_remote(self.url):  # e.g. a value stored before the check existed
            raise GitError(BAD_REMOTE)
        self.path = data_dir() / "brain"
        self._secrets = ()
        # Nothing from the host's git config (signing, hooks, credential helpers) applies here.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(IDENTITY, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
        config = [("core.symlinks", "false")]  # a committed symlink becomes a plain file
        parts = urlsplit(self.url)
        if parts.scheme in ("http", "https"):
            token = vault.get(conn, "brain_token")
            if not token:
                raise NotConnected
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            self._secrets = (token, basic)
            config.append((f"http.{parts.scheme}://{parts.netloc}/.extraHeader", f"Authorization: Basic {basic}"))
        self.env["GIT_CONFIG_COUNT"] = str(len(config))
        for i, (key, value) in enumerate(config):
            self.env[f"GIT_CONFIG_KEY_{i}"], self.env[f"GIT_CONFIG_VALUE_{i}"] = key, value

    def run(self, *args, cwd=None, text=True):
        try:
            return subprocess.run(["git", *args], cwd=cwd or self.path, env=self.env, capture_output=True,
                                  timeout=120, **({"encoding": "utf-8", "errors": "replace"} if text else {}))
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise GitError(f"git {args[0]}: {exc.__class__.__name__}") from None

    def git(self, *args, cwd=None):
        """stdout; GitError (token scrubbed) when git fails."""
        r = self.run(*args, cwd=cwd)
        if r.returncode:
            raise self.error(r, args[0])
        return r.stdout

    def blob(self, sha):
        """A file's text; UnicodeDecodeError when it is not UTF-8."""
        r = self.run("cat-file", "blob", sha, text=False)
        if r.returncode:
            raise GitError(f"git cat-file: exit {r.returncode}")
        return r.stdout.decode("utf-8")

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
        repo.git("clean", "-fdxq", "--", NOTES_DIR)
        result = _Import(conn, repo).run()
    if any(result):
        log.info("git sync: %d note(s) pulled, %d conflict(s)", *result)
    return result


def export(conn, *, idle=IDLE):
    """Write notes changed here (and idle for `idle`, and not open in an editor) to the clone,
    commit once, push. -> number of notes written or removed."""
    Repo(conn)
    with _locked():
        pushed = _Export(conn, ensure_clone(conn), idle).run()
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
    summary = f"Viimane muudatus {'.'.join(reversed(day.split('-')))}: {subject}"
    if stuck := problems(conn):
        summary += f". Sünkroonimata: {len(stuck)} ({', '.join(stuck[:3])}{', …' if len(stuck) > 3 else ''})"
    return {"ok": True, "summary": summary, "error": None}


def history(conn, note, limit=50):
    """[{sha, at, message, path}] newest first: the commits that changed the note's file, following
    moves and renames ("Ajalugu", docs/MARKMED-PLAN.md 17). [] before its first sync. Plain reads
    of committed objects, so no lock: a read that meets a pull's reset fails with GitError and the
    panel asks to try again. NotConnected, GitError."""
    if not note["file_path"]:
        return []
    repo = Repo(conn)
    if not (repo.path / ".git").is_dir():
        return []
    out = repo.git("log", f"-n{limit}", "--follow", "--diff-filter=d", "--name-only",
                   "--format=%x00%H%x1f%cI%x1f%s", "HEAD", "--", note["file_path"])
    versions = []
    for chunk in out.split("\0")[1:]:
        head, _, rest = chunk.partition("\n")
        sha, at, message = head.split("\x1f", 2)
        path = next((line for line in rest.splitlines() if line.strip()), note["file_path"])
        versions.append({"sha": sha, "at": util.iso(util.parse_iso(at)), "message": message, "path": path})
    return versions


def version(conn, note, sha):
    """The note's body as it was in commit sha (one of history()'s), or None."""
    found = next((v for v in history(conn, note, limit=200) if v["sha"] == sha), None)
    if found is None:
        return None
    _, body = linkfmt.from_file(Repo(conn).git("show", f"{sha}:{found['path']}"))
    return body


def problems(conn):
    """Titles (or paths) of notes the last pull or export could not sync, logged each time."""
    rows = conn.execute("SELECT value FROM settings WHERE key IN (?, ?)", (PROBLEMS_KEY + "pull", PROBLEMS_KEY + "export"))
    return sorted({title for r in rows for title in json.loads(r["value"])})


connections.register("brain", "Brain repo", order=40, test=test_connection, secret_names=("brain_token",))


class _Import:
    def __init__(self, conn, repo):
        self.conn, self.repo, self.state = conn, repo, _load_state(conn)
        self.problems, self.failed, self.held = set(), set(), set()

    def run(self):
        conn = self.conn
        tree = _tree(self.repo)
        rows = conn.execute("SELECT id, file_path, deleted_at FROM notes WHERE file_path IS NOT NULL").fetchall()
        live = {r["file_path"]: r["id"] for r in rows if r["deleted_at"] is None}
        dead = {r["file_path"]: r["id"] for r in rows if r["deleted_at"] is not None}
        self.synced_path = {r["id"]: r["file_path"] for r in rows}  # where each last-synced file was
        changed = []
        for path, blob in sorted(tree.items()):
            known = live.get(path, dead.get(path))
            if known is not None and (self.state.get(known) or {}).get("blob") == blob:
                continue  # unchanged since the last sync (a deleted note's file: the export removes it)
            try:
                changed.append((path, blob, *linkfmt.from_file(self.repo.blob(blob))))
            except Exception as exc:
                self._problem(path, known, exc)
        moved = self._moved(changed, live, tree)
        claims = self._claims(changed, live, dead, tree, set(moved))
        changed = [c for c in changed if c[0] not in self.failed]
        owners = {**live, **moved}
        results = [self._attempt(path, live.get(path, dead.get(path)), self._file, path, blob, meta, body,
                                 owners.get(path) or claims.get(path), dead.get(path), live.get(path))
                   for path, blob, meta, body in changed]
        keep = set(claims.values()) | set(moved.values()) | self.held
        for path, note_id in live.items():
            if path not in tree and note_id not in keep:
                results.append(self._attempt(path, note_id, self._removed, note_id))
        _save_problems(conn, "pull", self.problems)
        return results.count("pulled"), results.count("conflict")

    def _attempt(self, path, note_id, fn, *args):
        try:
            return fn(*args)
        except (notes.NoteBusy, _Later):
            pass  # open in an editor, or changed while we read it: the next run tries again
        except Exception as exc:
            self._problem(path, note_id, exc)
        self.state = _load_state(self.conn)  # the transaction rolled back
        return None

    def _problem(self, path, note_id, exc):
        log.warning("git sync: skipped %s (%s: %s)", path, type(exc).__name__, exc)
        self.failed.add(path)
        row = self.conn.execute("SELECT title FROM notes WHERE id = ?", (note_id,)).fetchone() if note_id else None
        self.problems.add(row["title"] if row else path)

    def _moved(self, changed, live, tree):
        """{path: note id} for changed files whose frontmatter id names another live note than
        the one recorded at that path, when that note's own file is gone or went to yet another
        note: files swapped on the laptop, or a run that pushed a move and died before recording
        it. The id wins over the path."""
        ids = {path: _meta_id(meta) for path, _blob, meta, _body in changed}
        moved = {}
        for path, _blob, _meta, _body in changed:
            other = ids[path]
            if path not in live or other in (None, live[path]):
                continue
            try:
                note = notes.get(self.conn, other)
            except Exception as exc:
                self._problem(path, live[path], exc)
                continue
            own = note["file_path"] if note is not None else None
            if own is not None and (own not in tree or own in live and ids.get(own) not in (None, other)):
                moved[path] = other
        return moved

    def _claims(self, changed, live, dead, tree, taken):
        """{path: note id} for new files that belong to an existing note: the note their
        frontmatter id names when its own file is gone (renamed or moved on the laptop), else a
        note never exported, whose file this would be. When several files name one note (renamed
        and copied), the one whose body is the last synced one gets it, or none does. A file this
        fails for is a problem of its own; the others go on. taken: paths that went to another note
        by id, so their recorded owner counts as having lost its file."""
        claims, by_id = {}, {}
        for path, _blob, meta, body in changed:
            if path in live:
                continue
            try:
                note_id = _meta_id(meta)
                note = notes.get(self.conn, note_id) if note_id else None
                # Its file is gone: not in git any more, gone to another note by id, or detached by
                # an import that failed after that (no path, but still sync state).
                lost = (note["file_path"] not in tree or note["file_path"] in taken
                        if note is not None and note["file_path"] is not None
                        else note is not None and note["id"] in self.state)
                if lost:
                    by_id.setdefault(note["id"], []).append((path, body))
                    continue
                row = self.conn.execute("SELECT id, slug, folder FROM notes WHERE file_path IS NULL "
                                        "AND deleted_at IS NULL AND slug = ?", (_stem(path),)).fetchone()
                if row is not None and _path(row) == path:
                    claims[path] = row["id"]
            except Exception as exc:
                self._problem(path, dead.get(path), exc)
        for note_id, files in by_id.items():
            if len(files) == 1:
                claims[files[0][0]] = note_id
                continue
            try:
                base = self._base(note_id)
            except Exception as exc:
                self.held.add(note_id)  # its file may be one of these: not a delete
                for path, _body in files:
                    self._problem(path, note_id, exc)
                continue
            same = [path for path, body in files if base and body == base[1]]
            if same:
                claims[same[0]] = note_id
        return claims

    def _base(self, note_id):
        """(meta, body) of the note's file as last synced, or None."""
        blob = (self.state.get(note_id) or {}).get("blob")
        return linkfmt.from_file(self.repo.blob(blob)) if blob else None

    def _file(self, path, blob, meta, body, note_id, dead_id, owner_id):
        base = self._base(note_id) if note_id else None
        with tx(self.conn):
            if dead_id is not None:
                self._forget(dead_id)  # deleted here but edited there: the edit comes back as a new note
            if owner_id not in (None, note_id):  # the file went to another note by id: detach it
                self.conn.execute("UPDATE notes SET file_path = NULL WHERE id = ? AND file_path = ?", (owner_id, path))
            if note_id is None:
                return self._create(path, blob, meta, body)
            # Read again inside the transaction: a task tick, "Seo" or AI write may have landed since.
            note = notes.get(self.conn, note_id)
            if note is None:
                raise _Later
            return self._changed(note, path, blob, meta, body, base)

    def _changed(self, note, path, blob, meta, body, base):
        conn, st = self.conn, self.state.get(note["id"]) or {}
        base_meta, base_body = base or (None, None)
        base_fields = _file_fields(base_meta, self.synced_path.get(note["id"]) or path) if base else None
        theirs, ours, our_body = _file_fields(meta, path), _note_fields(note), note["body"]
        body_dirty = note["rev"] > note["exported_rev"]
        if body != our_body and body_dirty and body != base_body:
            self._conflict(note, body, theirs)
            conn.execute("UPDATE notes SET file_path = ? WHERE id = ?", (path, note["id"]))
            self._mark(note["id"], blob, st.get("at", ""))
            return "conflict"
        if body != our_body and not body_dirty:
            notes.write_body(conn, note["id"], body)  # NoteBusy: the whole file waits
        for field, value in theirs.items():
            if base_fields and value != ours[field] and value != base_fields[field] and ours[field] == base_fields[field]:
                _set_field(conn, note["id"], field, value)
        note = notes.get(conn, note["id"])
        in_sync = note["body"] == body
        conn.execute("UPDATE notes SET file_path = ?, exported_rev = CASE WHEN ? THEN rev ELSE exported_rev END "
                     "WHERE id = ?", (path, in_sync, note["id"]))
        # When the database now holds exactly the file, the file needs no rewrite; otherwise the
        # old "at" makes the export write the merged note.
        same = in_sync and _note_fields(note) == theirs
        if _meta_id(meta) not in (None, note["id"]):
            same, st = False, {}  # the path won over a stale id: the export rewrites it with the right one
        self._mark(note["id"], blob, note["updated_at"] if same else st.get("at", ""))
        return "pulled" if note["body"] != our_body or _note_fields(note) != ours else None

    def _conflict(self, note, body, theirs):
        title = notes.derive_title(body)
        copy = notes.create(self.conn, f"{title} (konflikt)", folder=theirs["folder"],
                            body=_retitle(body, " (konflikt)"))
        notes.set_tags(self.conn, copy["id"], theirs["tags"])
        log.warning("git sync: note %s changed on both sides; the file's version is in %s", note["slug"], copy["slug"])

    def _create(self, path, blob, meta, body):
        # With a "# " heading in the body the title comes from it, so the file name can give the
        # slug and the file keeps its name; without one, create() puts the title on top.
        title = _stem(path) if _h1_line(body) is not None else str(meta.get("title") or _stem(path))
        fields = _file_fields(meta, path)
        note = notes.create(self.conn, title, folder=fields["folder"], body=body)
        for field in ("tags", "aliases", "extra"):
            if fields[field]:
                _set_field(self.conn, note["id"], field, fields[field])
        self.conn.execute("UPDATE notes SET file_path = ?, exported_rev = rev WHERE id = ?", (path, note["id"]))
        self._mark(note["id"], blob, "")  # "at" empty: the export rewrites the file once, with its id
        return "pulled"

    def _removed(self, note_id):
        with tx(self.conn):
            note = notes.get(self.conn, note_id)
            st = self.state.get(note_id) or {}
            if note is None or note["rev"] > note["exported_rev"] or note["updated_at"] > st.get("at", ""):
                self._forget(note_id)  # changed here since: kept, and the export writes the file again
                return None
            if collab.is_busy(self.conn, note_id):
                raise _Later
            notes.soft_delete(self.conn, note_id)
            self._forget(note_id)
            return "pulled"

    def _mark(self, note_id, blob, at):
        _set_state(self.conn, note_id, blob, at)
        self.state[note_id] = {"blob": blob, "at": at}

    def _forget(self, note_id):
        self.conn.execute("UPDATE notes SET file_path = NULL WHERE id = ?", (note_id,))
        self.conn.execute("DELETE FROM settings WHERE key = ?", (f"{STATE_PREFIX}{note_id}",))
        self.state.pop(note_id, None)


@dataclass
class _Plan:
    """What one export run does for one note."""
    note: object
    target: str | None  # None for a deleted note
    old: str | None     # the file it gives up (moved, renamed or deleted)
    blob: str = ""
    text: str = ""
    done: bool = False
    changed: bool = False
    giver: object = None     # the plan whose old path this one takes over
    written: list = field(default_factory=list)
    gave_up: bool = False


class _Export:
    def __init__(self, conn, repo, idle):
        self.conn, self.repo, self.state = conn, repo, _load_state(conn)
        self.cutoff = util.iso(util.now_utc() - idle)
        self.problems, self.skipped, self.staged = set(), set(), set()

    def run(self):
        conn, repo = self.conn, self.repo
        listing = _ls_tree(repo)
        self.head = _tree(repo, listing)
        self.modes = {path: mode for path, (mode, _sha) in listing.items()}
        rows = conn.execute("SELECT * FROM notes WHERE deleted_at IS NULL OR file_path IS NOT NULL").fetchall()
        live = [r for r in rows if r["deleted_at"] is None]
        folders = {r["slug"]: r["folder"] for r in live}
        self.basename_taken = {slug: folders[slug] for slug in self._taken(live)}
        plans = [p for p in (self._attempt(note, self._plan, note) for note in rows) if p]
        givers = {p.old: p for p in plans if p.old}
        for plan in plans:
            self._attempt(plan.note, self._write, plan, givers)
        self._settle(plans)
        taken = {p.target for p in plans if p.done and p.target}
        titles = [p.note["title"] + (" (deleted)" if p.target is None else "") for p in plans if p.done and p.changed]
        if repo.run("diff", "--cached", "--quiet").returncode:
            repo.git("commit", "--quiet", "-m", _message(titles))
        branch = repo.branch()
        try:
            if int(repo.git("rev-list", "--count", f"origin/{branch}..HEAD")):
                self._push(branch, {p.old for p in plans if p.done and p.old})
        except GitError:
            self.problems.update(titles or ["push ebaõnnestus"])
            _save_problems(conn, "export", self._with_skipped())
            raise
        after = _tree(repo)
        with tx(conn):
            for plan in plans:
                note = plan.note
                if not plan.done:
                    continue
                if plan.target is None and (plan.old is None or plan.old not in after or plan.old in taken):
                    conn.execute("UPDATE notes SET file_path = NULL WHERE id = ?", (note["id"],))
                    conn.execute("DELETE FROM settings WHERE key = ?", (f"{STATE_PREFIX}{note['id']}",))
                elif plan.target is not None and plan.target in after:
                    conn.execute("UPDATE notes SET exported_rev = ?, file_path = ? WHERE id = ?",
                                 (note["rev"], plan.target, note["id"]))
                    _set_state(conn, note["id"], plan.blob, note["updated_at"])
                else:
                    log.warning("git sync: %s did not reach the commit", plan.target or plan.old)
                    self.problems.add(note["title"])
            _save_problems(conn, "export", self._with_skipped())
        return len(titles)

    def _attempt(self, note, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            log.warning("git sync: note %s not exported (%s: %s)", note["slug"], type(exc).__name__, exc)
            self.problems.add(note["title"])
            return None

    def _with_skipped(self):
        """This run's problems, plus the earlier ones of notes this run did not try (busy, not idle)."""
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (PROBLEMS_KEY + "export",)).fetchone()
        earlier = set(json.loads(row["value"])) if row else set()
        return self.problems | (earlier & self.skipped)

    def _plan(self, note):
        st, fp = self.state.get(note["id"]) or {}, note["file_path"]
        # A file in git that is not the version last synced changed there, and the pull has not
        # taken it in (yet, or it failed): never overwrite or delete it. For a live note a file
        # gone from git is such a change too (a delete the pull kept waiting).
        if fp and (fp in self.head and self.head[fp] != st.get("blob") or fp not in self.head and not note["deleted_at"]):
            return None
        old = fp if fp in self.modes else None
        if old:
            self._safe(old)
        if note["deleted_at"] is not None:
            return _Plan(note, None, old, done=True)
        target = _path(note)
        if (st and note["rev"] <= note["exported_rev"] and note["updated_at"] <= st["at"] and fp == target):
            return None
        if note["updated_at"] > self.cutoff or collab.is_busy(self.conn, note["id"]):
            self.skipped.add(note["title"])
            return None
        if any(_refused_name(part) for part in target.split("/")):
            raise ValueError(f"git or the remote refuses the name {target!r}")
        self._safe(target)
        text = linkfmt.to_file(note, note["body"], basename_taken=self.basename_taken)
        return _Plan(note, target, old if old != target else None, _blob_id(text), text)

    def _write(self, plan, givers):
        note, target = plan.note, plan.target
        if target is None:
            return
        if target != note["file_path"] and target in self.modes:
            # Another note's file: taken over only from a note that gives it up this run (_settle
            # takes it back if that note does not get through).
            plan.giver = givers.get(target)
            if plan.giver is None or plan.giver is plan:
                raise ValueError(f"{target} is another file in git")  # e.g. a laptop file not imported
        written = []
        # Compared with HEAD, not the working tree: a file a failed run left behind still needs adding.
        if self.head.get(target) != plan.blob:
            file = self._safe(target)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(plan.text, encoding="utf-8")
            written.append(target)
        images = self._copy_images(note["body"])
        self._stage(written + images, [])
        self.staged.update(images)
        plan.written, plan.done, plan.changed = written, True, bool(written or images) or plan.old is not None

    def _settle(self, plans):
        """A note that took over a path keeps it only if the note giving the path up got through; a
        path nobody writes this run is deleted, never left without an owner (the next pull would
        import it as a new note); a note whose old file cannot be removed takes its new one back.
        Repeats, as taking one note back can leave another without its giver (chains, swaps)."""
        progress = True
        while progress:
            progress = False
            for plan in plans:
                if plan.done and plan.giver is not None and not plan.giver.done:
                    self._undo(plan, "the note giving up its path did not get through")
                    progress = True
            taken = {p.target for p in plans if p.done and p.target}
            for plan in plans:
                if plan.done and plan.old and not plan.gave_up and plan.old not in taken:
                    progress = True
                    try:
                        self._give_up(plan)
                    except Exception as exc:
                        self._undo(plan, exc)

    def _give_up(self, plan):
        plan.gave_up = True
        self._safe(plan.old).unlink(missing_ok=True)
        self._stage([], [plan.old])
        plan.changed = True

    def _undo(self, plan, reason):
        """Take one note's changes back out of this commit; the note tries again next run."""
        log.warning("git sync: note %s not exported (%s)", plan.note["slug"], reason)
        self.problems.add(plan.note["title"])
        paths = plan.written + ([plan.old] if plan.gave_up else [])
        if paths:
            self.repo.run("--literal-pathspecs", "reset", "-q", "--", *paths)
            for rel in paths:
                if rel in self.modes:
                    self.repo.run("--literal-pathspecs", "checkout", "-q", "--", rel)
                else:
                    self._safe(rel).unlink(missing_ok=True)
        plan.done = plan.gave_up = False
        plan.written = []

    def _stage(self, written, removed):
        """Stage one note's paths; on failure none of them, so one bad note never blocks the rest."""
        paths = written + removed
        if not paths:
            return
        try:
            # -f: a .gitignore in the brain repo must not keep a note out of the commit.
            if written:
                self.repo.git("--literal-pathspecs", "add", "-f", "--", *written)
            if removed:
                self.repo.git("--literal-pathspecs", "rm", "-q", "--cached", "--ignore-unmatch", "--", *removed)
        except GitError:
            self.repo.run("--literal-pathspecs", "reset", "-q", "--", *paths)
            raise

    def _push(self, branch, moved):
        repo = self.repo
        if not repo.run("push", "--quiet", "origin", f"HEAD:{branch}").returncode:
            return
        # Rejected, most likely because the laptop pushed meanwhile. A laptop change to a file that
        # moved or went away this run belongs to the note that had it, and a rebase would hand it to
        # the new owner: drop this run instead (the next pull takes the change in, the next export
        # plans again).
        repo.git("fetch", "--quiet", "origin")
        if moved & set(repo.git("diff", "--name-only", "--no-renames", "-z", f"HEAD...origin/{branch}").split("\0")):
            raise GitError("git push: the laptop changed a file that moved this run; the next run retries")
        # Otherwise rebase onto it and retry once. A rebase conflict is a true conflict; the next
        # pull drops our commit and sorts it out.
        r = repo.run("pull", "--rebase", "--quiet", "origin", branch)
        if r.returncode:
            repo.run("rebase", "--abort")
            raise repo.error(r, "pull --rebase")
        repo.git("push", "--quiet", "origin", f"HEAD:{branch}")

    def _safe(self, rel):
        """The working-tree file for a repo path; ValueError when it is a symlink or would land
        outside notes/."""
        file = self.repo.path / rel
        if file.is_symlink() or not file.resolve().is_relative_to(self.repo.path.resolve() / NOTES_DIR):
            raise ValueError(f"{rel} is outside notes/")
        return file

    def _copy_images(self, body):
        """Repo paths of the images this note brings that git does not have yet."""
        copied = []
        for name in linkfmt.image_names(body):
            src, rel = data_dir() / "pildid" / name, f"{IMAGES_DIR}/{name}"
            dst = self._safe(rel)
            if src.is_file() and rel not in self.modes and rel not in self.staged:  # staged by an earlier note
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                copied.append(rel)
        return copied

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


def _refused_name(part):
    """A path part git refuses to store or a remote's fsck rejects: anything starting with .git
    (.gitmodules, .gitattributes, in any case, with trailing dots or spaces), NTFS short names
    (gitmod~1, gi7eba~1) and names with invisible format characters (.git + zero-width joiner)."""
    if any(unicodedata.category(c) == "Cf" for c in part):
        return True
    name = part.rstrip(". ").casefold()
    return name.startswith(".git") or re.match(r"\w{1,6}~\d", name) is not None


def _meta_id(meta):
    """A frontmatter id that can name a note: a positive integer SQLite can hold, else None."""
    value = meta.get("id")
    if isinstance(value, str) and value.strip().isdecimal() and value.strip().isascii():
        value = int(value)
    return value if type(value) is int and 0 < value < 2 ** 63 else None


def _ls_tree(repo):
    """{path: (mode, blob)} of every file under notes/ in HEAD."""
    listing = {}
    for entry in repo.git("ls-tree", "-r", "-z", "HEAD", "--", NOTES_DIR).split("\0"):
        if entry:
            info, path = entry.split("\t", 1)
            mode, kind, sha = info.split()
            if kind == "blob":
                listing[path] = (mode, sha)
    return listing


def _tree(repo, listing=None):
    """{path: blob} of note files in HEAD: regular .md files under notes/."""
    listing = _ls_tree(repo) if listing is None else listing
    return {path: sha for path, (mode, sha) in listing.items() if mode in REGULAR and path.endswith(".md")}


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
    if not titles:
        return "markmik: sync"
    head = f"markmik: {len(titles)} note{'' if len(titles) == 1 else 's'}"
    return head + "\n\n" + "\n".join(f"- {t}" for t in titles)


def _save_problems(conn, phase, titles):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (PROBLEMS_KEY + phase, json.dumps(sorted(titles), ensure_ascii=False)))


def _load_state(conn):
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE ?", (STATE_PREFIX + "%",))
    return {int(r["key"][len(STATE_PREFIX):]): json.loads(r["value"]) for r in rows}


def _set_state(conn, note_id, blob, at):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (f"{STATE_PREFIX}{note_id}", json.dumps({"blob": blob, "at": at})))


@contextmanager
def _locked():
    """One sync at a time across the web process and the worker."""
    path = data_dir() / "brain.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
