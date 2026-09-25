"""What a hostile or broken client must not be able to do to a shared note: poison the op log,
act for another tab, flood the others' answers (review of wp/editor)."""

import json
from datetime import timedelta

import pytest

from app import collab
from app.util import iso, now_utc

OP = [0, "text", {"es": [0, "x"]}]
H = {"Content-Type": "application/json"}


def strict(data):
    """Parse as JSON that every browser accepts: no NaN or Infinity."""
    def refuse(name):
        raise ValueError(name)
    return json.loads(data, parse_constant=refuse)


def raw(client, url, text):
    return client.post(url, data=text, headers=H)


def join(client, slug, tab):
    return client.post("/api/collab/join", json={"slug": slug, "client": tab})


def test_nan_and_infinity_never_reach_the_op_log(owner, guest, note, conn):
    editor = guest(note["id"], "edit")
    s = join(editor, note["slug"], "e1").get_json()["session"]
    bad = [
        '{"client":"e1","seen":0,"op":[0,"text",{"es":[NaN]}],"op_id":"p1","base":0}',
        '{"client":"e1","seen":0,"op":[0,"text",{"es":[1e400,"x"]}],"op_id":"p2","base":0}',
        '{"client":"e1","seen":0,"cursor":{"path":[0],"start":Infinity,"end":0}}',
        '{"client":"e1","seen":1e400}',
        '{"client":"e1","seen":0,"op":{"junk":1},"op_id":"p3","base":0}',
        '{"client":"e1","seen":0,"op":[0,{"x":1}],"op_id":"p4","base":0}',
        '{"client":"e1","seen":0,"op":' + "[" * 100 + "]" * 100 + ',"op_id":"p5","base":0}',
    ]
    for text in bad:
        assert raw(editor, f"/api/collab/{s}/sync", text).status_code == 400, text[:60]
    assert collab.last_seq(conn, s) == 0
    r = join(owner, note["slug"], "o1")
    assert strict(r.data)["ops"] == []
    with pytest.raises(collab.BadOp):
        collab.sync(conn, s, "o1", {"name": "Omanik"}, 0, [0, "text", {"es": [float("nan")]}], "x", 0)
    # A number too big for a caret is dropped, the poll goes on.
    r = raw(editor, f"/api/collab/{s}/sync", '{"client":"e1","seen":0,"cursor":{"path":[0,"text"],"start":1e400,"end":0}}')
    assert r.status_code == 200 and strict(r.data)
    assert strict(owner.post(f"/api/collab/{s}/sync", json={"client": "o1", "seen": 0}).data)["cursors"][0]["cursor"] is None


def test_a_tab_is_spoken_for_only_by_its_own_browser(owner, guest, note, conn):
    s = join(owner, note["slug"], "own").get_json()["session"]
    owner.post(f"/api/collab/{s}/sync", json={"client": "own", "seen": 0, "cursor": {"path": [0, "text"], "start": 1, "end": 1}})
    viewer = guest(note["id"], "view", "Mari")
    r = viewer.post(f"/api/collab/{s}/sync", json={"client": "v1", "seen": 0}).get_json()
    # Others appear as small numbers, never as their tab ids.
    assert r["cursors"] == [{"id": 1, "name": "Omanik", "cursor": {"path": [0, "text"], "start": 1, "end": 1}}]
    assert "own" not in json.dumps(r)
    # Leaving or moving "own" from another browser touches only that browser's own tab of the name.
    viewer.post(f"/api/collab/{s}/leave", json={"client": "own"})
    editor = guest(note["id"], "edit", "Jaan")
    editor.post(f"/api/collab/{s}/sync", json={"client": "own", "seen": 0, "cursor": {"path": [0, "text"], "start": 5, "end": 5}})
    seen_by_viewer = viewer.post(f"/api/collab/{s}/sync", json={"client": "v1", "seen": 0}).get_json()["cursors"]
    assert {"id": 1, "name": "Omanik", "cursor": {"path": [0, "text"], "start": 1, "end": 1}} in seen_by_viewer
    assert "Omanik" in collab.participants(s)
    # Everyone gone from the list for a moment: the session still is not replaced under the owner.
    collab._participants[s].clear()
    assert join(viewer, note["slug"], "v2").get_json()["session"] == s
    r = owner.post(f"/api/collab/{s}/sync", json={"client": "own", "seen": 0, "op": OP, "op_id": "k1", "base": 0}).get_json()
    assert r.get("reset") is None and [o["client"] for o in r["ops"]] == ["own"]
    # The op's sender is named only to the sender.
    assert [o["client"] for o in viewer.post(f"/api/collab/{s}/sync", json={"client": "v2", "seen": 0}).get_json()["ops"]] == [""]


