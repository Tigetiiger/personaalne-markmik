"""HTTP: /kalender, /api/events, /api/today, /api/settings/reminders."""

import json
from datetime import date

import pytest

from app import events
from app.views import calendar as calendar_view


def test_anonymous_gets_nothing(client):
    assert client.get("/api/events?start=2026-10-05&end=2026-10-12").status_code == 401
    assert client.post("/api/events", json={"title": "x"}).status_code == 401
    assert client.get("/api/today").status_code == 401
    assert client.get("/kalender", headers={"Accept": "text/html"}).status_code == 302


def test_calendar_page(owner):
    r = owner.get("/kalender")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'data-page="calendar"' in html and 'id="kalender"' in html
    assert "<script>" not in html


def test_create_list_patch_delete(owner, conn):
    r = owner.post("/api/events", json={"title": "Koosolek", "kind": "sündmus", "all_day": False,
                                        "start": "2026-10-07T10:00", "end": "2026-10-07T11:00", "location": "Aula"})
    assert r.status_code == 201
    created = r.get_json()
    assert (created["start"], created["end"], created["location"], created["user_edited"]) == ("2026-10-07T10:00", "2026-10-07T11:00", "Aula", False)
    assert events.get(conn, created["id"])["start_at"] == "2026-10-07T07:00:00Z"

    listed = owner.get("/api/events?start=2026-10-05T00:00:00+03:00&end=2026-10-12T00:00:00+03:00").get_json()
    assert [e["title"] for e in listed] == ["Koosolek"]

    # Drag: new times only; the rest stays.
    r = owner.patch(f"/api/events/{created['id']}", json={"start": "2026-10-08T12:00", "end": "2026-10-08T13:30", "all_day": False})
    moved = r.get_json()
    assert r.status_code == 200
    assert (moved["start"], moved["end"], moved["title"], moved["user_edited"]) == ("2026-10-08T12:00", "2026-10-08T13:30", "Koosolek", True)

    # Resize to all-day in the month view: end is optional.
    r = owner.patch(f"/api/events/{created['id']}", json={"all_day": True, "start": "2026-10-08", "end": None})
    assert (r.get_json()["start"], r.get_json()["end"], r.get_json()["all_day"]) == ("2026-10-08", "2026-10-09", True)

    # Edit text only.
    r = owner.patch(f"/api/events/{created['id']}", json={"title": "Lastevanemate koosolek", "kind": "isiklik"})
    assert (r.get_json()["title"], r.get_json()["kind"], r.get_json()["start"]) == ("Lastevanemate koosolek", "isiklik", "2026-10-08")

    assert owner.delete(f"/api/events/{created['id']}").status_code == 204
    assert owner.get("/api/events?start=2026-10-05&end=2026-10-12").get_json() == []
    assert owner.patch(f"/api/events/{created['id']}", json={"title": "x"}).status_code == 404
    assert owner.delete(f"/api/events/{created['id']}").status_code == 404
    assert events.get(conn, created["id"])["status"] == "eemaldatud"


def test_patch_cannot_change_status_or_source(owner, conn):
    event_id = events.create(conn, {"title": "AI leid", "source": "ai", "status": "pakutud", "start_at": "2026-10-07T07:00:00Z"})
    r = owner.patch(f"/api/events/{event_id}", json={"title": "Muudetud", "status": "kinnitatud", "source": "käsitsi"})
    assert r.status_code == 200
    row = events.get(conn, event_id)
    assert (row["title"], row["status"], row["source"]) == ("Muudetud", "pakutud", "ai")


