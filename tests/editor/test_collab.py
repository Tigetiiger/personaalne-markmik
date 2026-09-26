"""Live co-editing (app/collab.py, app/views/collab.py). Ported from Steam's test_koostoo.py and
extended with the per-note validity and save rules."""

import threading
from datetime import timedelta

import pytest

from app import collab, create_app, notes
from app.db import connect
from app.util import iso, now_utc

OP_A = [0, "text", {"es": [0, "A"]}]  # the server never interprets ops; any JSON will do
OP_B = [0, "text", {"es": [0, "B"]}]
MARI = {"name": "mari", "is_guest": True}
JAAN = {"name": "jaan", "is_guest": True}
ANNA = {"name": "anna", "is_guest": True}


def body_rev(conn, note_id):
    row = conn.execute("SELECT body, rev FROM notes WHERE id = ?", (note_id,)).fetchone()
    return row["body"], row["rev"]


def outside_write(conn, note_id, body):
    conn.execute("UPDATE notes SET body = ?, rev = rev + 1 WHERE id = ?", (body, note_id))


def age(conn, session_id, seconds):
    conn.execute(
        "UPDATE collab_sessions SET last_active_at = ? WHERE id = ?",
        (iso(now_utc() - timedelta(seconds=seconds)), session_id),
    )


# --- sessions and ops -------------------------------------------------------------------------

def test_second_editor_joins_the_same_session_and_sees_ops(conn, note):
    a = collab.join(conn, note["id"], "ka", MARI)
    assert (a["base"], a["ops"], a["participants"], a["rev"]) == ("# Alus\n", [], ["mari"], 1)
    collab.sync(conn, a["session"], "ka", MARI, 0, OP_A, "a1", 0)
    b = collab.join(conn, note["id"], "kb", JAAN)
    assert b["session"] == a["session"]
    assert [(o["seq"], o["op"], o["op_id"], o["author"]) for o in b["ops"]] == [(1, OP_A, "a1", "mari")]
    assert b["participants"] == ["jaan", "mari"]


def test_sessions_are_per_note(conn, note):
    other = notes.create(conn, "Teine", body="# Teine\n")
    a = collab.join(conn, note["id"], "ka", MARI)
    b = collab.join(conn, other["id"], "kb", MARI)
    assert a["session"] != b["session"] and b["base"] == "# Teine\n"