def test_one_browser_cannot_flood_a_session(owner, guest, note):
    s = join(owner, note["slug"], "own").get_json()["session"]
    editor = guest(note["id"], "edit")
    for i in range(40):
        editor.post(f"/api/collab/{s}/sync", json={"client": f"f{i}", "seen": 0, "cursor": {"path": ["x" * 64] * 32, "start": 0, "end": 0}})
    r = owner.post(f"/api/collab/{s}/sync", json={"client": "own", "seen": 0})
    assert len(r.get_json()["cursors"]) == collab.MAX_CLIENTS_PER_ACTOR
    assert len(r.data) < 20_000
    # A view-only guest shows no caret at all.
    viewer = guest(note["id"], "view")
    viewer.post(f"/api/collab/{s}/sync", json={"client": "v", "seen": 0, "cursor": {"path": [0, "text"], "start": 0, "end": 0}})
    cursors = owner.post(f"/api/collab/{s}/sync", json={"client": "own", "seen": 0}).get_json()["cursors"]
    assert [c["cursor"] for c in cursors if c["name"] == "Külaline (külaline)"][-1] is None


def test_a_full_session_refuses_more_browsers(note):
    view = {"name": "x", "role": "view", "share": None}
    for i in range(collab.MAX_CLIENTS):
        collab._present(1, f"b{i}:t", {**view, "share": i})
    with pytest.raises(collab.TooManyClients):
        collab._present(1, "uus:t", view)
    collab._present(1, "b0:t2", view)  # a browser already there: its new tab takes its old one's place
    assert "b0:t" not in collab._participants[1] and len(collab._participants[1]) == collab.MAX_CLIENTS
    # The owner and an edit guest are never turned away: the longest-idle view guest makes room.
    collab._present(1, "omanik:t", {"name": "Omanik"})
    collab._present(1, "muutja:t", {"name": "m", "role": "edit", "share": 99})
    clients = collab._participants[1]
    assert {"omanik:t", "muutja:t"} <= set(clients) and len(clients) == collab.MAX_CLIENTS
    assert "b1:t" not in clients and "b2:t" not in clients


