"""Import an existing collection into the media folder (docs/MEEDIA-PLAN.md, call 10):
python -m app.meedia.importer <folder> [--move]   (inside the worker container)

Every picture and video under <folder>, subfolders too, goes through the same intake as an
upload. Content already kept (also as the original of a converted file) is skipped. Without
--move the collection is left as it was."""

import argparse
import os
import secrets
import shutil
from pathlib import Path

from ..db import connect, init_db
from . import index, probe, store


def import_tree(conn, root, source_dir, *, move=False, say=print):
    """Returns counts {taken, skipped, failed}."""
    store.ensure(root)
    counts = {"taken": 0, "skipped": 0, "failed": 0}
    for folder, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for file in sorted(files):
            src = Path(folder) / file
            # The root's own files are kept already; a collection under .saabumas is not.
            if not store.kind_of(file) or src.resolve().parent == root.resolve():
                continue
            same = index.find_duplicate(conn, store.sha256_of(src))
            if same:
                counts["skipped"] += 1
                say(f"sama juba olemas: {src} = {same}")
                if move:
                    src.unlink()
                continue
            # Copied into .saabumas first: a scan never sees half a file in the root.
            work = root / store.INCOMING / f"{secrets.token_hex(8)}.import"
            shutil.copy2(src, work)
            try:
                final = index.intake(conn, root, store.clean_name(file), source="import", path=work, trash_original=False)
            except probe.Unreadable as exc:
                work.unlink(missing_ok=True)
                counts["failed"] += 1
                say(f"VIGA {src}: {exc}")
                continue
            counts["taken"] += 1
            say(f"{src} -> {final}")
            if move:
                src.unlink()
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--move", action="store_true", help="delete each file from <folder> once it is kept")
    args = parser.parse_args()
    db_path = os.environ.get("DATABASE_PATH", "data/markmik.db")
    init_db(db_path)
    conn = connect(db_path)
    try:
        print(import_tree(conn, store.default_root(), args.folder, move=args.move))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
