"""Nightly SQLite backup to <DATA_DIR>/backups/markmik-YYYY-MM-DD.db, the 14 newest kept.
docs/PLAN-OVERVIEW.md §11."""

import os
import sqlite3
from pathlib import Path

from .jobs import job
from .util import local_today

KEEP = 14


@job("backup", at=("03:30",))
def backup(conn):
    folder = Path(os.environ.get("DATA_DIR", "data")) / "backups"
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
    return target