def test_join_is_rate_limited(owner, note):
    codes = [join(owner, note["slug"], f"t{i}").status_code for i in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_prune_closes_dead_sessions_and_drops_old_ones(conn, note):
    s = collab.join(conn, note["id"], "a", {"name": "x"})["session"]
    collab.sync(conn, s, "a", {"name": "x"}, 0, OP, "a1", 0)
    conn.execute("UPDATE notes SET rev = rev + 1 WHERE id = ?", (note["id"],))
    collab.prune(conn)
    assert collab.get(conn, s)["closed"] == 1 and collab.last_seq(conn, s) == 1
    conn.execute("UPDATE collab_sessions SET last_active_at = ? WHERE id = ?", (iso(now_utc() - timedelta(days=collab.KEEP_DAYS + 1)), s))
    collab.prune(conn)
    assert collab.get(conn, s) is None and collab.last_seq(conn, s) == 0


def body_of(conn, note):
    return conn.execute("SELECT body FROM notes WHERE id = ?", (note["id"],)).fetchone()["body"]


def test_a_last_save_with_its_op_writes_only_if_that_op_got_its_place(conn, note):
    A, B = {"name": "a"}, {"name": "b"}
    s = collab.join(conn, note["id"], "a", A)["session"]
    collab.join(conn, note["id"], "b", B)
    # B's op took seq 1 before A's last op arrived (A had not heard of it).
    collab.sync(conn, s, "b", B, 0, OP, "b1", 0)
    assert collab.save(conn, s, "# Alus A\n", 1, A, client="a", op=[0, "text", {"es": [6, " A"]}], op_id="a1")
    assert body_of(conn, note) == "# Alus\n" and collab.last_seq(conn, s) == 1  # neither B's op lost nor A's text
    # In order: A's op is stored and the text saved at its place.
    assert collab.save(conn, s, "# xAlus A\n", 2, A, client="a", op=[0, "text", {"es": [7, " A"]}], op_id="a2")
    assert body_of(conn, note) == "# xAlus A\n" and collab.get(conn, s)["saved_seq"] == 2


def test_a_final_save_ends_the_session_so_the_next_join_starts_from_it(conn, note):
    A = {"name": "a"}
    s = collab.join(conn, note["id"], "a", A)["session"]
    collab.sync(conn, s, "a", A, 0, OP, "a1", 0)
    # Text with changes that never reached the log, and someone's op after it: refused.
    collab.sync(conn, s, "b", A, 1, OP, "b1", 1)
    assert collab.save(conn, s, "# xAlus ja veel\n", 1, A, final=True)
    assert body_of(conn, note) == "# Alus\n"
    assert collab.save(conn, s, "# xxAlus ja veel\n", 2, A, final=True)
    assert body_of(conn, note) == "# xxAlus ja veel\n" and collab.get(conn, s)["closed"] == 1
    j = collab.join(conn, note["id"], "c", A)  # at once, not after GONE_S
    assert j["session"] != s and j["base"] == "# xxAlus ja veel\n" and j["ops"] == []


def test_reset_says_whether_the_resent_op_is_already_saved(conn, note):
    A = {"name": "a"}
    s = collab.join(conn, note["id"], "a", A)["session"]
    collab.sync(conn, s, "a", A, 0, OP, "a1", 0)  # its answer lost
    collab.save(conn, s, "# xAlus\n", 1, {"name": "b"})  # a co-editor saved it
    collab.sync(conn, s, "a", A, 1, OP, "a2", 1)  # not saved
    conn.execute("UPDATE notes SET rev = rev + 1 WHERE id = ?", (note["id"],))
    assert collab.sync(conn, s, "a", A, 0, OP, "a1", 0) == {"reset": True, "saved": True}
    assert collab.sync(conn, s, "a", A, 1, OP, "a2", 1) == {"reset": True, "saved": False}


def test_view_share_holders_cannot_crowd_out_the_owner(owner, guest, note, client):
    s = join(owner, note["slug"], "own").get_json()["session"]
    viewers = [guest(note["id"], "view", f"v{i}") for i in range(6)]
    for i, v in enumerate(viewers):  # separate shares: at most MAX_CLIENTS_PER_SHARE each
        for t in range(collab.MAX_CLIENTS_PER_ACTOR):
            v.post(f"/api/collab/{s}/sync", json={"client": f"t{t}", "seen": 0})
    same_link = [type(client)(client.c.application.test_client()) for _ in range(3)]
    share_id = collab._participants[s][next(c for c, p in collab._participants[s].items() if p["share"])]["share"]
    for c in same_link:
        with c.c.session_transaction() as sess:
            sess["share_id"], sess["csrf"] = share_id, __import__("os").urandom(8).hex()
        for t in range(collab.MAX_CLIENTS_PER_ACTOR):
            c.post(f"/api/collab/{s}/sync", json={"client": f"x{t}", "seen": 0})
    shares = [p["share"] for p in collab._participants[s].values() if p["share"]]
    assert max(shares.count(x) for x in set(shares)) <= collab.MAX_CLIENTS_PER_SHARE
    assert len(collab._participants[s]) == collab.MAX_CLIENTS  # full of view guests
    late = guest(note["id"], "view", "hiline")
    assert late.post(f"/api/collab/{s}/sync", json={"client": "h", "seen": 0}).status_code == 429
    editor = guest(note["id"], "edit", "Muutja")
    assert join(editor, note["slug"], "e").status_code == 200
    fresh_owner = type(owner)(owner.c.application.test_client())
    with fresh_owner.c.session_transaction() as sess:
        sess["owner"], sess["csrf"] = True, "o" * 20
    assert join(fresh_owner, note["slug"], "own2").status_code == 200


def test_join_tells_whether_a_kept_copy_got_saved(owner, note, conn):
    s = join(owner, note["slug"], "t1").get_json()["session"]
    def ask(op_id):
        r = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "t9", "copies": [{"tab": "t1", "op_id": op_id}]})
        return r.get_json()["copies"][0]
    assert ask("puudub") == {"first": False, "all": False}
    # The op in the live session: part of what the note shows, but its queued changes are not.
    owner.post(f"/api/collab/{s}/sync", json={"client": "t1", "seen": 0, "op": OP, "op_id": "a1", "base": 0})
    assert ask("a1") == {"first": True, "all": False}
    # A final save with it: everything was written.
    owner.post(f"/api/collab/{s}/save", json={"client": "t1", "body": "# xAlus veel\n", "seq": 2,
                                             "op": [0, "text", {"es": [1, "y"]}], "op_id": "a2", "final": True})
    assert collab.get(conn, s)["closed"] == 1
    assert ask("a2") == {"first": True, "all": True}
    # Another browser cannot ask about this one's tabs.
    other = type(owner)(owner.c.application.test_client())
    with other.c.session_transaction() as sess:
        sess["owner"], sess["csrf"] = True, "teine" * 5
    r = other.post("/api/collab/join", json={"slug": note["slug"], "client": "x", "copies": [{"tab": "t1", "op_id": "a2"}]})
    assert r.get_json()["copies"] == [{"first": False, "all": False}]


def test_a_session_closed_otherwise_does_not_count_as_a_written_final_save(owner, note, conn):
    s = join(owner, note["slug"], "t1").get_json()["session"]
    owner.post(f"/api/collab/{s}/sync", json={"client": "t1", "seen": 0, "op": OP, "op_id": "a1", "base": 0})
    # The tab's final save never arrived; a co-editor saved at its op, then the session closed another way.
    owner.post(f"/api/collab/{s}/save", json={"body": "# xAlus\n", "seq": 1})
    collab.close_all(conn, note["id"])
    r = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "t9", "copies": [{"tab": "t1", "op_id": "a1"}, {"tab": "t1", "op_id": ""}]})
    assert r.get_json()["copies"] == [{"first": True, "all": False}, {"first": False, "all": False}]
    # The record goes with the session when it is pruned.
    conn.execute("INSERT INTO settings (key, value) VALUES ('collab_final:999', '{}')")
    collab.prune(conn)
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'collab_final:999'").fetchone() is None