@pytest.mark.parametrize("body, message", [
    ({"title": "", "start": "2026-10-07T10:00"}, "Pealkiri on puudu."),
    ({"title": "x", "start": "2026-10-07T10:00", "end": "2026-10-07T09:00"}, "Lõpp ei saa olla enne algust."),
    ({"title": "x", "start": "homme"}, "Vigane algusaeg."),
    ({"title": "x"}, "Vigane algusaeg."),
    ({"title": "x", "kind": "pidu", "start": "2026-10-07T10:00"}, "Tundmatu liik."),
    ({"title": "x", "all_day": True, "start": "2026-10-07", "end": "2026-10-07"}, "Lõpp ei saa olla enne algust."),
    ("tekst", "Vigane päring."),
])
def test_create_validation(owner, body, message):
    r = owner.post("/api/events", json=body)
    assert r.status_code == 400 and r.get_json() == {"error": message}


@pytest.mark.parametrize("query", ["", "?start=2026-10-05", "?start=eile&end=täna", "?start=2026-10-12&end=2026-10-05",
                                   "?start=2026-01-01&end=2028-01-01"])
def test_bad_ranges(owner, query):
    r = owner.get(f"/api/events{query}")
    assert r.status_code == 400 and r.get_json() == {"error": "Vigane ajavahemik."}


def test_csrf_needed(owner):
    assert owner.c.post("/api/events", json={"title": "x", "start": "2026-10-07T10:00"}).status_code == 403


def test_done_toggle(owner, conn):
    series = events.create(conn, {"title": "Mate", "start_at": "2026-10-19T05:00:00Z", "rrule": "FREQ=WEEKLY"})
    assert owner.post(f"/api/events/{series}/done", json={"occurrence": "2026-10-26T06:00:00Z", "done": True}).status_code == 204
    listed = owner.get("/api/events?start=2026-10-19&end=2026-11-02").get_json()
    assert [(e["start"][:10], e["done"]) for e in listed] == [("2026-10-19", False), ("2026-10-26", True)]
    owner.post(f"/api/events/{series}/done", json={"occurrence": "2026-10-26T06:00:00Z", "done": False})
    assert not any(e["done"] for e in owner.get("/api/events?start=2026-10-19&end=2026-11-02").get_json())
    assert owner.post("/api/events/9999/done", json={"done": True}).status_code == 404


def test_today(owner, conn, note, task, monkeypatch):
    monkeypatch.setattr(calendar_view, "local_today", lambda: date(2026, 10, 7))
    events.create(conn, {"title": "Mate", "kind": "tund", "start_at": "2026-10-07T05:00:00Z"})
    events.create(conn, {"title": "Homme", "start_at": "2026-10-08T05:00:00Z"})
    events.create(conn, {"title": "Füüsika KT", "kind": "kontrolltöö", "all_day": 1, "start_date": "2026-10-09"})
    events.create(conn, {"title": "Kauge KT", "kind": "kontrolltöö", "all_day": 1, "start_date": "2026-10-20"})
    events.create(conn, {"title": "Pakkumine 1", "status": "pakutud", "start_at": "2026-11-01T05:00:00Z"})
    events.create(conn, {"title": "Pakkumine 2", "status": "pakutud", "all_day": 1, "start_date": "2026-11-02"})
    n = note("fuusika")
    task(n, "täna teha", "2026-10-07", due_time="15:00")
    task(n, "ammu unustatud", "2026-10-01")
    task(n, "ammu tehtud", "2026-10-01", done=True)
    task(n, "homme teha", "2026-10-08")
    note("2026-10-07", daily_date="2026-10-07")

    data = owner.get("/api/today").get_json()
    assert data["date"] == "2026-10-07"
    assert [e["title"] for e in data["events"]] == ["Mate"]
    assert [(t["text"], t["done"], t["note_slug"]) for t in data["tasks"]] == [("ammu unustatud", False, "fuusika"), ("täna teha", False, "fuusika")]
    assert set(data["tasks"][0]) == {"id", "note_slug", "note_title", "text", "done", "due_date", "due_time"}
    assert [(t["title"], t["days_left"]) for t in data["tests_soon"]] == [("Füüsika KT", 2)]
    assert data["daily"] == {"slug": "2026-10-07", "title": "2026-10-07"}
    assert data["postkast"] == 2


