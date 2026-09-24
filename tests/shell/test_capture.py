"""Quick capture (app/capture.py, POST /api/capture): event, task or note."""

import pytest

from app import capture, collab, notes

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
]


@pytest.mark.parametrize("text, kind, title, start, start_at", EVENTS)
def test_dates_make_events(conn, today, text, kind, title, start, start_at):
    out = capture.capture(conn, text)
    assert out == {"kind": "event", "id": out["id"], "title": title, "start": start, "event_kind": kind}
    row = conn.execute("SELECT * FROM events WHERE id = ?", (out["id"],)).fetchone()
    assert (row["title"], row["kind"], row["status"], row["source"]) == (title, kind, "kinnitatud", "käsitsi")
    if start_at:
        assert (row["all_day"], row["start_at"]) == (0, start_at)
    else:
        assert (row["all_day"], row["start_date"]) == (1, start)


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
                            "event_kind": "kontrolltöö"}
    assert owner.post("/api/capture", json={"text": "Uus mõte", "preview": True}).get_json()["slug"] is None
    assert owner.post("/api/capture", json={"text": "Uus mõte"}).get_json()["slug"] == "uus-mote"


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}, []])
def test_api_rejects_empty(owner, body):
    r = owner.post("/api/capture", json=body)
    assert r.status_code == 400 and r.get_json() == {"error": "Kirjuta midagi."}


def test_api_busy_is_409(owner, conn, today, monkeypatch):
    notes.daily(conn, today)
    monkeypatch.setattr(collab, "is_busy", lambda conn, note_id: True)
    r = owner.post("/api/capture", json={"text": "todo x"})
    assert r.status_code == 409 and r.get_json() == {"error": "Märget muudetakse teises aknas."}
