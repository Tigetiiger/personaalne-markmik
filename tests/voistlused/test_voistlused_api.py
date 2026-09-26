"""The finder's endpoints and its cards in Postkast (app/views/inbox.py)."""

import time

import pytest

from app import events, voistlused

URL = "https://robotex.international/et/2026"


@pytest.fixture
def robotex(conn, world):
    """A web find with its registration deadline, proposed. -> (competition id, deadline id)"""
    world.csv = b""
    world.candidate(URL, "Robotex", "Robootikavõistlus koolinoortele.")
    world.page(URL, world.ROBOTEX, world.event(registration_text="kuni 1. novembrini 2026",
                                               registration_deadline="2026-11-01"))
    voistlused.run(conn)
    comp, reg = conn.execute("SELECT id FROM events ORDER BY id").fetchall()
    return comp["id"], reg["id"]


def wait_done(owner):
    for _ in range(100):
        s = owner.get("/api/voistlused/status").get_json()
        if not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("the search did not end")


def test_run_in_the_background_then_status(owner, world):
    r = owner.post("/api/voistlused/run")
    assert r.status_code == 202 and "running" in r.get_json()
    s = wait_done(owner)
    assert s["last"]["new"] == s["last"]["teaduskool"] > 0 and s["last"]["errors"] == []
    assert owner.get("/api/inbox/count").get_json()["count"] == s["last"]["new"]


def test_run_refused_while_running_and_without_interests(owner, conn, world):
    voistlused._claim(conn)
    r = owner.post("/api/voistlused/run")
    assert r.status_code == 409 and r.get_json() == {"error": "Võistluste otsing juba käib."}
    voistlused._release(conn)
    voistlused.save_settings(conn, {"huvid": [], "markus": "", "iganadal": True})
    r = owner.post("/api/voistlused/run")
    assert r.status_code == 400 and r.get_json() == {"error": "Vali Seadetes vähemalt üks huviala."}
    assert owner.get("/api/voistlused/status").get_json() == {"running": False, "last": None}


def test_owner_and_csrf_only(client, owner, world):
    assert client.get("/api/voistlused/status").status_code == 401
    assert client.post("/api/voistlused/run").status_code in (401, 403)
    assert client.put("/api/voistlused/settings", json={}).status_code in (401, 403)
    assert owner.c.post("/api/voistlused/run").status_code == 403  # no X-CSRF
    assert owner.c.put("/api/voistlused/settings", json={"huvid": [], "markus": "", "iganadal": False}).status_code == 403
    assert owner.get("/api/voistlused/status").get_json()["running"] is False


def test_settings_endpoints(owner):
    got = owner.get("/api/voistlused/settings").get_json()
    assert got["huvid"] == ["reaalained", "loodusained", "robootika"] and got["iganadal"] is True
    assert [c["key"] for c in got["choices"]] == ["reaalained", "loodusained", "robootika", "humanitaar"]
    assert got["choices"][2] == {"key": "robootika", "label": "Robootika ja tehnoloogia", "covers": "Robotex, FLL, häkatonid, inseneeria"}
    r = owner.put("/api/voistlused/settings", json={"huvid": ["humanitaar"], "markus": "7. klass", "iganadal": False})
    assert r.status_code == 200 and r.get_json()["huvid"] == ["humanitaar"]
    assert owner.get("/api/voistlused/settings").get_json()["markus"] == "7. klass"
    r = owner.put("/api/voistlused/settings", json={"huvid": ["sport"], "markus": "", "iganadal": False})
    assert r.status_code == 400 and r.get_json() == {"error": "Tundmatu huviala."}


def test_one_card_per_competition(owner, conn, robotex):
    comp, reg = robotex
    [card] = owner.get("/api/inbox").get_json()
    assert card["id"] == comp and card["origin"] == "Veeb" and card["link"] == URL
    assert card["deadline"] == {"id": reg, "start": "2026-11-01"}
    assert card["description"] == "Robootikavõistlus koolinoortele."  # the link line is not repeated
    assert card["source_quote"] == "Robotex International toimub 27.–29. novembril 2026 Tallinnas."
    assert owner.get("/api/inbox/count").get_json() == {"count": 1}
    assert owner.get("/api/today").get_json()["postkast"] == 1


def test_other_proposals_are_unchanged(owner, conn):
    events.create(conn, {"title": "Ekskursioon", "status": "pakutud", "source": "ai", "source_ref": "gmail:a:1",
                         "all_day": 1, "start_date": "2026-10-09", "description": "Link: https://x.ee"})
    [card] = owner.get("/api/inbox").get_json()
    assert "origin" not in card and "deadline" not in card and card["description"] == "Link: https://x.ee"


def test_kinnita_confirms_both_and_the_deadline_follows_the_title(owner, conn, robotex):
    comp, reg = robotex
    r = owner.post(f"/api/inbox/{comp}/confirm", json={"title": "Robotex 2026"})
    assert r.status_code == 204
    assert [(e["title"], e["status"]) for e in (events.get(conn, comp), events.get(conn, reg))] == [
        ("Robotex 2026", "kinnitatud"), ("Registreeri: Robotex 2026", "kinnitatud")]
    assert owner.get("/api/inbox/count").get_json() == {"count": 0}


def test_kinnita_without_changes(owner, conn, robotex):
    comp, reg = robotex
    assert owner.post(f"/api/inbox/{comp}/confirm").status_code == 204
    assert events.get(conn, reg)["title"] == "Registreeri: Robotex International 2026"
    assert events.get(conn, reg)["status"] == "kinnitatud"


def test_loobu_rejects_both_for_good(owner, conn, world, robotex):
    comp, reg = robotex
    assert owner.post(f"/api/inbox/{comp}/reject").status_code == 204
    assert {events.get(conn, i)["status"] for i in robotex} == {"eemaldatud"}
    uids = {r["uid"] for r in conn.execute("SELECT uid FROM rejected_uids")}
    assert uids == {events.get(conn, comp)["uid"], events.get(conn, reg)["uid"]}
    assert owner.get("/api/inbox").get_json() == []


def test_a_deadline_left_alone_is_its_own_card(owner, conn, robotex):
    comp, reg = robotex
    events.update(conn, comp, {"status": "kinnitatud"})  # confirmed in the calendar, say
    [card] = owner.get("/api/inbox").get_json()
    assert card["id"] == reg and card["title"] == "Registreeri: Robotex International 2026" and card["deadline"] is None