def test_today_for_another_day(owner, conn, note, task, monkeypatch):
    monkeypatch.setattr(calendar_view, "local_today", lambda: date(2026, 10, 7))
    events.create(conn, {"title": "Mate", "kind": "tund", "start_at": "2026-10-07T05:00:00Z"})
    events.create(conn, {"title": "Homme", "start_at": "2026-10-08T05:00:00Z"})
    n = note("fuusika")
    task(n, "täna teha", "2026-10-07")
    task(n, "homme teha", "2026-10-08")
    data = owner.get("/api/today?date=2026-10-08").get_json()
    assert data["date"] == "2026-10-08"
    assert [e["title"] for e in data["events"]] == ["Homme"]
    # Tasks overdue by that day come along, as they do for today.
    assert [t["text"] for t in data["tasks"]] == ["täna teha", "homme teha"]
    r = owner.get("/api/today?date=2026-02-31")
    assert r.status_code == 400 and r.get_json() == {"error": "Vigane kuupäev."}


def test_today_without_daily_note(owner, monkeypatch):
    monkeypatch.setattr(calendar_view, "local_today", lambda: date(2026, 10, 7))
    data = owner.get("/api/today").get_json()
    assert (data["daily"], data["events"], data["tasks"], data["tests_soon"], data["postkast"]) == (None, [], [], [], 0)


def test_reminder_settings(owner):
    assert owner.get("/api/settings/reminders").get_json()["before"] == {"on": True, "minutes": 30}
    r = owner.put("/api/settings/reminders", json={"test": {"on": True, "time": "19:30"}, "homework": {"on": False, "time": "17:00"},
                                                   "before": {"on": True, "minutes": 15}})
    assert r.status_code == 200 and r.get_json()["test"]["time"] == "19:30"
    assert owner.get("/api/settings/reminders").get_json()["homework"]["on"] is False
    r = owner.put("/api/settings/reminders", json={"before": {"minutes": 5000}})
    assert r.status_code == 400 and r.get_json() == {"error": "Minuteid peab olema 0 kuni 1440."}


def test_seed_route_only_for_browser_tests(owner):
    assert owner.post("/_test/calendar/events", json={"title": "x", "start_at": "2026-10-07T07:00:00Z"}).status_code == 404


BASE = {"title": "x", "start": "2026-10-07T10:00", "end": "2026-10-07T11:00"}


@pytest.mark.parametrize("patch, message", [
    ({"title": 5}, "Pealkiri on puudu."),
    ({"title": ["a"]}, "Pealkiri on puudu."),
    ({"location": ["a"]}, "Koht peab olema tekst."),
    ({"description": {"a": 1}}, "Kirjeldus peab olema tekst."),
    ({"kind": ["tund"]}, "Tundmatu liik."),
    ({"all_day": "false"}, "Vigane päring."),
    ({"start": 5}, "Vigane aeg."),
    ({"start": "2026-02-30T10:00"}, "Vigane algusaeg."),
    ({"location": "L" * 501}, "Koht on liiga pikk (üle 500 märgi)."),
    ({"description": "A" * 5001}, "Kirjeldus on liiga pikk (üle 5000 märgi)."),
])
def test_bad_input_types_are_400(owner, patch, message):
    event_id = owner.post("/api/events", json=BASE).get_json()["id"]
    r = owner.patch(f"/api/events/{event_id}", json=patch)
    assert r.status_code == 400 and r.get_json() == {"error": message}


def test_null_location_means_empty(owner):
    event_id = owner.post("/api/events", json={**BASE, "location": None}).get_json()["id"]
    r = owner.patch(f"/api/events/{event_id}", json={"description": None})
    assert r.status_code == 200 and (r.get_json()["location"], r.get_json()["description"]) == ("", "")