def test_op_is_accepted_only_on_top_of_the_latest(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.join(conn, note["id"], "kb", JAAN)
    r = collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    assert [o["op_id"] for o in r["ops"]] == ["a1"]  # own op comes back: acknowledged
    # jaan had not seen a1 yet: his op is not stored, he gets a1 to transform against
    r = collab.sync(conn, s, "kb", JAAN, 0, OP_B, "b1", 0)
    assert [o["op_id"] for o in r["ops"]] == ["a1"]
    r = collab.sync(conn, s, "kb", JAAN, 1, OP_B, "b1", 1)
    assert [(o["seq"], o["op_id"]) for o in r["ops"]] == [(2, "b1")]


def test_resent_op_is_stored_once(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    r = collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)  # response to the first was lost
    assert [o["seq"] for o in r["ops"]] == [1]


def test_op_ids_are_per_client(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.join(conn, note["id"], "kb", JAAN)
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "sama", 0)
    r = collab.sync(conn, s, "kb", JAAN, 1, OP_A, "sama", 1)  # jaan reuses mari's op id
    assert [(o["client"], o["seq"]) for o in r["ops"]] == [("kb", 2)]


def test_concurrent_ops_on_the_same_base_take_one_seq(app, note):
    s = collab.join(connect(app.config["DATABASE_PATH"]), note["id"], "k0", MARI)["session"]
    barrier = threading.Barrier(6)
    errors = []

    def send(i):
        c = connect(app.config["DATABASE_PATH"])
        try:
            barrier.wait()
            collab.sync(c, s, f"k{i}", MARI, 0, OP_A, f"op{i}", 0)
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=send, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c = connect(app.config["DATABASE_PATH"])
    assert errors == []
    assert [r[0] for r in c.execute("SELECT seq FROM collab_ops WHERE session_id = ?", (s,))] == [1]


def test_op_size_is_capped(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    with pytest.raises(collab.OpTooLarge):
        collab.sync(conn, s, "ka", MARI, 0, [0, "text", {"es": [0, "x" * collab.MAX_OP]}], "a1", 0)
    assert collab.last_seq(conn, s) == 0


def test_cursors_are_shared_with_others_only(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    b = collab.join(conn, note["id"], "kb", JAAN)
    assert b["cursors"] == [{"id": 1, "name": "mari", "cursor": None}]
    collab.sync(conn, s, "ka", MARI, 0, cursor={"path": [0, "text"], "start": 2, "end": 5})
    r = collab.sync(conn, s, "kb", JAAN, 0)
    assert r["cursors"] == [{"id": 1, "name": "mari", "cursor": {"path": [0, "text"], "start": 2, "end": 5}}]
    assert collab.sync(conn, s, "ka", MARI, 0)["cursors"] == [{"id": 2, "name": "jaan", "cursor": None}]


def test_participants_expire(conn, note, monkeypatch):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.join(conn, note["id"], "kb", JAAN)
    now = collab.time.monotonic()
    monkeypatch.setattr(collab.time, "monotonic", lambda: now + collab.GONE_S + 1)
    collab.sync(conn, s, "ka", MARI, 0)
    assert collab.participants(s) == ["mari"]


# --- saving and validity ----------------------------------------------------------------------

def test_save_writes_the_note_and_the_session_stays_valid(conn, note, monkeypatch):
    saved = []
    monkeypatch.setattr(notes, "on_saved", lambda c, note_id: saved.append(note_id))
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.join(conn, note["id"], "kb", JAAN)
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    collab.sync(conn, s, "kb", JAAN, 1, OP_B, "b1", 1)
    assert collab.save(conn, s, "Alus AB\n", 2, MARI)
    assert body_rev(conn, note["id"]) == ("Alus AB\n", 2)
    row = collab.get(conn, s)
    assert (row["saved_seq"], row["note_rev"]) == (2, 2)
    assert saved == [note["id"]]
    # Still the same session: its own save does not invalidate it.
    assert collab.sync(conn, s, "ka", MARI, 2).get("reset") is None
    assert collab.join(conn, note["id"], "kc", ANNA)["session"] == s


def test_equal_seq_save_is_a_no_op(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    assert collab.save(conn, s, "Esimene\n", 1, MARI)
    assert collab.save(conn, s, "Teine\n", 1, JAAN)
    assert body_rev(conn, note["id"]) == ("Esimene\n", 2)


def test_out_of_order_saves_keep_the_newest(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    collab.sync(conn, s, "ka", MARI, 1, OP_B, "a2", 1)
    assert collab.save(conn, s, "Uuem\n", 2, MARI)
    assert collab.save(conn, s, "Vanem\n", 1, JAAN)  # a slow request from before
    assert body_rev(conn, note["id"]) == ("Uuem\n", 2)
    assert collab.get(conn, s)["saved_seq"] == 2


def test_concurrent_saves_from_threads_keep_the_newest(app, note):
    path = app.config["DATABASE_PATH"]
    c = connect(path)
    s = collab.join(c, note["id"], "k0", MARI)["session"]
    for i in range(8):
        collab.sync(c, s, "k0", MARI, i, OP_A, f"a{i}", i)
    barrier = threading.Barrier(8)
    errors = []

    def save(seq):
        own = connect(path)
        try:
            barrier.wait()
            assert collab.save(own, s, f"seq {seq}\n", seq, MARI)
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)
        finally:
            own.close()

    threads = [threading.Thread(target=save, args=(seq,)) for seq in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    body, rev = body_rev(c, note["id"])
    assert body == "seq 8\n" and 2 <= rev <= 9
    row = collab.get(c, s)
    assert (row["saved_seq"], row["note_rev"]) == (8, rev)


def test_saved_seq_cannot_exceed_what_the_server_has(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    assert collab.save(conn, s, "x\n", 10**9, MARI)
    assert collab.get(conn, s)["saved_seq"] == 1
    collab.sync(conn, s, "ka", MARI, 1, OP_A, "a2", 1)  # unsaved op 2
    collab.leave(s, "ka")
    # Nobody there, but op 2 is unsaved: the session must be resumed, not replaced.
    j = collab.join(conn, note["id"], "kb", JAAN)
    assert j["session"] == s and [o["op_id"] for o in j["ops"]] == ["a1", "a2"]


def test_save_without_new_ops_writes_nothing(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    assert collab.save(conn, s, "Muu\n", 0, MARI)
    assert body_rev(conn, note["id"]) == ("# Alus\n", 1)


def test_idle_saved_session_is_replaced_by_a_new_one(conn, note, monkeypatch):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    collab.save(conn, s, "Uus\n", 1, MARI)
    collab.leave(s, "ka")
    # Just left (a reload): the session stays.
    assert collab.join(conn, note["id"], "kc", JAAN)["session"] == s
    collab.leave(s, "kc")
    later = collab.time.monotonic() + collab.GONE_S + 1
    monkeypatch.setattr(collab.time, "monotonic", lambda: later)
    new = collab.join(conn, note["id"], "kb", JAAN)
    assert new["session"] != s
    assert (new["base"], new["ops"], new["rev"]) == ("Uus\n", [], 2)
    assert collab.get(conn, s)["closed"] == 1


def test_unsaved_work_survives_everyone_leaving(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    collab.leave(s, "ka")
    again = collab.join(conn, note["id"], "kb", JAAN)
    assert again["session"] == s
    assert len(again["ops"]) == 1


def test_outside_write_resets_editors(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    outside_write(conn, note["id"], "Teine\n")  # the note moved on without this session
    assert collab.sync(conn, s, "ka", MARI, 1) == {"reset": True}
    assert collab.sync(conn, s, "ka", MARI, 1, OP_B, "a2", 1) == {"reset": True, "saved": False}
    assert not collab.save(conn, s, "x\n", 1, MARI)
    assert body_rev(conn, note["id"]) == ("Teine\n", 2)
    j = collab.join(conn, note["id"], "ka", MARI)
    assert j["session"] != s and j["base"] == "Teine\n" and j["ops"] == []


def test_deleted_note_resets_editors(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    conn.execute("UPDATE notes SET deleted_at = '2026-01-01T00:00:00Z' WHERE id = ?", (note["id"],))
    assert collab.sync(conn, s, "ka", MARI, 0) == {"reset": True}
    assert collab.join(conn, note["id"], "ka", MARI) is None


def test_close_all_resets_even_when_the_note_is_unchanged(conn, note):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.close_all(conn, note["id"])
    assert s not in collab._participants  # closed sessions do not stay in memory
    assert collab.sync(conn, s, "ka", MARI, 0) == {"reset": True}
    assert collab.join(conn, note["id"], "ka", MARI)["session"] != s


# --- busy -------------------------------------------------------------------------------------

def test_is_busy(conn, note):
    assert not collab.is_busy(conn, note["id"])  # nobody editing
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    assert collab.is_busy(conn, note["id"])  # just opened
    age(conn, s, collab.ACTIVE_S + 5)
    assert not collab.is_busy(conn, note["id"])  # open but idle, everything saved
    collab._touched.clear()
    collab.sync(conn, s, "ka", MARI, 0, OP_A, "a1", 0)
    assert collab.is_busy(conn, note["id"])  # typing
    age(conn, s, collab.ACTIVE_S + 5)
    assert collab.is_busy(conn, note["id"])  # idle, but op 1 is not saved
    collab.save(conn, s, "A\n", 1, MARI)
    age(conn, s, collab.ACTIVE_S + 5)
    assert not collab.is_busy(conn, note["id"])
    collab.sync(conn, s, "ka", MARI, 1, OP_B, "a2", 1)
    outside_write(conn, note["id"], "B\n")
    assert not collab.is_busy(conn, note["id"])  # an invalid session holds nothing up
    s2 = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.close_all(conn, note["id"])
    assert s2 != s and not collab.is_busy(conn, note["id"])


def test_busy_note_refuses_server_writes(conn, note):
    collab.join(conn, note["id"], "ka", MARI)
    with pytest.raises(notes.NoteBusy):
        notes.write_body(conn, note["id"], "x\n")


def test_last_active_is_written_at_most_every_ten_seconds(conn, note, monkeypatch):
    s = collab.join(conn, note["id"], "ka", MARI)["session"]
    clock = [1000.0]
    monkeypatch.setattr(collab.time, "monotonic", lambda: clock[0])
    writes = []
    conn.set_trace_callback(lambda sql: writes.append(sql) if "SET last_active_at" in sql else None)
    for i in range(5):
        clock[0] += 1
        collab.sync(conn, s, "ka", MARI, i, OP_A, f"a{i}", i)
    assert len(writes) == 1
    clock[0] += collab.TOUCH_S
    collab.sync(conn, s, "ka", MARI, 5, OP_A, "a5", 5)
    assert len(writes) == 2


def test_prune_drops_ops_of_sessions_closed_a_week_ago(conn, note):
    old = collab.join(conn, note["id"], "ka", MARI)["session"]
    collab.sync(conn, old, "ka", MARI, 0, OP_A, "a1", 0)
    collab.close_all(conn, note["id"])
    age(conn, old, (collab.KEEP_DAYS + 1) * 86400)
    recent = collab.join(conn, note["id"], "kb", JAAN)["session"]
    collab.sync(conn, recent, "kb", JAAN, 0, OP_A, "b1", 0)
    collab.prune(conn)
    assert collab.last_seq(conn, old) == 0
    assert collab.last_seq(conn, recent) == 1


# --- routes -----------------------------------------------------------------------------------

def test_routes_require_login_and_work_end_to_end(owner, client, conn, note):
    assert client.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).status_code == 401
    assert owner.post("/api/collab/join", json={"slug": note["slug"]}).status_code == 400
    assert owner.post("/api/collab/join", json={"slug": "puudub", "client": "k1"}).status_code == 404
    j = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()
    s = j["session"]
    assert j["base"] == "# Alus\n" and j["participants"] == ["Omanik"] and j["rev"] == 1
    assert owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": "abc"}).status_code == 400
    assert owner.post(f"/api/collab/{s}/save", json={"body": "x", "seq": None}).status_code == 400
    r = owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "op": OP_A, "op_id": "a1", "base": 0})
    assert [o["op_id"] for o in r.get_json()["ops"]] == ["a1"]
    assert owner.post(f"/api/collab/{s}/save", json={"body": "Koos\r\n", "seq": 1}).status_code == 204
    assert body_rev(conn, note["id"]) == ("Koos\n", 2)
    outside_write(conn, note["id"], "Muu\n")
    assert owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 1}).get_json() == {"reset": True}
    r = owner.post(f"/api/collab/{s}/save", json={"body": "x", "seq": 1})
    assert r.status_code == 409 and r.get_json() == {"reset": True}
    assert owner.post(f"/api/collab/{s}/leave", json={"client": "k1"}).status_code == 204
    assert owner.post("/api/collab/999/sync", json={"client": "k1", "seen": 0}).status_code == 404


def test_leave_as_beacon_carries_csrf_in_the_body(owner, note):
    s = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()["session"]
    assert owner.c.post(f"/api/collab/{s}/leave", json={"client": "k1"}).status_code == 403  # no token
    assert owner.c.post(f"/api/collab/{s}/leave", json={"client": "k1", "csrf": owner.token()}).status_code == 204
    assert collab.participants(s) == []


def test_client_error_report_closes_the_session(owner, note, conn):
    s = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()["session"]
    assert owner.post(f"/api/collab/{s}/leave", json={"client": "k1", "error": "TypeError: x"}).status_code == 204
    assert collab.get(conn, s)["closed"] == 1


def test_op_too_large_is_413(owner, note):
    s = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()["session"]
    big = [0, "text", {"es": [0, "x" * (collab.MAX_OP + 1)]}]
    r = owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "op": big, "op_id": "a1", "base": 0})
    assert r.status_code == 413 and "error" in r.get_json()


def test_guest_with_view_share_follows_but_cannot_edit(guest, note, conn):
    g = guest(note["id"], "view", "Mari")
    j = g.post("/api/collab/join", json={"slug": note["slug"], "client": "g1"}).get_json()
    s = j["session"]
    assert j["participants"] == ["Mari (külaline)"] and j["guest"] is True
    assert g.post(f"/api/collab/{s}/sync", json={"client": "g1", "seen": 0}).status_code == 200
    r = g.post(f"/api/collab/{s}/sync", json={"client": "g1", "seen": 0, "op": OP_A, "op_id": "a1", "base": 0})
    assert r.status_code == 403
    assert collab.last_seq(conn, s) == 0
    assert g.post(f"/api/collab/{s}/save", json={"body": "x", "seq": 0}).status_code in (401, 403)
    assert g.post(f"/api/collab/{s}/leave", json={"client": "g1", "error": "x"}).status_code == 403
    assert collab.get(conn, s)["closed"] == 0
    assert g.post(f"/api/collab/{s}/leave", json={"client": "g1"}).status_code == 204


def test_guest_with_edit_share_co_edits_only_their_note(guest, owner, note, conn):
    other = notes.create(conn, "Salajane", body="Salajane\n")
    g = guest(note["id"], "edit", "Mari")
    s = g.post("/api/collab/join", json={"slug": note["slug"], "client": "g1"}).get_json()["session"]
    r = g.post(f"/api/collab/{s}/sync", json={"client": "g1", "seen": 0, "op": OP_A, "op_id": "a1", "base": 0})
    assert [o["author"] for o in r.get_json()["ops"]] == ["Mari (külaline)"]
    assert g.post(f"/api/collab/{s}/save", json={"body": "Mari oma\n", "seq": 1}).status_code == 204
    assert body_rev(conn, note["id"])[0] == "Mari oma\n"
    # Another note: neither its session nor a new one.
    # Another note: indistinguishable from a missing one.
    assert g.post("/api/collab/join", json={"slug": other["slug"], "client": "g1"}).status_code == 404
    assert g.post("/api/collab/join", json={"slug": "puudub", "client": "g1"}).status_code == 404
    o = owner.post("/api/collab/join", json={"slug": other["slug"], "client": "k1"}).get_json()["session"]
    assert g.post(f"/api/collab/{o}/sync", json={"client": "g1", "seen": 0}).status_code == 403
    assert g.post(f"/api/collab/{o}/save", json={"body": "x", "seq": 0}).status_code == 403


def test_route_keeps_only_well_formed_cursors(owner, note):
    s = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()["session"]
    owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k2"})
    owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "cursor": {"path": [1, "text", True], "start": "3", "end": 4}})
    others = owner.post(f"/api/collab/{s}/sync", json={"client": "k2", "seen": 0}).get_json()["cursors"]
    assert others == [{"id": 1, "name": "Omanik", "cursor": {"path": [1, "text"], "start": 3, "end": 4}}]
    owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "cursor": "<script>"})
    others = owner.post(f"/api/collab/{s}/sync", json={"client": "k2", "seen": 0}).get_json()["cursors"]
    assert others[0]["cursor"] is None


