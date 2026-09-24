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
STATE_PREFIX = "gitsync:note:"
PROBLEMS_KEY = "gitsync:problems:"  # + "pull" / "export": json list of titles that did not sync
REGULAR = ("100644", "100755")  # tree modes of plain files; symlinks and submodules are not notes
IDENTITY = {"GIT_AUTHOR_NAME": "Märkmik", "GIT_AUTHOR_EMAIL": "markmik@localhost",
            "GIT_COMMITTER_NAME": "Märkmik", "GIT_COMMITTER_EMAIL": "markmik@localhost"}
_H1_RE = re.compile(r" {0,3}#[ \t]+\S")
# Path parts git refuses to store (.git in any case, and its NTFS short and dotted forms).
_GIT_NAME_RE = re.compile(r"(\.git|git~1)[. ]*$", re.I)


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


def problems(conn):
    """Titles (or paths) of notes the last pull or export could not sync, logged each time."""
    rows = conn.execute("SELECT value FROM settings WHERE key IN (?, ?)", (PROBLEMS_KEY + "pull", PROBLEMS_KEY + "export"))
    return sorted({title for r in rows for title in json.loads(r["value"])})


connections.register("brain", "Brain repo", order=40, test=test_connection, secret_names=("brain_token",))


class _Import:
    def __init__(self, conn, repo):
        self.conn, self.repo, self.state = conn, repo, _load_state(conn)
        self.problems = set()

    def run(self):
        conn = self.conn
        tree = _tree(self.repo)
        rows = conn.execute("SELECT id, file_path, deleted_at FROM notes WHERE file_path IS NOT NULL").fetchall()
        live = {r["file_path"]: r["id"] for r in rows if r["deleted_at"] is None}
        dead = {r["file_path"]: r["id"] for r in rows if r["deleted_at"] is not None}
        changed = []
        for path, blob in sorted(tree.items()):
            known = live.get(path, dead.get(path))
            if known is not None and (self.state.get(known) or {}).get("blob") == blob:
                continue  # unchanged since the last sync (a deleted note's file: the export removes it)
            try:
                changed.append((path, blob, *linkfmt.from_file(self.repo.blob(blob))))
            except Exception as exc:
                self._problem(path, known, exc)
        claims = self._claims(changed, live, tree)
        results = [self._attempt(path, live.get(path, dead.get(path)), self._file, path, blob, meta, body,
                                 live.get(path) or claims.get(path), dead.get(path))
                   for path, blob, meta, body in changed]
        for path, note_id in live.items():
            if path not in tree and note_id not in claims.values():
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
        row = self.conn.execute("SELECT title FROM notes WHERE id = ?", (note_id,)).fetchone() if note_id else None
        self.problems.add(row["title"] if row else path)

    def _claims(self, changed, live, tree):
        """{path: note id} for new files that belong to an existing note: the note their
        frontmatter id names when its own file is gone (renamed or moved on the laptop), else a
        note never exported, whose file this would be. When several files name one note (renamed
        and copied), the one whose body is the last synced one gets it, or none does."""
        claims, by_id = {}, {}
        for path, _blob, meta, body in changed:
            if path in live:
                continue
            try:
                note = notes.get(self.conn, int(meta.get("id")))
            except (TypeError, ValueError):
                note = None
            if note is not None and note["file_path"] is not None and note["file_path"] not in tree:
                by_id.setdefault(note["id"], []).append((path, body))
                continue
            row = self.conn.execute("SELECT id, slug, folder FROM notes WHERE file_path IS NULL "
                                    "AND deleted_at IS NULL AND slug = ?", (_stem(path),)).fetchone()
            if row is not None and _path(row) == path:
                claims[path] = row["id"]
        for note_id, files in by_id.items():
            if len(files) == 1:
                claims[files[0][0]] = note_id
                continue
            base = self._base(note_id)
            same = [path for path, body in files if base and body == base[1]]
            if same:
                claims[same[0]] = note_id
        return claims

    def _base(self, note_id):
        """(meta, body) of the note's file as last synced, or None."""
        blob = (self.state.get(note_id) or {}).get("blob")
        return linkfmt.from_file(self.repo.blob(blob)) if blob else None

    def _file(self, path, blob, meta, body, note_id, dead_id):
        base = self._base(note_id) if note_id else None
        with tx(self.conn):
            if dead_id is not None:
                self._forget(dead_id)  # deleted here but edited there: the edit comes back as a new note
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
        base_fields = _file_fields(base_meta, note["file_path"]) if base else None
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


