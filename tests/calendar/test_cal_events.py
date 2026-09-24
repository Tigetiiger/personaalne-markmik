"""app/events.py: validation, sync upserts, removal and the range query."""

import json
from datetime import date

import pytest

from app import events, ics

D = date.fromisoformat


def occ(conn, start, end, **kw):
    return events.occurrences(conn, D(start), D(end), **kw)


# --- create / update / validation ------------------------------------------------------------

def test_create_fills_defaults_and_uid(conn, add):
    row = events.get(conn, add(title="  Mate  "))
    assert row["title"] == "Mate"
    assert row["uid"].startswith("markmik-")
    assert (row["kind"], row["status"], row["source"], row["tzid"]) == ("sündmus", "kinnitatud", "käsitsi", "Europe/Tallinn")
    assert row["user_edited"] == 0


def test_offsets_are_stored_as_utc(conn, add):
    row = events.get(conn, add(start_at="2026-10-07T10:00:00+03:00", end_at="2026-10-07T11:00+03:00"))
    assert (row["start_at"], row["end_at"]) == ("2026-10-07T07:00:00Z", "2026-10-07T08:00:00Z")


def test_all_day_end_defaults_to_next_day(conn, add):
    row = events.get(conn, add(all_day=1, start_date="2026-10-07"))
    assert (row["start_date"], row["end_date"], row["start_at"]) == ("2026-10-07", "2026-10-08", None)


@pytest.mark.parametrize("fields, message", [
    ({"title": ""}, "Pealkiri on puudu."),
    ({"title": "   "}, "Pealkiri on puudu."),
    ({"title": "x" * 201}, "Pealkiri on liiga pikk"),
    ({"kind": "pidu"}, "Tundmatu liik."),
    ({"status": "valmis"}, "Tundmatu olek."),
    ({"source": "twitter"}, "Tundmatu allikas."),
    ({"confidence": "sure"}, "Tundmatu kindlus."),
    ({"tzid": "Mars/Olympus"}, "Tundmatu ajavöönd."),
    ({"start_at": "2026-10-07T10:00:00"}, "Algusaeg on puudu või vigane."),  # no offset
    ({"start_at": "eile"}, "Algusaeg on puudu või vigane."),
    ({"end_at": "2026-10-07T06:59:00Z"}, "Lõpp ei saa olla enne algust."),
    ({"start_date": "2026-10-07"}, "Kellaajaga sündmusel pole terve päeva kuupäevi."),
    ({"rrule": "FREQ=SOMETIMES"}, "Vigane kordusreegel."),
    ({"colour": "red"}, "Tundmatud väljad: colour."),
])
def test_invalid_timed_fields(add, fields, message):
    with pytest.raises(ValueError, match=message):
        add(**fields)


@pytest.mark.parametrize("fields, message", [
    ({"start_date": "2026-10-07", "start_at": "2026-10-07T07:00:00Z"}, "Terve päeva sündmusel on kuupäevad"),
    ({"start_date": "7.10.2026"}, "Alguskuupäev on puudu või vigane."),
    ({}, "Alguskuupäev on puudu või vigane."),
    ({"start_date": "2026-10-07", "end_date": "2026-10-07"}, "Lõpp ei saa olla enne algust."),
    ({"start_date": "2026-10-07", "end_date": "2026-10-06"}, "Lõpp ei saa olla enne algust."),
])
def test_invalid_all_day_fields(add, fields, message):
    with pytest.raises(ValueError, match=message):
        add(all_day=1, **fields)


def test_title_of_200_chars_is_fine(add):
    add(title="x" * 200)


def test_update_marks_user_edited_only_when_by_user(conn, add):
    event_id = add()
    assert events.update(conn, event_id, {"title": "Uus"}, by_user=False)["user_edited"] == 0
    row = events.update(conn, event_id, {"title": "Uuem"})
    assert (row["title"], row["user_edited"]) == ("Uuem", 1)
    assert events.update(conn, 9999, {"title": "x"}) is None


def test_update_validates_the_merged_row(conn, add):
    event_id = add(end_at="2026-10-07T08:00:00Z")
    with pytest.raises(ValueError, match="Lõpp ei saa olla enne algust."):
        events.update(conn, event_id, {"start_at": "2026-10-07T09:00:00Z"})
    assert events.get(conn, event_id)["start_at"] == "2026-10-07T07:00:00Z"


