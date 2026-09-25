"""Postkast: list, count, confirm (plain and edited), reject, confirm-all."""

import pytest

from app import events

HTML = {"Accept": "text/html"}


def status(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def test_list_is_newest_first_with_local_times(owner, conn, proposed):
    older = proposed("Vanem", start_at="2026-10-02T07:15:00Z", end_at="2026-10-02T08:00:00Z",
                     description="Nädalapäev ei klapi kuupäevaga.", confidence="med")
    newer = proposed("Uuem", kind="kodutöö", all_day=1, start_at=None, start_date="2026-10-05", end_date="2026-10-06")
    events.create(conn, {"title": "Kinnitatud", "start_at": "2026-10-02T07:15:00Z"})

    items = owner.get("/api/inbox").get_json()

    assert [i["id"] for i in items] == [newer, older]
    assert items[0]["all_day"] is True
    assert (items[0]["start"], items[0]["end"], items[0]["kind"]) == ("2026-10-05", "2026-10-06", "kodutöö")
    assert items[1]["start"] == "2026-10-02T10:15" and items[1]["end"] == "2026-10-02T11:00"  # Tallinn, summer time
    assert items[1]["source_quote"] == "Reedel kell 10.15 on kontrolltöö."
    assert items[1]["confidence"] == "med" and items[1]["source"] == "ai"
    assert items[1]["description"] == "Nädalapäev ei klapi kuupäevaga."


def test_count(owner, proposed):
    assert owner.get("/api/inbox/count").get_json() == {"count": 0}
    proposed()
    proposed("Teine")
    assert owner.get("/api/inbox/count").get_json() == {"count": 2}


def test_confirm(owner, conn, proposed):
    eid = proposed()
    assert owner.post(f"/api/inbox/{eid}/confirm").status_code == 204
    row = status(conn, eid)
    assert row["status"] == "kinnitatud" and row["user_edited"] == 1
    assert row["title"] == "Matemaatika kontrolltöö" and row["start_at"] == "2026-10-02T07:15:00Z"
    assert owner.get("/api/inbox").get_json() == []


def test_confirm_with_edited_time_after_dst_change(owner, conn, proposed):
    eid = proposed()
    r = owner.post(f"/api/inbox/{eid}/confirm", json={
        "title": "  Füüsika kontrolltöö ", "kind": "kodutöö", "all_day": False,
        "start": "2026-10-26T08:00", "end": "2026-10-26T09:30"})
    assert r.status_code == 204, r.get_json()
    row = status(conn, eid)
    assert (row["title"], row["kind"], row["status"], row["user_edited"]) == ("Füüsika kontrolltöö", "kodutöö", "kinnitatud", 1)
    # 26.10 is winter time (UTC+2).
    assert (row["start_at"], row["end_at"], row["start_date"], row["all_day"]) == ("2026-10-26T06:00:00Z", "2026-10-26T07:30:00Z", None, 0)


def test_confirm_edited_to_all_day(owner, conn, proposed):
    eid = proposed()
    assert owner.post(f"/api/inbox/{eid}/confirm", json={"all_day": True, "start": "2026-10-03"}).status_code == 204
    row = status(conn, eid)
    assert (row["all_day"], row["start_date"], row["end_date"], row["start_at"]) == (1, "2026-10-03", "2026-10-04", None)


@pytest.mark.parametrize("body, message", [
    ({"title": "  "}, "Pealkiri on puudu."),
    ({"title": "x" * 201}, "Pealkiri on liiga pikk (üle 200 märgi)."),
    ({"kind": "pidu"}, "Tundmatu liik."),
    ({"start": "homme"}, "Algusaeg on puudu või vigane."),
    ({"all_day": True, "start": "3.10"}, "Kuupäev on puudu või vigane."),
    ({"start": "2026-10-03T10:00", "end": "2026-10-03T09:00"}, "Lõpp ei saa olla enne algust."),
    ({"all_day": 1, "start": "2026-10-03"}, "„Terve päev“ peab olema jah või ei."),
    ({"all_day": "false", "start": "2026-10-03T10:00"}, "„Terve päev“ peab olema jah või ei."),
])
def test_confirm_refuses_bad_edits(owner, conn, proposed, body, message):
    eid = proposed()
    r = owner.post(f"/api/inbox/{eid}/confirm", json=body)
    assert r.status_code == 400 and r.get_json() == {"error": message}
    assert status(conn, eid)["status"] == "pakutud"


def test_confirm_keeps_multi_day_and_overnight_spans(owner, conn, proposed):
    trip = proposed("Laager", all_day=1, start_at=None, start_date="2026-10-05", end_date="2026-10-08")
    r = owner.post(f"/api/inbox/{trip}/confirm", json={"title": "Laager", "all_day": True, "start": "2026-10-05", "end": "2026-10-08"})
    assert r.status_code == 204
    assert (status(conn, trip)["start_date"], status(conn, trip)["end_date"]) == ("2026-10-05", "2026-10-08")
    night = proposed("Öömatk")
    r = owner.post(f"/api/inbox/{night}/confirm", json={"all_day": False, "start": "2026-10-03T22:00", "end": "2026-10-04T01:30"})
    assert r.status_code == 204
    assert (status(conn, night)["start_at"], status(conn, night)["end_at"]) == ("2026-10-03T19:00:00Z", "2026-10-03T22:30:00Z")


def test_a_proposal_edited_in_the_calendar_can_still_be_confirmed(owner, conn, proposed):
    eid = proposed()
    conn.execute("UPDATE events SET user_edited = 1, title = 'Kalendris muudetud' WHERE id = ?", (eid,))
    assert owner.post(f"/api/inbox/{eid}/confirm").status_code == 204
    row = status(conn, eid)
    assert (row["status"], row["title"], row["user_edited"]) == ("kinnitatud", "Kalendris muudetud", 1)


def test_only_proposed_events_can_be_confirmed_or_rejected(owner, conn):
    confirmed = events.create(conn, {"title": "Juba kalendris", "start_at": "2026-10-02T07:15:00Z"})
    for action in ("confirm", "reject"):
        for eid in (confirmed, 9999):
            r = owner.post(f"/api/inbox/{eid}/{action}")
            assert r.status_code == 404 and "error" in r.get_json()
    assert status(conn, confirmed)["status"] == "kinnitatud"


def test_reject_remembers_the_uid(owner, conn, proposed):
    eid = proposed()
    uid = status(conn, eid)["uid"]
    assert owner.post(f"/api/inbox/{eid}/reject").status_code == 204
    assert status(conn, eid)["status"] == "eemaldatud"
    assert conn.execute("SELECT 1 FROM rejected_uids WHERE uid = ?", (uid,)).fetchone()
    # The next extraction run cannot bring it back.
    assert events.upsert_by_uid(conn, uid, {"title": "Uuesti", "status": "pakutud", "start_at": "2026-10-02T07:15:00Z"}) == (None, False)
    assert owner.get("/api/inbox/count").get_json() == {"count": 0}


def test_confirm_all(owner, conn, proposed):
    ids = [proposed("Üks"), proposed("Kaks", all_day=1, start_at=None, start_date="2026-10-05"), proposed("Kolm")]
    conn.execute("UPDATE events SET user_edited = 1 WHERE id = ?", (ids[2],))  # edited in the calendar
    others = [proposed("Kinnitatud", status="kinnitatud"), proposed("Eemaldatud", status="eemaldatud")]
    conn.execute("UPDATE events SET updated_at = '2020-01-01T00:00:00Z' WHERE id IN (?, ?)", others)
    before = [tuple(status(conn, i)) for i in others]

    r = owner.post("/api/inbox/confirm-all")
    assert r.status_code == 200 and r.get_json() == {"confirmed": 3}
    assert [status(conn, i)["status"] for i in ids] == ["kinnitatud"] * 3
    assert [tuple(status(conn, i)) for i in others] == before
    assert owner.get("/api/inbox/count").get_json() == {"count": 0}
    assert owner.post("/api/inbox/confirm-all").get_json() == {"confirmed": 0}


def test_postkast_page(owner):
    r = owner.get("/postkast", headers=HTML)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'data-page="inbox"' in html and 'id="pk-list"' in html and "<script>" not in html
