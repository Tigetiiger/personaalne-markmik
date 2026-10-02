"""Taking files in (docs/MEEDIA-PLAN.md, step 2): what a file says about itself, and conversion
to the one kept format per kind."""

import json
import os
import time

import pytest
from PIL import ExifTags

from app.meedia import index, jobs, probe, store
from tests.meedia.samples import heic, jpeg, video

B = ExifTags.Base


def companion(root, name):
    return json.loads(store.companion_path(root, name).read_text())


def trash(root):
    return sorted(p.name.split("_", 1)[1] for p in (root / store.TRASH).iterdir())


def test_picture_date_place_and_camera_come_from_exif(conn, root):
    jpeg(root / "a.jpg", exif={
        B.DateTimeOriginal: "2026:09:30 14:02:11", B.OffsetTimeOriginal: "+03:00",
        B.Make: "Apple", B.Model: "iPhone 15",
        "gps": {1: "N", 2: (59.0, 26.0, 13.2), 3: "E", 4: (24.0, 44.0, 42.0)},
    })
    index.intake(conn, root, "a.jpg", source="upload")
    data = companion(root, "a.jpg")
    assert data["taken_at"] == "2026-09-30T14:02:11+03:00"
    assert data["gps"] == {"lat": 59.437, "lon": 24.745}
    assert data["camera"] == "Apple iPhone 15"
    assert conn.execute("SELECT taken_at FROM media").fetchone()[0] == "2026-09-30T11:02:11Z"


def test_picture_without_offset_is_tallinn_time_and_turned_one_is_measured_turned(conn, root):
    jpeg(root / "a.jpg", size=(64, 48), exif={B.DateTimeOriginal: "2026:01:15 10:00:00", B.Orientation: 6,
                                              B.Make: "Canon", B.Model: "Canon EOS R6"})
    index.intake(conn, root, "a.jpg", source="upload")
    data = companion(root, "a.jpg")
    assert data["taken_at"] == "2026-01-15T10:00:00+02:00"
    assert (data["width"], data["height"]) == (48, 64)
    assert data["camera"] == "Canon EOS R6"


def test_heic_becomes_jpeg_and_the_original_goes_to_the_trash(conn, root):
    heic(root / "IMG_9.HEIC")
    sha = store.sha256_of(root / "IMG_9.HEIC")
    assert index.intake(conn, root, "IMG_9.HEIC", source="upload") == "IMG_9.jpg"
    data = companion(root, "IMG_9.jpg")
    assert (data["mime"], data["width"], data["height"]) == ("image/jpeg", 64, 48)
    assert data["converted_from"] == {"name": "IMG_9.HEIC", "sha256": sha}
    assert not (root / "IMG_9.HEIC").exists()
    assert trash(root) == ["IMG_9.HEIC"]
    assert index.find_duplicate(conn, sha) == "IMG_9.jpg"


def test_hevc_mov_becomes_h264_mp4(conn, root):
    video(root / "klipp.mov", codec="libx265")
    assert index.intake(conn, root, "klipp.mov", source="folder") == "klipp.mp4"
    info = probe.video_meta(root / "klipp.mp4")
    assert (info["_vcodec"], info["_acodec"], info["_pix_fmt"]) == ("h264", "aac", "yuv420p")
    assert abs(info["duration"] - 2) <= probe.DURATION_SLACK
    data = companion(root, "klipp.mp4")
    assert (data["kind"], data["mime"], data["width"], data["height"]) == ("video", "video/mp4", 320, 240)
    assert data["converted_from"]["name"] == "klipp.mov"
    assert trash(root) == ["klipp.mov"]
    assert list((root / store.INCOMING).iterdir()) == []


def test_h264_mp4_up_to_1080p_is_kept_as_it_is(conn, root):
    video(root / "v.mp4")
    sha = store.sha256_of(root / "v.mp4")
    assert index.intake(conn, root, "v.mp4", source="upload") == "v.mp4"
    assert companion(root, "v.mp4")["sha256"] == sha
    assert companion(root, "v.mp4")["converted_from"] is None


def test_bigger_than_1080p_is_scaled_to_1080_on_the_short_side(conn, root):
    video(root / "suur.mp4", size="1920x1440", seconds=1)
    final = index.intake(conn, root, "suur.mp4", source="upload")
    assert final == "suur.mp4"  # the original went to the trash first: the name is free
    data = companion(root, final)
    assert (data["width"], data["height"]) == (1440, 1080)
    assert trash(root) == ["suur.mp4"]


def test_hdr_video_is_tone_mapped_to_8_bit(conn, root):
    video(root / "hdr.mov", codec="libx265", hdr=True, seconds=1)
    assert probe.video_meta(root / "hdr.mov")["_hdr"]
    index.intake(conn, root, "hdr.mov", source="upload")
    info = probe.video_meta(root / "hdr.mp4")
    assert (info["_pix_fmt"], info["_hdr"]) == ("yuv420p", False)


