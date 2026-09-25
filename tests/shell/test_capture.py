"""Quick capture (app/capture.py, POST /api/capture): event, task or note."""

from datetime import date

import pytest

from app import capture, collab, notes, util

EVENTS = [
    # text, kind, title, start (local), start_at (UTC) or None for all-day
    ("mate kontrolltöö reedel 10.15", "kontrolltöö", "mate kontrolltöö", "2026-09-25T10:15", "2026-09-25T07:15:00Z"),
    ("kt homme", "kontrolltöö", "kt", "2026-09-25", None),
    ("Kodutöö: matemaatika lk 12 3. oktoobril", "kodutöö", "Kodutöö: matemaatika lk 12", "2026-10-03", None),
    ("füüsika test järgmisel esmaspäeval kell 9", "kontrolltöö", "füüsika test", "2026-09-28T09:00", "2026-09-28T06:00:00Z"),
    ("Kohtumine Mariga 2.10 kell 14.15", "sündmus", "Kohtumine Mariga", "2026-10-02T14:15", "2026-10-02T11:15:00Z"),
    ("ajaloo ülesanded ülehomme", "kodutöö", "ajaloo ülesanded", "2026-09-26", None),
    ("trenn kell 18", "sündmus", "trenn", "2026-09-24T18:00", "2026-09-24T15:00:00Z"),
    ("sünnipäev 30.10.2026", "sündmus", "sünnipäev", "2026-10-30", None),
    # After 25.10 Tallinn is on winter time (UTC+2).
    ("kontsert 31. oktoobril kell 19:30", "sündmus", "kontsert", "2026-10-31T19:30", "2026-10-31T17:30:00Z"),
    ("homme", "sündmus", "Sündmus", "2026-09-25", None),
    # H.MM after a day word is a time, even when it could be a date ("8.05" is 8 May)...
    ("kt reedel 8.05", "kontrolltöö", "kt", "2026-09-25T08:05", "2026-09-25T05:05:00Z"),
    ("kt reedel 10.10", "kontrolltöö", "kt", "2026-09-25T10:10", "2026-09-25T07:10:00Z"),
    ("kt esmaspäeval 10.05", "kontrolltöö", "kt", "2026-09-28T10:05", "2026-09-28T07:05:00Z"),
    ("kt homme 9.10", "kontrolltöö", "kt", "2026-09-25T09:10", "2026-09-25T06:10:00Z"),
    # ... unless it is a date on that weekday in the next 8 weeks (2.10 and 16.10.2026 are Fridays)...
    ("proov reedel 2.10", "sündmus", "proov", "2026-10-02", None),
    ("kontrolltöö reedel 16.10", "kontrolltöö", "kontrolltöö", "2026-10-16", None),
    ("kt teisipäeval 13.10", "kontrolltöö", "kt", "2026-10-13", None),
    # ... or the hour would be before 6 (3.10 is a Saturday: the explicit date wins).
    ("kt reedel 3.10", "kontrolltöö", "kt", "2026-10-03", None),
    ("kt 3.10 9.10", "kontrolltöö", "kt", "2026-10-03T09:10", "2026-10-03T06:10:00Z"),
    # A time anywhere goes with the date.
    ("kell 18 trenn homme", "sündmus", "trenn", "2026-09-25T18:00", "2026-09-25T15:00:00Z"),
    ("trenn homme, kell 18", "sündmus", "trenn", "2026-09-25T18:00", "2026-09-25T15:00:00Z"),
]


@pytest.mark.parametrize("text, kind, title, start, start_at", EVENTS)
def test_dates_make_events(conn, today, text, kind, title, start, start_at):
    out = capture.capture(conn, text)
    assert out == {"kind": "event", "id": out["id"], "title": title, "start": start, "event_kind": kind,
                   "untitled": title == kind.capitalize()}
    row = conn.execute("SELECT * FROM events WHERE id = ?", (out["id"],)).fetchone()
    assert (row["title"], row["kind"], row["status"], row["source"]) == (title, kind, "kinnitatud", "käsitsi")
    if start_at:
        assert (row["all_day"], row["start_at"]) == (0, start_at)
    else:
        assert (row["all_day"], row["start_date"]) == (1, start)


@pytest.mark.parametrize("today_, text, start", [
    # Said on Friday 25.9: "reedel" is 2.10, and 16.10 is a Friday within 8 weeks — a date.
    (date(2026, 9, 25), "kontrolltöö reedel 16.10", "2026-10-16"),
    (date(2026, 9, 25), "kontrolltöö reedel 9.10", "2026-10-09"),
    (date(2026, 9, 25), "kt reedel 8.05", "2026-10-02T08:05"),  # 8 May: too far, a time
    # Said on 8.10: 6.10 is past, so a time on the coming Tuesday.
    (date(2026, 10, 8), "kt teisipäeval 6.10", "2026-10-13T06:10"),
])
def test_weekday_then_day_month(conn, monkeypatch, today_, text, start):
    monkeypatch.setattr(util, "local_today", lambda: today_)
    assert capture.capture(conn, text, preview=True)["start"] == start


@pytest.mark.parametrize("text, untitled", [("reede", True), ("Neljapäev, 1. oktoober 2026", True), ("homme kell 9", True),
                                            ("Peatükk 3.1", False)])
def test_untitled_marks_a_text_that_is_only_a_date(conn, today, text, untitled):
    assert capture.capture(conn, text, preview=True)["untitled"] is untitled