def test_update_switches_timed_to_all_day_and_back(conn, add):
    event_id = add(end_at="2026-10-07T08:00:00Z")
    row = events.update(conn, event_id, events.local_times(True, "2026-10-07"))
    assert (row["all_day"], row["start_at"], row["end_at"], row["start_date"], row["end_date"]) == (1, None, None, "2026-10-07", "2026-10-08")
    row = events.update(conn, event_id, events.local_times(False, "2026-10-07T10:00", "2026-10-07T11:30"))
    assert (row["all_day"], row["start_at"], row["end_at"], row["start_date"]) == (0, "2026-10-07T07:00:00Z", "2026-10-07T08:30:00Z", None)


def test_local_times_reads_tallinn_wall_clock():
    assert events.local_times(False, "2026-07-01T10:00")["start_at"] == "2026-07-01T07:00:00Z"  # EEST
    assert events.local_times(False, "2026-12-01T10:00")["start_at"] == "2026-12-01T08:00:00Z"  # EET
    # The repeated hour means its first pass.
    assert events.local_times(False, "2026-10-25T03:30")["start_at"] == "2026-10-25T00:30:00Z"
    assert events.local_times(True, "2026-10-07T10:00", "2026-10-07T11:00")["end_date"] is None
    with pytest.raises(ValueError, match="Vigane algusaeg."):
        events.local_times(False, "2026-10-07")


# --- upsert / remove ---------------------------------------------------------------------------

def test_upsert_creates_then_refreshes(conn):
    fields = {"title": "Kontrolltöö", "kind": "kontrolltöö", "source": "ekool", "all_day": 1, "start_date": "2026-10-09"}
    event_id, created = events.upsert_by_uid(conn, "ekool:1", fields)
    assert created and events.get(conn, event_id)["uid"] == "ekool:1"
    same_id, created = events.upsert_by_uid(conn, "ekool:1", {**fields, "title": "KT: murrud"})
    assert (same_id, created) == (event_id, False)
    row = events.get(conn, event_id)
    assert (row["title"], row["user_edited"]) == ("KT: murrud", 0)


def test_upsert_keeps_user_edited_rows(conn):
    fields = {"title": "Kontrolltöö", "all_day": 1, "start_date": "2026-10-09"}
    event_id, _ = events.upsert_by_uid(conn, "ekool:2", fields)
    events.update(conn, event_id, {"title": "Minu pealkiri"})
    assert events.upsert_by_uid(conn, "ekool:2", {**fields, "title": "Allika pealkiri"}) == (event_id, False)
    assert events.get(conn, event_id)["title"] == "Minu pealkiri"


def test_removed_uid_never_comes_back(conn):
    event_id, _ = events.upsert_by_uid(conn, "gmail:x:1", {"title": "Ekskursioon", "source": "ai", "all_day": 1, "start_date": "2026-10-09"})
    events.remove(conn, event_id)
    assert events.get(conn, event_id)["status"] == "eemaldatud"
    assert conn.execute("SELECT 1 FROM rejected_uids WHERE uid = 'gmail:x:1'").fetchone()
    assert events.upsert_by_uid(conn, "gmail:x:1", {"title": "Ekskursioon", "all_day": 1, "start_date": "2026-10-09"}) == (None, False)
    assert occ(conn, "2026-10-01", "2026-10-31") == []


def test_invalid_upsert_writes_nothing(conn):
    with pytest.raises(ValueError):
        events.upsert_by_uid(conn, "ics:bad", {"title": "", "all_day": 1, "start_date": "2026-10-09"})
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


# --- occurrences ---------------------------------------------------------------------------------

def test_occurrence_shape(conn, add):
    event_id = add(title="Mate", kind="tund", end_at="2026-10-07T07:45:00Z", location="203")
    [o] = occ(conn, "2026-10-07", "2026-10-08")
    assert o == {
        "id": event_id, "uid": events.get(conn, event_id)["uid"], "title": "Mate",
        "start": "2026-10-07T10:00", "end": "2026-10-07T10:45", "all_day": False, "kind": "tund",
        "status": "kinnitatud", "source": "käsitsi", "note_id": None, "occurrence": "", "done": False,
        "location": "203", "description": "", "source_quote": None, "confidence": None, "recurring": False,
    }


