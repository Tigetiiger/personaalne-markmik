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
    for i in range(collab.MAX_CLIENTS):
        collab._present(1, f"b{i}:t", "x")
    with pytest.raises(collab.TooManyClients):
        collab._present(1, "uus:t", "x")
    collab._present(1, "b0:t2", "x")  # a browser already there: its new tab takes its old one's place
    assert "b0:t" not in collab._participants[1] and len(collab._participants[1]) == collab.MAX_CLIENTS


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
