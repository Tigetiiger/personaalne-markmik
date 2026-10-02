"""The Meedia page's API (docs/MEEDIA-PLAN.md, step 4): search, details, tags, files."""

import json
from urllib.parse import quote

import pytest

from app.meedia import index, search, store
from tests.meedia.samples import jpeg, video


@pytest.fixture
def media(app, conn):
    """Three kept files with dates, tags and text; returns the media root."""
    root = store.default_root()
    store.ensure(root)
    for name, color, when in (("koer pargis.jpg", (1, 1, 1), "2026:09:30 14:00:00"),
                              ("kass.jpg", (2, 2, 2), "2025:12:31 23:30:00"),
                              ("meri.mp4", None, None)):
        if color:
            jpeg(root / name, color=color, exif={0x9003: when, 0x9011: "+02:00"})
        else:
            video(root / name)
        index.intake(conn, root, name, source="upload")
    index.update(conn, root, "koer pargis.jpg", {"tags": ["koer"], "auto_tags": ["park", "sügis"], "ocr": "Kõik on hästi"})
    index.update(conn, root, "kass.jpg", {"auto_tags": ["kass", "talv"], "description": "Kass aknal"})
    return root


def names(r):
    assert r.status_code == 200, r.json
    return [i["name"] for i in r.json["items"]]


def test_newest_first_and_total(owner, media):
    r = owner.get("/api/meedia")
    assert r.json["total"] == 3
    assert names(r)[1:] == ["koer pargis.jpg", "kass.jpg"]  # the video has no date: added now


@pytest.mark.parametrize("query, expected", [
    ("q=koik", ["koer pargis.jpg"]),          # OCR text, diacritics folded
    ("q=aknal", ["kass.jpg"]),                # description
    ("q=pargi", ["koer pargis.jpg"]),         # prefix of the name
    ("q=%23talv", ["kass.jpg"]),              # #tag
    ("q=-%23talv", ["meri.mp4", "koer pargis.jpg"]),
    ("tag=park", ["koer pargis.jpg"]),
    ("ilma=koer&liik=image", ["kass.jpg"]),
    ("liik=video", ["meri.mp4"]),
    ("sildita=1", ["meri.mp4"]),
    ("alates=2026-01-01&kuni=2026-09-30", ["koer pargis.jpg"]),
    ("alates=2025-12-31&kuni=2025-12-31", ["kass.jpg"]),  # 23:30 +02:00 is still the 31st here
    ("q=NEAR%20*%22", []),                    # FTS syntax stays plain text
])
def test_search(owner, media, query, expected):
    assert names(owner.get(f"/api/meedia?{query}")) == expected


def test_paging_and_bad_date(owner, media):
    assert len(names(owner.get("/api/meedia?limit=2&offset=2"))) == 1
    assert owner.get("/api/meedia?alates=eile").status_code == 400


def test_parse():
    assert search.parse("koer -#Kass #park mets") == (["koer", "mets"], ["park"], ["kass"])


def test_detail_and_known_tags(owner, media):
    r = owner.get("/api/meedia/fail/" + quote("koer pargis.jpg"))
    assert r.json["tags"] == [{"tag": "koer", "auto": False}, {"tag": "park", "auto": True}, {"tag": "sügis", "auto": True}]
    assert (r.json["taken_local"], r.json["ocr"]) == ("2026-09-30T14:00:00+02:00", "Kõik on hästi")
    tags = owner.get("/api/meedia/sildid").json["tags"]
    assert {"tag": "koer", "count": 1} in tags
    assert owner.get("/api/meedia/fail/puudub.jpg").status_code == 404


def test_editing_tags_keeps_the_models_and_rejects_the_removed(owner, media):
    url = "/api/meedia/fail/" + quote("koer pargis.jpg")
    r = owner.patch(url, json={"tags": ["koer", "park", "Mets"], "title": " Rex "})
    assert r.json["tags"] == [{"tag": "koer", "auto": False}, {"tag": "mets", "auto": False}, {"tag": "park", "auto": True}]
    data = json.loads(store.companion_path(media, "koer pargis.jpg").read_text())
    assert (data["tags"], data["rejected_tags"], data["title"]) == (["koer", "mets"], ["sügis"], "Rex")
    r = owner.patch(url, json={"tags": ["koer", "park", "mets", "sügis"]})  # typed in again
    data = json.loads(store.companion_path(media, "koer pargis.jpg").read_text())
    assert (data["tags"], data["rejected_tags"]) == (["koer", "mets", "sügis"], [])


def test_bulk_tags_and_delete(owner, media):
    r = owner.post("/api/meedia/mitu", json={"names": ["kass.jpg", "meri.mp4", "puudub"], "add": ["Puhkus"], "remove": ["talv"]})
    assert r.json == {"done": 2}
    assert names(owner.get("/api/meedia?tag=puhkus")) == ["meri.mp4", "kass.jpg"]
    assert names(owner.get("/api/meedia?tag=talv")) == []
    owner.post("/api/meedia/mitu", json={"names": ["kass.jpg"], "delete": True})
    assert "kass.jpg" not in names(owner.get("/api/meedia"))
    assert not (media / "kass.jpg").exists()


def test_files_thumbnails_and_ranges(owner, media):
    r = owner.get("/meedia/fail/meri.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100 and r.mimetype == "video/mp4"
    r = owner.get("/meedia/fail/kass.jpg?lae=1")
    assert "attachment" in r.headers["Content-Disposition"]
    for name in ("kass.jpg", "meri.mp4"):
        r = owner.get(f"/meedia/pisi/{name}")
        assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"
    assert owner.get("/meedia/pisi/kass.jpg?s=5000").status_code == 404
    assert owner.get("/meedia/fail/..%2F..%2Fmarkmik.db").status_code == 404


def test_waiting_and_refused_files_are_listed_and_can_be_deleted(owner, media, conn):
    (media / "katki.mov").write_bytes(b"x")
    index.expect(conn, media, "katki.mov", source="upload")
    index.fail(conn, "katki.mov", "Videot ei saa avada.")
    assert owner.get("/api/meedia/ootel").json == {"items": [{"name": "katki.mov", "kind": "video", "error": "Videot ei saa avada."}]}
    assert owner.delete("/api/meedia/fail/katki.mov").json == {"ok": True}
    assert owner.get("/api/meedia/ootel").json == {"items": []}
    assert owner.delete("/api/meedia/fail/katki.mov").status_code == 404


def test_page_and_everything_is_owner_only(client, owner, media):
    assert owner.get("/meedia").status_code == 200
    for url in ("/meedia", "/api/meedia", "/meedia/fail/kass.jpg", "/meedia/pisi/kass.jpg"):
        assert client.get(url).status_code in (302, 401, 403), url
