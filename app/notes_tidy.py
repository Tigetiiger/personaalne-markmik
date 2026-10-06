"""Once, for notes made before automatic slugs were marked (docs/MARKMED-PLAN.md 8): every note
still on a "marge" slug its title does not make gets the mark, then settles. A blank one goes into
the trash (it is already in git), a named one takes its title's slug.

    docker compose exec web python -m app.notes_tidy
"""

import os
import re

from . import db, notes, util


def run(conn):
    """[(old slug, new slug or None)] for every note it touched."""
    done = []
    rows = conn.execute("SELECT id, slug, title FROM notes WHERE deleted_at IS NULL AND slug GLOB 'marge*'").fetchall()
    for row in rows:
        if not re.fullmatch(r"marge(-\d+)?", row["slug"]) or util.slug_base(row["title"]) == "marge":
            continue
        conn.execute("INSERT OR IGNORE INTO note_auto_slug (note_id) VALUES (?)", (row["id"],))
        done.append((row["slug"], notes.settle(conn, row["id"], swept=True)))
    return done


if __name__ == "__main__":
    conn = db.connect(os.environ.get("DATABASE_PATH", "data/markmik.db"))
    for old, new in run(conn):
        print(f"{old} -> {new or 'prügikasti'}")
