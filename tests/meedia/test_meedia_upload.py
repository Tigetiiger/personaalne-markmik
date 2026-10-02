"""Chunked upload (docs/MEEDIA-PLAN.md, call 8)."""

import hashlib
import json

from app.meedia import store
from app.views import meedia as view
from tests.meedia.samples import heic, jpeg, video


def media_root(app):
    return store.default_root()


def upload(owner, data, name, chunk=None, sha=None):
    r = owner.post("/api/meedia/upload", json={"name": name, "size": len(data)})
    assert r.status_code == 200, r.json
    upload_id, step = r.json["id"], chunk or r.json["chunk"]
    for at in range(0, len(data), step):
        r = owner.put(f"/api/meedia/upload/{upload_id}?offset={at}", data=data[at:at + step],
                      headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.json
    return upload_id, owner.post(f"/api/meedia/upload/{upload_id}/done",
                                 json={"sha256": sha or hashlib.sha256(data).hexdigest()})


def test_picture_upload_in_pieces_is_taken_in_at_once(app, owner, conn, tmp_path):
    data = jpeg(tmp_path / "x.jpg").read_bytes()
    _, r = upload(owner, data, "Puhkus.JPG", chunk=100)
    assert r.status_code == 200 and r.json == {"name": "Puhkus.jpg", "status": "ready"}
    root = media_root(app)
    assert (root / "Puhkus.jpg").read_bytes() == data
    assert json.loads(store.companion_path(root, "Puhkus.jpg").read_text())["source"] == "upload"
    assert sorted(p.name for p in (root / store.INCOMING).iterdir()) == []


def test_heic_upload_is_converted_in_the_request(app, owner, tmp_path):
    _, r = upload(owner, heic(tmp_path / "x.heic").read_bytes(), "IMG_1.heic")
    assert r.json == {"name": "IMG_1.jpg", "status": "ready"}


def test_video_needing_conversion_is_left_to_the_worker(app, owner, conn, tmp_path):
    _, r = upload(owner, video(tmp_path / "x.mov", codec="libx265").read_bytes(), "klipp.mov")
    assert r.status_code == 202 and r.json == {"name": "klipp.mov", "status": "processing"}
    row = conn.execute("SELECT name, source, error FROM media_pending").fetchone()
    assert tuple(row) == ("klipp.mov", "upload", None)


def test_h264_video_is_kept_at_once(app, owner, tmp_path):
    _, r = upload(owner, video(tmp_path / "x.mp4").read_bytes(), "v.mp4")
    assert r.json == {"name": "v.mp4", "status": "ready"}


def test_resume_after_a_lost_piece(app, owner, tmp_path):
    data = jpeg(tmp_path / "x.jpg").read_bytes()
    upload_id = owner.post("/api/meedia/upload", json={"name": "a.jpg", "size": len(data)}).json["id"]
    owner.put(f"/api/meedia/upload/{upload_id}?offset=0", data=data[:300])
    r = owner.put(f"/api/meedia/upload/{upload_id}?offset=600", data=data[600:])
    assert r.status_code == 409 and r.json["received"] == 300
    assert owner.get(f"/api/meedia/upload/{upload_id}").json == {"received": 300}
    r = owner.post(f"/api/meedia/upload/{upload_id}/done", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 409
    owner.put(f"/api/meedia/upload/{upload_id}?offset=300", data=data[300:])
    r = owner.post(f"/api/meedia/upload/{upload_id}/done", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.json["status"] == "ready"


def test_wrong_checksum_drops_the_upload(app, owner, tmp_path):
    data = jpeg(tmp_path / "x.jpg").read_bytes()
    upload_id, r = upload(owner, data, "a.jpg", sha="0" * 64)
    assert r.status_code == 400
    assert owner.get(f"/api/meedia/upload/{upload_id}").status_code == 404
    assert not (media_root(app) / "a.jpg").exists()


def test_same_content_twice_is_kept_once(app, owner, tmp_path):
    data = jpeg(tmp_path / "x.jpg").read_bytes()
    upload(owner, data, "a.jpg")
    _, r = upload(owner, data, "b.jpg")
    assert r.json == {"name": "a.jpg", "status": "ready", "duplicate": True}
    assert not (media_root(app) / "b.jpg").exists()


def test_converted_original_uploaded_again_is_a_duplicate(app, owner, tmp_path):
    data = heic(tmp_path / "x.heic").read_bytes()
    upload(owner, data, "a.heic")
    _, r = upload(owner, data, "a.heic")
    assert r.json["duplicate"] and r.json["name"] == "a.jpg"


def test_unreadable_file_is_refused_and_dropped(app, owner):
    _, r = upload(owner, b"not a picture", "a.jpg")
    assert r.status_code == 400 and r.json["error"] == "Pilti ei saa avada."
    assert list((media_root(app) / store.INCOMING).iterdir()) == []


def test_refusals(app, owner, monkeypatch):
    assert owner.post("/api/meedia/upload", json={"name": "a.exe", "size": 5}).status_code == 400
    assert owner.post("/api/meedia/upload", json={"name": "a.jpg", "size": 0}).status_code == 400
    assert owner.get("/api/meedia/upload/../../etc").status_code == 404
    assert owner.get("/api/meedia/upload/" + "a" * 32).status_code == 404
    upload_id = owner.post("/api/meedia/upload", json={"name": "a.jpg", "size": 10}).json["id"]
    assert owner.put(f"/api/meedia/upload/{upload_id}?offset=0", data=b"1" * 11).status_code == 400
    monkeypatch.setattr(view, "DISK_RESERVE", 10 ** 15)
    assert owner.post("/api/meedia/upload", json={"name": "a.jpg", "size": 10}).status_code == 507


def test_only_the_owner_may_upload(client):
    assert client.post("/api/meedia/upload", json={"name": "a.jpg", "size": 5}).status_code in (302, 401, 403)


def test_checksums_are_optional_but_checked_when_sent(app, owner, tmp_path):
    data = jpeg(tmp_path / "x.jpg").read_bytes()
    upload_id = owner.post("/api/meedia/upload", json={"name": "a.jpg", "size": len(data)}).json["id"]
    r = owner.put(f"/api/meedia/upload/{upload_id}?offset=0", data=data, headers={"X-SHA256": "0" * 64})
    assert r.status_code == 400 and r.json["received"] == 0
    r = owner.put(f"/api/meedia/upload/{upload_id}?offset=0", data=data,
                  headers={"X-SHA256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200
    assert owner.post(f"/api/meedia/upload/{upload_id}/done", json={}).json["status"] == "ready"
