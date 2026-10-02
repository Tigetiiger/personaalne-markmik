"""Automatic tags (docs/MEEDIA-PLAN.md, step 3), with the model faked."""

import json

import pytest

from app.ai import runner
from app.meedia import index, store, tagging
from tests.meedia.samples import jpeg, video


def companion(root, name):
    return json.loads(store.companion_path(root, name).read_text())


@pytest.fixture
def calls(monkeypatch):
    """Every fake model call: (prompt, media types of its pictures). Answers from `answers`."""
    seen, answers = [], []

    def fake(prompt, schema, model):
        seen.append((prompt, list(runner.fake_images), model))
        return answers.pop(0) if answers else {"items": []}
    monkeypatch.setattr(runner, "fake_structured", fake)
    return seen, answers


def keep(conn, root, name, **kw):
    if store.kind_of(name) == "video":
        video(root / name, **kw)
    else:
        jpeg(root / name, color=tuple(name.encode()[:3]), **kw)
    return index.intake(conn, root, name, source="upload")


def test_pictures_go_four_to_a_call_and_answers_land_in_the_companions(conn, root, calls):
    seen, answers = calls
    for n in "abcde":
        keep(conn, root, f"{n}.jpg")
    answers += [
        {"items": [{"n": i, "tags": ["Koer", "park", "koer"], "description": f"Pilt {i}", "ocr": ""} for i in (1, 2, 3, 4)]},
        {"items": [{"n": 1, "tags": ["silt"], "description": "Hinnasilt", "ocr": "PIIM 1,29 €"}]},
    ]
    assert tagging.run(conn, root, max_calls=5) == 5
    assert [len(images) for _, images, _ in seen] == [4, 1]
    assert all(model == "haiku" for _, _, model in seen)
    a, e = companion(root, "a.jpg"), companion(root, "e.jpg")
    assert (a["auto_tags"], a["description"], a["ai"]["model"]) == (["koer", "park"], "Pilt 1", "haiku")
    assert (e["auto_tags"], e["ocr"]) == (["silt"], "PIIM 1,29 €")
    assert tagging.pending(conn, 10) == []
    assert tagging.run(conn, root, max_calls=5) == 0 and len(seen) == 2


def test_known_tags_are_offered_to_the_model(conn, root, calls):
    seen, _ = calls
    keep(conn, root, "a.jpg")
    index.update(conn, root, "a.jpg", {"tags": ["perekond"], "ai": {"model": "x"}})
    keep(conn, root, "b.jpg")
    tagging.run(conn, root, max_calls=1)
    assert "Olemasolevad sildid: perekond" in seen[0][0]


def test_a_video_is_one_call_of_four_frames(conn, root, calls):
    seen, answers = calls
    keep(conn, root, "v.mp4")
    keep(conn, root, "a.jpg")
    answers += [{"items": [{"n": 1, "tags": ["meri"], "description": "Laine", "ocr": ""}]}]
    tagging.run(conn, root, max_calls=1)
    assert seen[0][1] == ["image/jpeg"] * 4
    assert "ühe video kaadrid" in seen[0][0]
    assert companion(root, "v.mp4")["auto_tags"] == ["meri"]
    assert companion(root, "a.jpg")["ai"] is None


def test_a_persons_description_and_tags_are_never_overwritten(conn, root, calls):
    _, answers = calls
    keep(conn, root, "a.jpg")
    index.update(conn, root, "a.jpg", {"description": "Minu oma", "tags": ["mets"], "rejected_tags": ["puu"]})
    answers += [{"items": [{"n": 1, "tags": ["mets", "puu", "sammal"], "description": "Mudeli oma", "ocr": ""}]}]
    tagging.run(conn, root, max_calls=1)
    data = companion(root, "a.jpg")
    assert (data["description"], data["tags"]) == ("Minu oma", ["mets"])
    assert [tuple(r) for r in conn.execute("SELECT tag, auto FROM media_tags ORDER BY tag")] == [("mets", 0), ("sammal", 1)]


def test_missing_answer_marks_the_file_as_tried(conn, root, calls):
    _, answers = calls
    keep(conn, root, "a.jpg")
    keep(conn, root, "b.jpg")
    answers += [{"items": [{"n": 2, "tags": ["x"], "description": "", "ocr": ""}]}]
    tagging.run(conn, root, max_calls=1)
    assert companion(root, "a.jpg")["ai"]["error"] == "mudel ei vastanud selle faili kohta"
    assert companion(root, "b.jpg")["auto_tags"] == ["x"]


def test_a_file_that_cannot_be_shown_is_marked_and_the_rest_go(conn, root, calls):
    seen, _ = calls
    keep(conn, root, "a.jpg")
    keep(conn, root, "b.jpg")
    (root / "a.jpg").write_bytes(b"broken since")
    tagging.run(conn, root, max_calls=1)
    assert "error" in companion(root, "a.jpg")["ai"]
    assert len(seen[0][1]) == 1


def test_unusable_answer_marks_the_batch(conn, root, monkeypatch):
    keep(conn, root, "a.jpg")

    def broken(*a, **kw):
        raise RuntimeError("AI vastus ei vasta skeemile")
    monkeypatch.setattr(runner, "run_structured", broken)
    assert tagging.run(conn, root, max_calls=1) == 0
    assert companion(root, "a.jpg")["ai"]["error"] == "AI vastus ei vasta skeemile"


def test_own_daily_cap_pauses_and_leaves_files_waiting(conn, root, calls, monkeypatch):
    seen, _ = calls
    monkeypatch.setattr(tagging, "DAILY_CAP", 1)
    keep(conn, root, "v.mp4")
    keep(conn, root, "w.mp4", size="320x200")
    assert tagging.run(conn, root, max_calls=5) == 1
    assert len(seen) == 1
    assert [r["name"] for r in tagging.pending(conn, 10)] == ["w.mp4"]


def test_unavailable_model_leaves_files_untouched(conn, root, monkeypatch):
    keep(conn, root, "a.jpg")

    def down(*a, **kw):
        raise runner.AIUnavailable("Claude'i võti puudub")
    monkeypatch.setattr(runner, "run_structured", down)
    assert tagging.run(conn, root, max_calls=3) == 0
    assert companion(root, "a.jpg")["ai"] is None
