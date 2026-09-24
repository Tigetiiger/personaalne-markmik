"""app/backup.py: nightly online backup, 14 newest kept."""

import sqlite3

from app import backup, notes


def test_backup_is_a_readable_copy_and_keeps_14(conn, tmp_path):
    notes.create(conn, "Varundatud")
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 20):
        (folder / f"markmik-2026-01-{day:02d}.db").write_text("vana")
    (folder / "muu.txt").write_text("ei puutu")
    target = backup.backup(conn)
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT title FROM notes").fetchall() == [("Varundatud",)]
    copy.close()
    kept = sorted(p.name for p in folder.glob("markmik-*.db"))
    assert len(kept) == 14 and kept[-1] == target.name and kept[0] == "markmik-2026-01-07.db"
    assert (folder / "muu.txt").exists() and not list(folder.glob("*.partial"))


def test_same_day_backup_is_replaced(conn, tmp_path):
    first = backup.backup(conn)
    notes.create(conn, "Hiljem")
    assert backup.backup(conn) == first
    copy = sqlite3.connect(first)
    assert copy.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
    copy.close()
