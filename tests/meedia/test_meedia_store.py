"""The media folder and its index (docs/MEEDIA-PLAN.md, step 1): companion files are the truth."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.meedia import index, store


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "meedia"
    store.ensure(r)
    return r


def put(root, name, data=b"x" * 100):
    (root / name).write_bytes(data)
    return root / name


def edit_companion(root, name, **changes):
    """A person editing the JSON by hand. The mtime is moved on explicitly: two writes within one
    timer tick can get the same mtime."""
    path = store.companion_path(root, name)
    data = json.loads(path.read_text())
    data.update(changes)
    before = path.stat().st_mtime_ns
    path.write_text(json.dumps(data, ensure_ascii=False))
    os.utime(path, ns=(before + 10**9, before + 10**9))


def rows(conn):
    """Everything the index holds, without ids: comparable across a rebuild."""
    media = [{k: r[k] for k in r.keys() if k != "id"} for r in conn.execute("SELECT * FROM media ORDER BY name")]
    tags = [tuple(r) for r in conn.execute(
        "SELECT m.name, t.tag, t.auto FROM media_tags t JOIN media m ON m.id = t.media_id ORDER BY 1, 2")]
    return media, tags


def fts(conn, q):
    return [r[0] for r in conn.execute(
        "SELECT m.name FROM media_fts JOIN media m ON m.id = media_fts.rowid WHERE media_fts MATCH ? ORDER BY 1", (q,))]


def test_intake_writes_companion_in_key_order_and_indexes(conn, root):
    put(root, "IMG_1.jpg", b"abc")
    index.intake(conn, root, "IMG_1.jpg", source="upload")
    data = json.loads(store.companion_path(root, "IMG_1.jpg").read_text())
    assert list(data) == list(store.KEYS)
    assert data["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert (data["kind"], data["mime"], data["bytes"], data["source"]) == ("image", "image/jpeg", 3, "upload")
    row = conn.execute("SELECT * FROM media").fetchone()
    assert (row["name"], row["kind"], row["sha256"]) == ("IMG_1.jpg", "image", data["sha256"])


def test_hand_edit_is_picked_up_by_the_next_scan(conn, root):
    put(root, "koer.jpg")
    index.intake(conn, root, "koer.jpg", source="upload")
    edit_companion(root, "koer.jpg", tags=["Perekond", "#Sügis"], description="Koer pargis")
    assert index.scan(conn, root)["indexed"] == 1
    assert rows(conn)[1] == [("koer.jpg", "perekond", 0), ("koer.jpg", "sügis", 0)]
    assert fts(conn, "pargis") == ["koer.jpg"]
    assert fts(conn, "sugis") == ["koer.jpg"]  # diacritics fold
    assert index.scan(conn, root)["indexed"] == 0  # unchanged: not read again


def test_rebuilt_index_equals_the_old_one(conn, root):
    for n in ("a.jpg", "b.mp4", "c.png"):
        put(root, n, n.encode())
        index.intake(conn, root, n, source="folder")
    index.update(conn, root, "a.jpg", {"tags": ["mets"], "auto_tags": ["puu", "mets"], "title": "Mets"})
    index.update(conn, root, "b.mp4", {"auto_tags": ["meri"], "rejected_tags": ["meri"]})
    before = rows(conn)
    with conn:
        conn.execute("DELETE FROM media")
    index.reindex(conn, root)
    assert rows(conn) == before
    assert fts(conn, "mets") == ["a.jpg"]


def test_user_tags_beat_auto_tags_and_rejected_auto_tags_drop(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    index.update(conn, root, "x.jpg", {"tags": ["mets"], "auto_tags": ["Mets", "puu", "kivi"], "rejected_tags": ["kivi"]})
    assert rows(conn)[1] == [("x.jpg", "mets", 0), ("x.jpg", "puu", 1)]
    assert conn.execute("SELECT tags_text FROM media").fetchone()[0] == "mets puu"


def test_update_refuses_keys_the_file_owns(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    with pytest.raises(KeyError):
        index.update(conn, root, "x.jpg", {"sha256": "0" * 64})


def test_keys_added_by_hand_are_kept_after_the_known_ones(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    edit_companion(root, "x.jpg", minu_märge="alles")
    index.update(conn, root, "x.jpg", {"title": "Uus"})
    data = json.loads(store.companion_path(root, "x.jpg").read_text())
    assert data["minu_märge"] == "alles"
    assert list(data)[-1] == "minu_märge"


def test_a_copy_in_progress_is_taken_in_only_once_it_holds_still(conn, root):
    p = put(root, "suur.mov", b"1" * 10)
    assert index.scan(conn, root)["waiting"] == 1
    p.write_bytes(b"1" * 20)  # still growing
    assert index.scan(conn, root)["waiting"] == 1
    assert not store.companion_path(root, "suur.mov").exists()
    assert index.scan(conn, root)["taken"] == 1
    data = json.loads(store.companion_path(root, "suur.mov").read_text())
    assert (data["kind"], data["bytes"], data["source"]) == ("video", 20, "folder")
    assert conn.execute("SELECT count(*) FROM media_pending").fetchone()[0] == 0


def test_other_files_and_dot_files_are_not_media(conn, root):
    put(root, "märkmed.txt")
    put(root, ".DS_Store")
    (root / store.INCOMING / "pool.mp4").write_bytes(b"1")
    index.scan(conn, root)
    index.scan(conn, root)
    assert conn.execute("SELECT count(*) FROM media").fetchone()[0] == 0


def test_lost_companion_is_written_back_from_the_index(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    index.update(conn, root, "x.jpg", {"tags": ["mets"]})
    store.companion_path(root, "x.jpg").unlink()
    assert index.scan(conn, root)["restored"] == 1
    assert json.loads(store.companion_path(root, "x.jpg").read_text())["tags"] == ["mets"]


def test_row_goes_when_the_file_goes(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    (root / "x.jpg").unlink()
    assert index.scan(conn, root)["dropped"] == 1
    assert rows(conn) == ([], [])
    assert fts(conn, "x") == []


def test_broken_companion_keeps_the_old_row(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    index.update(conn, root, "x.jpg", {"tags": ["mets"]})
    path = store.companion_path(root, "x.jpg")
    path.write_text("{ katki")
    os.utime(path, ns=(1, 1))
    assert index.scan(conn, root)["indexed"] == 0
    assert rows(conn)[1] == [("x.jpg", "mets", 0)]
    assert index.scan(conn, root)["indexed"] == 0  # not read again until it changes


@pytest.mark.parametrize("original, expected", [
    ("IMG_1.JPG", "IMG_1.jpg"),
    ("../../etc/passwd.jpg", "passwd.jpg"),
    ("C:\\Users\\x\\pilt.png", "pilt.png"),
    (".peidus.jpg", "peidus.jpg"),
    ("sõber\x00.jpg", "sõber.jpg"),
    (".jpg", "jpg"),
])
def test_clean_name(original, expected):
    assert store.clean_name(original) == expected


def test_free_name_steps_past_files_and_companions(root):
    put(root, "a.jpg")
    store.companion_path(root, "a-2.jpg").write_text("{}")
    assert store.free_name(root, "a.jpg") == "a-3.jpg"
    assert store.free_name(root, "b.jpg") == "b.jpg"


def test_trash_keeps_both_files_and_purge_waits_thirty_days(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    then = datetime(2026, 10, 1, tzinfo=timezone.utc)
    store.move_to_trash(root, "x.jpg", now=then)
    conn.execute("DELETE FROM media")
    trash = sorted(p.name for p in (root / store.TRASH).iterdir())
    assert trash == ["20261001T000000Z_x.jpg", "20261001T000000Z_x.jpg.json"]
    (root / store.TRASH / "kasitsi.jpg").write_bytes(b"1")
    assert store.purge_trash(root, now=then + timedelta(days=29)) == []
    assert len(store.purge_trash(root, now=then + timedelta(days=31))) == 2
    assert [p.name for p in (root / store.TRASH).iterdir()] == ["kasitsi.jpg"]


def test_remove_moves_to_trash_and_drops_the_row(conn, root):
    put(root, "x.jpg")
    index.intake(conn, root, "x.jpg", source="upload")
    index.remove(conn, root, "x.jpg")
    assert rows(conn) == ([], [])
    assert not (root / "x.jpg").exists()
    assert len(list((root / store.TRASH).iterdir())) == 2