def test_converted_name_steps_past_a_kept_file(conn, root):
    video(root / "a.mp4")
    index.intake(conn, root, "a.mp4", source="upload")
    video(root / "a.mov", codec="libx265")
    assert index.intake(conn, root, "a.mov", source="upload") == "a-2.mp4"


def test_file_waiting_in_incoming_reaches_the_root_only_with_its_companion(conn, root):
    path = jpeg(root / store.INCOMING / "abc123.part")
    final = index.intake(conn, root, "Puhkus.jpg", source="upload", path=path)
    assert final == "Puhkus.jpg"
    assert (root / "Puhkus.jpg").exists() and store.companion_path(root, "Puhkus.jpg").exists()
    assert not path.exists()
    jpeg(root / store.INCOMING / "def.part")
    assert index.intake(conn, root, "Puhkus.jpg", source="upload", path=root / store.INCOMING / "def.part") == "Puhkus-2.jpg"


def test_import_original_is_not_trashed_when_asked(conn, root):
    path = heic(root / store.INCOMING / "x.part")
    index.intake(conn, root, "x.heic", source="import", path=path, trash_original=False)
    assert trash(root) == []
    assert not path.exists()


def test_broken_file_fails_once_and_is_tried_again_when_it_changes(conn, root):
    (root / "katki.mp4").write_bytes(b"not a video")
    index.scan(conn, root)
    counts = index.scan(conn, root)
    assert counts["failed"] == 1
    assert conn.execute("SELECT error FROM media_pending").fetchone()[0] == "Videot ei saa avada."
    assert index.scan(conn, root)["failed"] == 1  # not read again
    assert (root / "katki.mp4").exists() and not store.companion_path(root, "katki.mp4").exists()
    video(root / "katki.mp4")  # replaced with a good one
    assert index.scan(conn, root)["waiting"] == 1
    assert index.scan(conn, root)["taken"] == 1


def test_scan_hands_videos_to_convert_to_the_converter(conn, root):
    video(root / "a.mov", codec="libx265")
    jpeg(root / "b.jpg")
    handed = []
    index.scan(conn, root, convert=lambda name, source: handed.append((name, source)))
    counts = index.scan(conn, root, convert=lambda name, source: handed.append((name, source)))
    assert (counts["queued"], counts["taken"]) == (1, 1)
    assert handed == [("a.mov", "folder")]
    assert index.scan(conn, root, busy={"a.mov"})["busy"] == 1
    jobs.convert_one(conn, root, "a.mov", "folder")
    assert index.scan(conn, root)["indexed"] == 0
    assert [r[0] for r in conn.execute("SELECT name FROM media ORDER BY name")] == ["a.mp4", "b.jpg"]
    assert conn.execute("SELECT count(*) FROM media_pending").fetchone()[0] == 0


def test_converter_records_a_failure(conn, root):
    (root / "x.mov").write_bytes(b"junk")
    index.expect(conn, root, "x.mov", source="upload")
    jobs.convert_one(conn, root, "x.mov", "upload")
    assert conn.execute("SELECT source, error FROM media_pending").fetchone()[:] == ("upload", "Videot ei saa avada.")


def test_expected_upload_is_taken_at_the_next_scan_with_its_source(conn, root):
    jpeg(root / "u.jpg")
    index.expect(conn, root, "u.jpg", source="upload")
    assert index.scan(conn, root)["taken"] == 1
    assert companion(root, "u.jpg")["source"] == "upload"


def test_scan_stops_taking_in_past_its_time_budget(conn, root):
    for i in range(3):
        jpeg(root / f"{i}.jpg", color=(i, i, i))
    index.scan(conn, root)
    counts = index.scan(conn, root, budget=0)
    assert (counts["taken"], counts["waiting"]) == (0, 3)
    assert index.scan(conn, root)["taken"] == 3


def test_abandoned_uploads_are_purged_after_two_days(root):
    old = root / store.INCOMING / "old.part"
    new = root / store.INCOMING / "new.part"
    old.write_bytes(b"1")
    new.write_bytes(b"1")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    assert store.purge_incoming(root) == ["old.part"]
    assert new.exists()


@pytest.mark.parametrize("text, expected", [
    ("2026-09-30T14:02:11+0300", "2026-09-30T14:02:11+03:00"),
    ("2026-09-30T11:02:11.000000Z", "2026-09-30T14:02:11+03:00"),
    ("2026-01-30T10:02:11Z", "2026-01-30T12:02:11+02:00"),
    ("eile", None),
    (None, None),
])
def test_video_time(text, expected):
    assert probe._video_time(text) == expected


def test_iso6709():
    assert probe._iso6709("+59.4370+024.7450+012.000/") == {"lat": 59.437, "lon": 24.745}
    assert probe._iso6709("") is None