def test_statuses_filter(conn, add):
    add(title="kindel")
    add(title="pakutud", status="pakutud")
    add(title="eemaldatud", status="eemaldatud")
    assert [o["title"] for o in occ(conn, "2026-10-07", "2026-10-08")] == ["kindel", "pakutud"]
    assert [o["title"] for o in occ(conn, "2026-10-07", "2026-10-08", statuses=("kinnitatud",))] == ["kindel"]


def test_timed_range_edges(conn, add):
    # 23:30-00:30 local crosses midnight: on both days. 22:00-24:00 ends at midnight: first day only.
    add(title="üle südaöö", start_at="2026-10-07T20:30:00Z", end_at="2026-10-07T21:30:00Z")
    add(title="kuni südaööni", start_at="2026-10-07T19:00:00Z", end_at="2026-10-07T21:00:00Z")
    add(title="ilma lõputa", start_at="2026-10-08T06:00:00Z")
    assert [o["title"] for o in occ(conn, "2026-10-07", "2026-10-08")] == ["kuni südaööni", "üle südaöö"]
    assert [o["title"] for o in occ(conn, "2026-10-08", "2026-10-09")] == ["üle südaöö", "ilma lõputa"]
    assert occ(conn, "2026-10-08", "2026-10-09")[1]["end"] is None


def test_all_day_ranges(conn, add):
    add(title="laager", all_day=1, start_date="2026-10-05", end_date="2026-10-08")  # 5., 6., 7.
    assert [o["start"] for o in occ(conn, "2026-10-07", "2026-10-08")] == ["2026-10-05"]
    assert occ(conn, "2026-10-08", "2026-10-09") == []
    assert occ(conn, "2026-10-01", "2026-10-05") == []
    [o] = occ(conn, "2026-10-01", "2026-11-01")
    assert (o["start"], o["end"], o["all_day"]) == ("2026-10-05", "2026-10-08", True)


def weekly_lesson(add, **kw):
    # Mondays 08:00-08:45 local from 19.10.2026 (EEST, UTC+3).
    return add(title="Mate", kind="tund", start_at="2026-10-19T05:00:00Z", end_at="2026-10-19T05:45:00Z",
               rrule="FREQ=WEEKLY;BYDAY=MO", **kw)


def test_weekly_lesson_keeps_its_local_time_across_dst(conn, add):
    series = weekly_lesson(add)
    got = occ(conn, "2026-10-19", "2026-11-03")
    assert [(o["start"], o["end"]) for o in got] == [
        ("2026-10-19T08:00", "2026-10-19T08:45"),
        ("2026-10-26T08:00", "2026-10-26T08:45"),  # the day after summer time ends
        ("2026-11-02T08:00", "2026-11-02T08:45"),
    ]
    # Occurrence keys are the UTC starts: the offset changed from +3 to +2.
    assert [o["occurrence"] for o in got] == ["2026-10-19T05:00:00Z", "2026-10-26T06:00:00Z", "2026-11-02T06:00:00Z"]
    assert all(o["id"] == series and o["recurring"] for o in got)


def test_series_with_until_and_rrule_prefix(conn, add):
    add(title="Kursus", start_at="2026-10-19T05:00:00Z", rrule="RRULE:FREQ=DAILY;UNTIL=20261021T235959Z")
    assert [o["start"][:10] for o in occ(conn, "2026-10-01", "2026-11-01")] == ["2026-10-19", "2026-10-20", "2026-10-21"]


def test_series_before_range_start_still_expands(conn, add):
    weekly_lesson(add)
    assert [o["start"] for o in occ(conn, "2027-03-01", "2027-03-02")] == ["2027-03-01T08:00"]


def test_exdates_skip_occurrences(conn, add):
    weekly_lesson(add, exdates_json=json.dumps(["2026-10-26T06:00:00Z"]))
    assert [o["start"][:10] for o in occ(conn, "2026-10-19", "2026-11-03")] == ["2026-10-19", "2026-11-02"]