def test_routes_reject_malformed_bodies_instead_of_500(owner, note):
    s = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "k1"}).get_json()["session"]
    for url in ("/api/collab/join", f"/api/collab/{s}/sync", f"/api/collab/{s}/save", f"/api/collab/{s}/leave"):
        assert owner.post(url, json=[1, 2]).status_code == 400, url
    assert owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "op": OP_A, "base": 0}).status_code == 400  # no op_id
    assert owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 10**20}).status_code == 400
    assert owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "op": OP_A, "op_id": "a", "base": "x"}).status_code == 400
    assert owner.post(f"/api/collab/{s}/save", json={"body": {"a": 1}, "seq": 0}).status_code == 400
    assert owner.post(f"/api/collab/{s}/save", json={"body": "x" * 600_000, "seq": 0}).status_code == 413
    # A cursor path element is truncated, not stored and echoed at any length.
    owner.post(f"/api/collab/{s}/sync", json={"client": "k1", "seen": 0, "cursor": {"path": ["X" * 10000], "start": 0, "end": 0}})
    r = owner.post(f"/api/collab/{s}/sync", json={"client": "k2", "seen": 0}).get_json()
    assert len(r["cursors"][0]["cursor"]["path"][0]) == 64


def test_test_harness_exists_only_with_the_test_flag(tmp_path, monkeypatch):
    def make(flag):
        if flag:
            monkeypatch.setenv("MARKMIK_TEST_PAGES", "1")
        else:
            monkeypatch.delenv("MARKMIK_TEST_PAGES", raising=False)
        app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / f"{flag}.db"), "DATA_DIR": str(tmp_path),
                          "OWNER_PASSWORD": "testi-parool-123", "OWNER_PASSWORD_HASH": None, "SECRET_KEY": "t"})
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf"] = "t"
        c.post("/login", data={"parool": "testi-parool-123", "csrf": "t"})
        return app, c

    _, c = make(False)
    assert c.get("/_test/editor/proov").status_code == 404
    app, c = make(True)
    r = c.get("/_test/editor/proov")
    assert r.status_code == 200 and 'data-slug="proov"' in r.get_data(as_text=True)
    assert notes.by_slug(connect(app.config["DATABASE_PATH"]), "proov") is not None
    assert app.test_client().get("/_test/editor/proov").status_code in (302, 401)  # owner only
