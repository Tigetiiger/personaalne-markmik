"""Import of an existing collection (docs/MEEDIA-PLAN.md, call 10)."""

import json

from app.meedia import importer, store
from tests.meedia.samples import heic, jpeg, video


def test_import_walks_subfolders_converts_and_skips_duplicates(conn, root, tmp_path):
    src = tmp_path / "kogu"
    (src / "2019" / "suvi").mkdir(parents=True)
    (src / ".peidus").mkdir()
    jpeg(src / "a.jpg", color=(1, 2, 3))
    jpeg(src / "2019" / "suvi" / "a.jpg", color=(1, 2, 3))  # same content again
    heic(src / "2019" / "b.HEIC")
    video(src / "2019" / "suvi" / "c.mov", codec="libx265", seconds=1)
    (src / "2019" / "katki.png").write_bytes(b"x")
    (src / "loe.txt").write_text("x")
    jpeg(src / ".peidus" / "d.jpg")
    lines = []
    counts = importer.import_tree(conn, root, src, say=lines.append)
    assert counts == {"taken": 3, "skipped": 1, "failed": 1}
    names = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
    assert names == ["a.jpg", "a.jpg.json", "b.jpg", "b.jpg.json", "c.mp4", "c.mp4.json"]
    assert json.loads(store.companion_path(root, "c.mp4").read_text())["source"] == "import"
    assert list((root / store.TRASH).iterdir()) == []  # the collection keeps the originals
    assert list((root / store.INCOMING).iterdir()) == []
    assert (src / "2019" / "b.HEIC").exists()
    assert any(line.startswith("VIGA") for line in lines)
    assert importer.import_tree(conn, root, src, say=lines.append) == {"taken": 0, "skipped": 4, "failed": 1}


def test_import_with_move_empties_the_collection_of_what_it_kept(conn, root, tmp_path):
    src = tmp_path / "kogu"
    src.mkdir()
    jpeg(src / "a.jpg")
    (src / "katki.jpg").write_bytes(b"x")
    importer.import_tree(conn, root, src, move=True, say=lambda _: None)
    assert sorted(p.name for p in src.iterdir()) == ["katki.jpg"]


def test_collection_under_incoming_is_imported_but_the_roots_own_files_are_not(conn, root):
    jpeg(root / "olemas.jpg", color=(5, 5, 5))
    src = root / store.INCOMING / "kogu"
    src.mkdir()
    jpeg(src / "uus.jpg", color=(6, 6, 6))
    assert importer.import_tree(conn, root, root, say=lambda _: None) == {"taken": 0, "skipped": 0, "failed": 0}
    assert importer.import_tree(conn, root, src, move=True, say=lambda _: None)["taken"] == 1
    assert (root / "uus.jpg").exists() and list(src.iterdir()) == []