@pytest.mark.parametrize("body", [[1], "x", {"occurrence": 5, "done": True}, {"done": "false"}, {}])
def test_done_needs_an_object_and_a_bool(owner, body):
    event_id = owner.post("/api/events", json=BASE).get_json()["id"]
    r = owner.post(f"/api/events/{event_id}/done", json=body)
    assert r.status_code == 400 and r.get_json() == {"error": "Vigane päring."}


def test_delete_one_occurrence_keeps_the_series(owner, conn):
    series = events.create(conn, {"title": "Mate", "uid": "sari", "start_at": "2026-10-19T05:00:00Z", "rrule": "FREQ=WEEKLY"})
    assert owner.delete(f"/api/events/{series}?occurrence=2026-10-26T08:00:00%2B02:00").status_code == 204
    listed = owner.get("/api/events?start=2026-10-19&end=2026-11-03").get_json()
    assert [e["start"][:10] for e in listed] == ["2026-10-19", "2026-11-02"]
    assert json.loads(events.get(conn, series)["exdates_json"]) == ["2026-10-26T06:00:00Z"]
    assert conn.execute("SELECT COUNT(*) FROM rejected_uids").fetchone()[0] == 0
    single = owner.post("/api/events", json=BASE).get_json()["id"]
    r = owner.delete(f"/api/events/{single}?occurrence=2026-10-07T07:00:00Z")
    assert r.status_code == 400 and r.get_json() == {"error": "Sündmus ei kordu."}


def test_empty_occurrence_does_not_delete_the_series(owner, conn):
    series = events.create(conn, {"title": "Mate", "uid": "sari2", "start_at": "2026-10-19T05:00:00Z", "rrule": "FREQ=WEEKLY"})
    r = owner.delete(f"/api/events/{series}?occurrence=")
    assert r.status_code == 400 and r.get_json() == {"error": "Vigane päring."}
    assert events.get(conn, series)["status"] == "kinnitatud"
    assert conn.execute("SELECT COUNT(*) FROM rejected_uids").fetchone()[0] == 0


def raw_event(conn, **cols):
    row = {"uid": f"raw-{len(cols)}-{sorted(cols)}", "title": "raw", "start_at": "2026-10-19T05:00:00Z", **cols}
    return conn.execute(f"INSERT INTO events ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid


@pytest.mark.parametrize("cols, status, message", [
    ({"rrule": "FREQ=WEEKLY", "exdates_json": "garbage"}, 400, "Vigased erandkuupäevad."),
    ({"rrule": "FREQ=WEEKLY\nX-A:b"}, 400, "Vigane kordusreegel."),
    ({"rrule": "FREQ=WEEKLY", "tzid": "Nowhere/Land"}, 400, "Tundmatu ajavöönd."),
    ({"start_at": "garbage"}, 400, "Algusaeg on puudu või vigane."),
    ({"uid": "x\ny"}, 400, "Vigane uid."),
    ({"all_day": 1, "start_at": None, "start_date": "2026-10-20"}, 200, None),  # NULL end_date: repaired
])
def test_patch_on_a_bad_old_row_is_a_clear_400_or_repairs_it(owner, conn, cols, status, message):
    event_id = raw_event(conn, **cols)
    r = owner.patch(f"/api/events/{event_id}", json={"title": "t"})
    assert r.status_code == status and (r.get_json().get("error") if message else None) == message
    if status == 200:
        assert (r.get_json()["start"], r.get_json()["end"]) == ("2026-10-20", "2026-10-21")
    if "exdates_json" in cols:
        r = owner.delete(f"/api/events/{event_id}?occurrence=2026-10-26T05:00:00Z")
        assert r.status_code == 400 and r.get_json() == {"error": "Vigased erandkuupäevad."}


def test_patch_with_times_repairs_a_bad_old_row(owner, conn):
    event_id = raw_event(conn, start_at="garbage")
    r = owner.patch(f"/api/events/{event_id}", json={"start": "2026-10-20T10:00", "end": "2026-10-20T11:00"})
    assert r.status_code == 200 and r.get_json()["start"] == "2026-10-20T10:00"