def test_override_row_replaces_its_occurrence(conn, add):
    series = weekly_lesson(add)
    add(title="Mate (asendus)", kind="tund", start_at="2026-10-26T08:00:00Z", end_at="2026-10-26T08:45:00Z",
        series_id=series, recurrence_id="2026-10-26T06:00:00Z")
    got = occ(conn, "2026-10-19", "2026-11-03")
    assert [(o["title"], o["start"]) for o in got] == [
        ("Mate", "2026-10-19T08:00"), ("Mate (asendus)", "2026-10-26T10:00"), ("Mate", "2026-11-02T08:00")]


def test_cancelled_override_hides_occurrence(conn, add):
    series = weekly_lesson(add)
    add(title="Mate", kind="tund", start_at="2026-10-26T06:00:00Z", series_id=series,
        recurrence_id="2026-10-26T06:00:00Z", status="eemaldatud")
    assert [o["start"][:10] for o in occ(conn, "2026-10-19", "2026-11-03")] == ["2026-10-19", "2026-11-02"]


def test_removed_series_hides_its_overrides(conn, add):
    series = weekly_lesson(add)
    add(title="Asendus", start_at="2026-10-26T08:00:00Z", series_id=series, recurrence_id="2026-10-26T06:00:00Z")
    events.remove(conn, series)
    assert occ(conn, "2026-10-19", "2026-11-03") == []


def test_all_day_series(conn, add):
    add(title="Trenn", all_day=1, start_date="2026-10-24", rrule="FREQ=WEEKLY;COUNT=3",
        exdates_json=json.dumps(["2026-10-31"]))
    got = occ(conn, "2026-10-01", "2026-12-01")
    assert [(o["start"], o["end"], o["occurrence"]) for o in got] == [
        ("2026-10-24", "2026-10-25", "2026-10-24"), ("2026-11-07", "2026-11-08", "2026-11-07")]
    assert [o["start"] for o in occ(conn, "2026-11-07", "2026-11-08")] == ["2026-11-07"]


def test_done_per_occurrence(conn, add):
    series = weekly_lesson(add)
    single = add(title="Üksik", start_at="2026-10-20T07:00:00Z")
    events.set_done(conn, series, "2026-10-26T06:00:00Z", True)
    events.set_done(conn, single, "", True)
    got = {(o["title"], o["start"][:10]): o["done"] for o in occ(conn, "2026-10-19", "2026-11-03")}
    assert got == {("Mate", "2026-10-19"): False, ("Üksik", "2026-10-20"): True,
                   ("Mate", "2026-10-26"): True, ("Mate", "2026-11-02"): False}
    events.set_done(conn, series, "2026-10-26T06:00:00Z", False)
    assert not any(o["done"] for o in occ(conn, "2026-10-26", "2026-10-27"))


def test_dated_tasks_are_all_day_items(conn, note, task):
    n = note("matemaatika")
    t = task(n, "tee ülesanded 1-5", "2026-10-09", due_time="14:15")
    task(n, "tehtud asi", "2026-10-09", done=True)
    task(note("kustutatud", deleted=True), "kadunud", "2026-10-09")
    task(n, "hiljem", "2026-10-20")
    got = occ(conn, "2026-10-09", "2026-10-10")
    assert [(o["title"], o["done"]) for o in got] == [("tee ülesanded 1-5", False), ("tehtud asi", True)]
    o = got[0]
    assert (o["task_id"], o["note_slug"], o["note_id"], o["kind"], o["all_day"]) == (t, "matemaatika", n, "tähtaeg", True)
    assert (o["start"], o["end"], o["due_time"]) == ("2026-10-09", "2026-10-10", "14:15")
    assert occ(conn, "2026-10-09", "2026-10-10", tasks=False) == []


# --- stored forms and bad rows ----------------------------------------------------------------