class _Export:
    def __init__(self, conn, repo, idle):
        self.conn, self.repo, self.state = conn, repo, _load_state(conn)
        self.cutoff = util.iso(util.now_utc() - idle)
        self.titles, self.marks, self.problems = [], [], set()

    def run(self):
        conn, repo = self.conn, self.repo
        listing = _ls_tree(repo)
        self.head = _tree(repo, listing)
        self.modes = {path: mode for path, (mode, _sha) in listing.items()}
        rows = conn.execute("SELECT * FROM notes WHERE deleted_at IS NULL OR file_path IS NOT NULL").fetchall()
        live = [r for r in rows if r["deleted_at"] is None]
        self.targets = {_path(r) for r in live}
        folders = {r["slug"]: r["folder"] for r in live}
        self.taken = {slug: folders[slug] for slug in self._taken(live)}
        for note in rows:
            try:
                self._note(note)
            except Exception as exc:
                log.warning("git sync: note %s not exported (%s: %s)", note["slug"], type(exc).__name__, exc)
                self.problems.add(note["title"])
        if repo.run("diff", "--cached", "--quiet").returncode:
            repo.git("commit", "--quiet", "-m", _message(self.titles))
        branch = repo.branch()
        if int(repo.git("rev-list", "--count", f"origin/{branch}..HEAD")):
            self._push(branch)
        after = _tree(repo)
        with tx(conn):
            for note, path, blob in self.marks:
                if path is None and (note["file_path"] not in after or note["file_path"] in self.targets):
                    conn.execute("UPDATE notes SET file_path = NULL WHERE id = ?", (note["id"],))
                    conn.execute("DELETE FROM settings WHERE key = ?", (f"{STATE_PREFIX}{note['id']}",))
                elif path is not None and path in after:
                    conn.execute("UPDATE notes SET exported_rev = ?, file_path = ? WHERE id = ?",
                                 (note["rev"], path, note["id"]))
                    _set_state(conn, note["id"], blob, note["updated_at"])
                else:
                    log.warning("git sync: %s did not reach the commit", path or note["file_path"])
                    self.problems.add(note["title"])
            _save_problems(conn, "export", self.problems)
        return len(self.titles)

    def _note(self, note):
        st, fp = self.state.get(note["id"]) or {}, note["file_path"]
        # A file in git that is not the version last synced changed there, and the pull has not
        # taken it in (yet, or it failed): never overwrite or delete it. For a live note a file
        # gone from git is such a change too (a delete the pull kept waiting).
        if fp and (fp in self.head and self.head[fp] != st.get("blob") or fp not in self.head and not note["deleted_at"]):
            return
        if note["deleted_at"] is not None:
            removed = self._remove(note["file_path"])
            self._stage([], removed)
            if removed:
                self.titles.append(f"{note['title']} (deleted)")
            self.marks.append((note, None, None))
            return
        target = _path(note)
        if (st and note["rev"] <= note["exported_rev"] and note["updated_at"] <= st["at"]
                and note["file_path"] == target):
            return
        if note["updated_at"] > self.cutoff or collab.is_busy(self.conn, note["id"]):
            return
        if any(_GIT_NAME_RE.match(part) for part in target.split("/")):
            raise ValueError(f"git does not store {target}")
        if target != note["file_path"] and target in self.modes:
            raise ValueError(f"{target} is another file in git")  # e.g. a laptop file not imported
        file = self._safe(target)
        text = linkfmt.to_file(note, note["body"], basename_taken=self.taken)
        blob, written = _blob_id(text), []
        # Compared with HEAD, not the working tree: a file a failed run left behind still needs adding.
        if self.head.get(target) != blob:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
            written.append(target)
        removed = self._remove(note["file_path"]) if note["file_path"] and note["file_path"] != target else []
        written += self._copy_images(note["body"])
        self._stage(written, removed)
        self.marks.append((note, target, blob))
        if written or removed:
            self.titles.append(note["title"])

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

    def _safe(self, rel):
        """The working-tree file for a repo path; ValueError when it is a symlink or would land
        outside notes/."""
        file = self.repo.path / rel
        if file.is_symlink() or not file.resolve().is_relative_to(self.repo.path.resolve() / NOTES_DIR):
            raise ValueError(f"{rel} is outside notes/")
        return file

    def _remove(self, rel):
        """[rel] after deleting a note's old file, or [] when there is none or another note's file
        lives there now."""
        if rel in self.targets or rel not in self.modes:
            return []
        self._safe(rel).unlink(missing_ok=True)
        return [rel]

    def _copy_images(self, body):
        """Repo paths of the images this note brings that git does not have yet."""
        copied = []
        for name in linkfmt.image_names(body):
            src, rel = data_dir() / "pildid" / name, f"{IMAGES_DIR}/{name}"
            dst = self._safe(rel)
            if src.is_file() and rel not in self.modes:
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