RANGES = [
    # text, start_at, end_at (UTC), title
    ("koosolek 14:00-15:00", "2026-09-24T11:00:00Z", "2026-09-24T12:00:00Z", "koosolek"),
    ("homme kell 9.15–10 ekskursioon", "2026-09-25T06:15:00Z", "2026-09-25T07:00:00Z", "ekskursioon"),
    ("trenn reedel 17.30 - 19", "2026-09-25T14:30:00Z", "2026-09-25T16:00:00Z", "trenn"),
    ("vale 15:00-14:00", "2026-09-24T12:00:00Z", None, "vale -14:00"),  # an end before the start is no end
]


@pytest.mark.parametrize("text, start_at, end_at, title", RANGES)
def test_time_ranges_set_the_end(conn, today, text, start_at, end_at, title):
    out = capture.capture(conn, text)
    row = conn.execute("SELECT * FROM events WHERE id = ?", (out["id"],)).fetchone()
    assert (row["start_at"], row["end_at"], row["title"]) == (start_at, end_at, title)


TASKS = [
    # text, task line in the daily note, due date the tasks table gets
    ("ülesanne lugeda peatükk", "- [ ] lugeda peatükk", None),
    ("todo: osta piima", "- [ ] osta piima", None),
    ("TODO helista vanaemale", "- [ ] helista vanaemale", None),
    ("- [ ] tee ülesanded @reede", "- [ ] tee ülesanded @reede", "2026-09-25"),
    ("[ ] vii raamat tagasi @3.10", "- [ ] vii raamat tagasi @3.10", "2026-10-03"),
    ("osta piima @homme", "- [ ] osta piima @homme", "2026-09-25"),
]


@pytest.mark.parametrize("text, line, due", TASKS)
def test_tasks_go_to_the_daily_note(conn, today, text, line, due):
    out = capture.capture(conn, text)
    daily = notes.daily(conn, today)
    assert out == {"kind": "task", "slug": daily["slug"], "text": line[6:]}
    assert daily["body"] == f"# Neljapäev, 24. september 2026\n\n{line}\n"
    task = conn.execute("SELECT * FROM tasks WHERE note_id = ?", (daily["id"],)).fetchone()
    assert (task["done"], task["due_date"]) == (0, due)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_tasks_join_the_list_at_the_end(conn, today):
    capture.capture(conn, "todo üks")
    capture.capture(conn, "todo kaks")
    assert notes.daily(conn, today)["body"].endswith("\n\n- [ ] üks\n- [ ] kaks\n")


def test_task_into_a_busy_daily_note_raises(conn, today, monkeypatch):
    notes.daily(conn, today)
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    with pytest.raises(notes.NoteBusy):
        capture.capture(conn, "todo x")


def test_a_refused_task_leaves_no_daily_note_behind(conn, today, monkeypatch):
    # One transaction: a daily note made only to take the task goes when the write is refused.
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    with pytest.raises(notes.NoteBusy):
        capture.capture(conn, "todo x")
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0


NOTES = ["Raamatu mõtted", "Peatükk 3", "Mõtteid kella kohta", "todo", "Ideed: 12 asja"]


@pytest.mark.parametrize("text", NOTES)
def test_anything_else_is_a_new_note(conn, today, text):
    out = capture.capture(conn, text)
    note = notes.by_slug(conn, out["slug"])
    assert out == {"kind": "note", "slug": note["slug"], "title": text}
    assert note["body"] == f"# {text}\n"


@pytest.mark.parametrize("text, kind", [(EVENTS[0][0], "event"), (TASKS[0][0], "task"), (NOTES[0], "note")])
def test_preview_creates_nothing(conn, today, text, kind):
    assert capture.capture(conn, text, preview=True)["kind"] == kind
    for table in ("events", "notes", "tasks"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_api(owner, conn, today):
    r = owner.post("/api/capture", json={"text": "  mate  kt   reedel "})
    assert r.status_code == 200
    assert r.get_json() == {"kind": "event", "id": r.get_json()["id"], "title": "mate kt", "start": "2026-09-25",
                            "event_kind": "kontrolltöö", "untitled": False}
    assert owner.post("/api/capture", json={"text": "Uus mõte", "preview": True}).get_json()["slug"] is None
    assert owner.post("/api/capture", json={"text": "Uus mõte"}).get_json()["slug"] == "uus-mote"


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}, []])
def test_api_rejects_empty(owner, body):
    r = owner.post("/api/capture", json=body)
    assert r.status_code == 400 and r.get_json() == {"error": "Kirjuta midagi."}


def test_api_rejects_long_text(owner, conn, today):
    r = owner.post("/api/capture", json={"text": "x" * 501, "preview": True})
    assert r.status_code == 400 and r.get_json() == {"error": "Liiga pikk (üle 500 märgi)."}
    assert owner.post("/api/capture", json={"text": "x" * 500, "preview": True}).status_code == 200


def test_api_invalid_event_is_400(owner, conn, today):
    # The calendar refuses a title over 200 characters; the palette shows its message.
    r = owner.post("/api/capture", json={"text": "a" * 300 + " homme"})
    assert r.status_code == 400 and "Pealkiri on liiga pikk" in r.get_json()["error"]
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_api_busy_is_409(owner, conn, today, monkeypatch):
    notes.daily(conn, today)
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    r = owner.post("/api/capture", json={"text": "todo x"})
    assert r.status_code == 409 and r.get_json() == {"error": "Märget muudetakse teises aknas."}
