"""STUB (Wave 0). WP-B replaces this whole file; signatures in docs/CONTRACTS.md §4 notes.
Just enough for other packages to build and test against."""

from .util import now_iso, slugify


class NoteBusy(Exception):
    pass


def create(conn, title="", *, folder="", body=None, is_daily=False, daily_date=None):
    taken = {r["slug"] for r in conn.execute("SELECT slug FROM notes")}
    slug = slugify(title or "marge", taken)
    conn.execute(
        "INSERT INTO notes (slug, title, body, folder, is_daily, daily_date) VALUES (?, ?, ?, ?, ?, ?)",
        (slug, title, f"# {title}\n" if body is None else body, folder, int(is_daily), daily_date),
    )
    return by_slug(conn, slug)


def get(conn, note_id):
    return conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()


def by_slug(conn, slug):
    return conn.execute("SELECT * FROM notes WHERE slug = ? AND deleted_at IS NULL", (slug,)).fetchone()


def write_body(conn, note_id, body):
    from . import collab
    if collab.is_busy(conn, note_id):
        raise NoteBusy
    conn.execute("UPDATE notes SET body = ?, rev = rev + 1, updated_at = ? WHERE id = ?", (body, now_iso(), note_id))
    on_saved(conn, note_id)
    return get(conn, note_id)["rev"]


def on_saved(conn, note_id):
    pass


def daily(conn, day):
    row = conn.execute("SELECT * FROM notes WHERE daily_date = ? AND deleted_at IS NULL", (day.isoformat(),)).fetchone()
    return row or create(conn, day.isoformat(), folder="paevik", is_daily=True, daily_date=day.isoformat())
