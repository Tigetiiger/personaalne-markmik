"""Make the Mouser order template once (docs/ARVUTUS-PLAN.md): `python -m app.arvuta.mall` in the web
container. It is a note like any other afterwards: the owner may change or delete it, and this is
never run again by itself."""

import json
import os
import pathlib
import sys

from .. import db, notes

TITLE = "Mouser tellimus"
FOLDER = "Mallid"
BODY = (pathlib.Path(__file__).parent / "mouser_mall.md").read_text()


def make(conn):
    """The template note, made unless a note with its title is already in Mallid. (note, made)."""
    found = conn.execute("SELECT * FROM notes WHERE title = ? AND folder = ? AND deleted_at IS NULL",
                         (TITLE, FOLDER)).fetchone()
    if found:
        return found, False
    note = notes.create(conn, TITLE, folder=FOLDER, body=BODY)
    conn.execute("UPDATE notes SET frontmatter_extra = ? WHERE id = ?", (json.dumps({"mall_kaust": "tellimused"}), note["id"]))
    return notes.get(conn, note["id"]), True


if __name__ == "__main__":
    conn = db.connect(os.environ.get("DATABASE_PATH", "data/markmik.db"))
    note, made = make(conn)
    print(f"{'Tehtud' if made else 'Juba olemas'}: /m/{note['slug']}")
    sys.exit(0)
