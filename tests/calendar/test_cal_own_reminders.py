"""Own reminders (table `reminders`, docs/KODU-PLAN.md): one push at their time, late pushes, missed
ones, and their API."""

from datetime import date, datetime, timezone

import pytest

from app import events, reminders, vault

from test_cal_reminders import FakeNtfy, utc  # noqa: F401 - the fake ntfy server and clock helper


@pytest.fixture
def ntfy(conn, monkeypatch):
    vault.set_json(conn, "ntfy", {"url": "http://ntfy.test:8090/", "topic": "markmik-test"})
    fake = FakeNtfy()
    monkeypatch.setattr(reminders.httpx, "post", fake)
    monkeypatch.setattr(reminders.ics, "resolves_to_local", lambda host: False)
    return fake


@pytest.fixture
def clock(monkeypatch):
    def set_to(when):
        monkeypatch.setattr(reminders, "now_utc", lambda: when)
    return set_to


def run_at(conn, clock, when):
    clock(when)
    reminders.run(conn)


def test_local_at_reads_wall_time_across_the_dst_change():
    assert reminders.local_at("2026-10-26T18:00") == utc(2026, 10, 26, 16, 0)
    assert reminders.local_at("2026-10-24T18:00") == utc(2026, 10, 24, 15, 0)
    # The repeated hour on 25.10: the first pass (summer time).
    assert reminders.local_at("2026-10-25T03:30") == utc(2026, 10, 25, 0, 30)
    for bad in ("homme", "2026-10-26T18:00+02:00", ""):
        with pytest.raises(ValueError):
            reminders.local_at(bad)


def test_pushed_once_at_its_time(conn, ntfy, clock):
    rid = reminders.create(conn, "Helista vanaemale", reminders.local_at("2026-10-26T18:00"), source="ai")
    run_at(conn, clock, utc(2026, 10, 26, 15, 59))
    assert ntfy == []
    run_at(conn, clock, utc(2026, 10, 26, 16, 0))
    [push] = ntfy
    assert push["json"]["title"] == "Helista vanaemale"
    assert push["json"]["message"] == "Meeldetuletus esmaspäev 26.10. kell 18:00"
    run_at(conn, clock, utc(2026, 10, 26, 16, 1))
    assert len(ntfy) == 1
    assert reminders.get(conn, rid)["sent"] is True


def test_late_within_twelve_hours_then_missed(conn, ntfy, clock):
    reminders.create(conn, "Osta piima", reminders.local_at("2026-10-27T09:00"))
    reminders.create(conn, "Vana asi", reminders.local_at("2026-10-26T09:00"))
    run_at(conn, clock, utc(2026, 10, 27, 10, 0))  # 12:00 local: three hours late
    assert [p["json"]["title"] for p in ntfy] == ["(hilinenud) Osta piima"]
    listed = reminders.items(conn, date(2026, 10, 27), date(2026, 10, 28), missed=True)
    assert [(r["text"], r["missed"], r["sent"]) for r in listed] == [("Vana asi", True, False), ("Osta piima", False, True)]
    # Without missed only the day's own.
    assert [r["text"] for r in reminders.items(conn, date(2026, 10, 27), date(2026, 10, 28))] == ["Osta piima"]


def test_a_new_time_sends_it_again_and_done_ones_are_not_sent(conn, ntfy, clock):
    rid = reminders.create(conn, "Trenn", reminders.local_at("2026-10-26T17:00"))
    done = reminders.create(conn, "Juba tehtud", reminders.local_at("2026-10-26T17:00"))
    reminders.update(conn, done, done=True)
    run_at(conn, clock, utc(2026, 10, 26, 15, 0))
    assert [p["json"]["title"] for p in ntfy] == ["Trenn"]
    reminders.update(conn, rid, remind_at=reminders.local_at("2026-10-26T19:00"))
    assert reminders.get(conn, rid)["sent"] is False
    run_at(conn, clock, utc(2026, 10, 26, 17, 0))
    assert [p["json"]["title"] for p in ntfy] == ["Trenn", "Trenn"]
    with pytest.raises(LookupError):
        reminders.update(conn, 999, done=True)