def test_occurrence_keys_are_normalised(conn, add):
    series = weekly_lesson(add, exdates_json=["2026-10-26T08:00:00+02:00", "2026-11-02T06:00:00Z", "2026-11-02T06:00:00+00:00"])
    assert json.loads(events.get(conn, series)["exdates_json"]) == ["2026-10-26T06:00:00Z", "2026-11-02T06:00:00Z"]
    assert [o["start"][:10] for o in occ(conn, "2026-10-19", "2026-11-10")] == ["2026-10-19", "2026-11-09"]
    override = add(title="Asendus", start_at="2026-11-09T08:00:00Z", series_id=series, recurrence_id="2026-11-09T08:00:00+02:00")
    assert events.get(conn, override)["recurrence_id"] == "2026-11-09T06:00:00Z"
    daily = add(title="Trenn", all_day=1, start_date="2026-10-24", rrule="FREQ=DAILY;COUNT=3", exdates_json='["20261025"]')
    assert json.loads(events.get(conn, daily)["exdates_json"]) == ["2026-10-25"]


@pytest.mark.parametrize("fields, message", [
    ({"rrule": "FREQ=DAILY", "exdates_json": "not json"}, "Vigased erandkuupäevad."),
    ({"rrule": "FREQ=DAILY", "exdates_json": '{"a": 1}'}, "Vigased erandkuupäevad."),
    ({"rrule": "FREQ=DAILY", "exdates_json": [5]}, "Vigased erandkuupäevad."),
    ({"rrule": "FREQ=DAILY", "exdates_json": ["2026-10-26T08:00:00"]}, "Vigane korduse aeg."),  # no offset
    ({"rrule": "FREQ=DAILY;COUNT=3\nEXDATE:20261006T050000Z"}, "Vigane kordusreegel."),
    ({"rrule": "DTSTART:19000101T000000Z\r\nRRULE:FREQ=YEARLY"}, "Vigane kordusreegel."),
    ({"rrule": "FREQ=MINUTELY"}, "Kordus sagedamini kui kord päevas pole toetatud."),
    ({"rrule": "FREQ=DAILY;BYHOUR=8,9"}, "Kordus sagedamini kui kord päevas pole toetatud."),
    ({"series_id": 1}, "Korduse erandil puudub algne aeg"),
])
def test_rows_readers_cannot_use_are_refused(add, fields, message):
    if "series_id" in fields:
        weekly_lesson(add)
    with pytest.raises(ValueError, match=message):
        add(**fields)


def test_a_bad_legacy_row_is_skipped_not_fatal(owner, conn, add, caplog):
    add(title="Terve")
    # Written before validation got stricter (or by hand): unreadable exdates and rrule.
    conn.execute("INSERT INTO events (uid, title, start_at, rrule, exdates_json) VALUES "
                 "('vana-1', 'Vana', '2026-10-05T05:00:00Z', 'FREQ=DAILY', 'not json')")
    conn.execute("INSERT INTO events (uid, title, start_at, rrule) VALUES "
                 "('vana-2', 'Vanem', '2026-10-05T05:00:00Z', 'DTSTART:19000101T000000Z\nRRULE:FREQ=YEARLY')")
    assert [o["title"] for o in occ(conn, "2026-10-07", "2026-10-08")] == ["Terve"]
    assert "event" in caplog.text and "skipped" in caplog.text
    assert owner.get("/api/events?start=2026-10-01&end=2026-10-10").status_code == 200
    feed = owner.get(f"/ics/{ics.feed_token(conn)}.ics")
    assert feed.status_code == 200 and "SUMMARY:Terve" in feed.get_data(as_text=True)
    assert "Vanem" not in feed.get_data(as_text=True)


def test_flipping_all_day_converts_the_exception_keys(conn, add):
    series = weekly_lesson(add, exdates_json=["2026-10-26T06:00:00Z"])
    row = events.update(conn, series, events.local_times(True, "2026-10-19"))
    assert json.loads(row["exdates_json"]) == ["2026-10-26"]
    assert [o["start"] for o in occ(conn, "2026-10-19", "2026-11-03")] == ["2026-10-19", "2026-11-02"]
    row = events.update(conn, series, events.local_times(False, "2026-10-19T09:30", "2026-10-19T10:15"))
    assert json.loads(row["exdates_json"]) == ["2026-10-26T07:30:00Z"]  # 09:30 EET
    assert [o["start"] for o in occ(conn, "2026-10-19", "2026-11-03")] == ["2026-10-19T09:30", "2026-11-02T09:30"]
