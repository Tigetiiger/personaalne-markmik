"""Nightly SQLite backup to <DATA_DIR>/backups/markmik-YYYY-MM-DD.db, the 14 newest kept, and a
copy of each new data file in <DATA_DIR>/backups/failid. docs/PLAN-OVERVIEW.md §11,
docs/FAILID-PLAN.md call 4."""

import os
import shutil
import sqlite3
from pathlib import Path

from . import failid
from .jobs import job
from .util import local_today

KEEP = 14


@job("backup", at=("03:30",))
def backup(conn):
    data = Path(os.environ.get("DATA_DIR", "data"))
    folder = data / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"markmik-{local_today().isoformat()}.db"
    # Written aside and renamed, so a crash never leaves a half backup under a real name.
    partial = folder / (target.name + ".partial")
    partial.unlink(missing_ok=True)
    dest = sqlite3.connect(partial)
    try:
        conn.backup(dest)  # online backup: a consistent copy while the app keeps writing
    finally:
        dest.close()
    partial.replace(target)
    for old in sorted(folder.glob("markmik-*.db"))[:-KEEP]:
        old.unlink()
    copy_files(data / "failid", folder / "failid")
    return target


def copy_files(source, dest):
    """Data files are on the server only (the note's Markdown carries just the link), so the
    database copy alone would leave links dangling. Names are content hashes: a name already
    copied is the same bytes, and a file the clean-up deletes stays here. This is the same disk:
    it guards against a deleted or damaged file, not a lost disk (the VM snapshots do that)."""
    if not source.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if not failid.NAME.match(path.name) or (dest / path.name).exists():
            continue
        partial = dest / (path.name + ".partial")
        shutil.copyfile(path, partial)
        partial.replace(dest / path.name)