def test_a_removed_events_reminder_is_neither_sent_nor_listed(conn, ntfy, clock):
    eid = events.create(conn, {"title": "Füüsika KT", "kind": "kontrolltöö", "start_at": "2026-10-28T06:15:00Z"})
    keep = events.create(conn, {"title": "Ajaloo KT", "kind": "kontrolltöö", "start_at": "2026-10-28T08:15:00Z"})
    reminders.create(conn, "Õpi füüsikat", reminders.local_at("2026-10-27T18:00"), event_id=eid)
    reminders.create(conn, "Õpi ajalugu", reminders.local_at("2026-10-27T18:00"), event_id=keep)
    events.remove(conn, eid)
    run_at(conn, clock, utc(2026, 10, 27, 16, 0))
    # The kontrolltöö rule fires at 18:00 the evening before too; only the own ones count here.
    [push] = [p for p in ntfy if p["json"]["message"].startswith("Meeldetuletus")]
    assert push["json"]["title"] == "Õpi ajalugu"
    assert push["json"]["message"].endswith("· Ajaloo KT")
    assert [r["text"] for r in reminders.items(conn, date(2026, 10, 27), date(2026, 10, 28))] == ["Õpi ajalugu"]


def test_create_checks_text_and_event(conn):
    at = reminders.local_at("2026-10-26T18:00")
    with pytest.raises(ValueError):
        reminders.create(conn, "   ", at)
    with pytest.raises(ValueError):
        reminders.create(conn, "x", at, event_id=12345)
    rid = reminders.create(conn, "a" * 400, at)
    assert len(reminders.get(conn, rid)["text"]) == reminders.OWN_TEXT_CAP


def test_api(owner, conn):
    r = owner.post("/api/reminders", json={"text": "Helista", "remind_at": "2026-10-26T18:00"})
    assert r.status_code == 201, r.json
    rid = r.json["id"]
    assert r.json["remind_at"] == "2026-10-26T18:00"
    assert owner.post("/api/reminders", json={"text": "x"}).status_code == 400
    assert owner.post("/api/reminders", json={"text": "x", "remind_at": "homme"}).status_code == 400
    listed = owner.get("/api/reminders?start=2026-10-26&end=2026-10-27").json
    assert [x["id"] for x in listed] == [rid]
    cal = owner.get("/api/events?start=2026-10-26&end=2026-10-27").json
    [item] = [o for o in cal if o.get("reminder_id") == rid]
    assert (item["start"], item["kind"], item["title"]) == ("2026-10-26T18:00", "meeldetuletus", "Helista")
    r = owner.patch(f"/api/reminders/{rid}", json={"done": True, "remind_at": "2026-10-26T19:30"})
    assert r.status_code == 200 and r.json["done"] is True and r.json["remind_at"] == "2026-10-26T19:30"
    assert owner.patch(f"/api/reminders/{rid}", json={"done": "jah"}).status_code == 400
    assert owner.patch("/api/reminders/999", json={"done": True}).status_code == 404
    assert owner.delete(f"/api/reminders/{rid}").status_code == 204
    assert owner.delete(f"/api/reminders/{rid}").status_code == 404


def test_today_lists_reminders_and_whether_ntfy_is_set(owner, conn, monkeypatch):
    monkeypatch.setattr(reminders, "now_utc", lambda: datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc))
    reminders.create(conn, "Täna õhtul", reminders.local_at("2026-10-26T20:00"))
    data = owner.get("/api/today?date=2026-10-26").json
    assert [r["text"] for r in data["reminders"]] == ["Täna õhtul"]
    assert data["ntfy"] is False
    vault.set_json(conn, "ntfy", {"topic": "t"})
    assert owner.get("/api/today?date=2026-10-26").json["ntfy"] is True


def test_reminders_need_the_owner(client):
    assert client.get("/api/reminders?start=2026-10-26&end=2026-10-27").status_code in (302, 401)
