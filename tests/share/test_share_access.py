"""Who reaches what: every route in app.url_map × {anonymous, view guest, edit guest, guest of a
revoked link, guest of an expired link, owner}.

The rules come from the app itself, so a route added later is covered without touching this file:
a route with no marking is owner-only and every visitor but the owner must be refused. A route
that lets anyone else in (@auth.public, @auth.guest) fails the test until it is listed below.
Which note a guest may touch is checked with real sessions further down."""

import io
import re

import pytest
from flask import session
from werkzeug.exceptions import HTTPException

from app import auth, collab, images, notes, security, shares

PUBLIC = {"static", "core.login", "core.muya", "core.manifest", "share.page", "calendar.ics_feed",
          "meedia_jaga.page", "meedia_jaga.file", "meedia_jaga.preview",  # the token is the gate
          "collab.test_guest"}  # the last only with MARKMIK_TEST_PAGES=1
GUEST = {  # endpoint -> the share mode it needs; the endpoint itself checks the note
    "collab.join": "view", "collab.sync": "view", "collab.leave": "view", "collab.save": "edit",
    "images.image": "view", "images.upload": "edit", "share.current": "view",
}
ACTORS = ("anonymous", "guest-view", "guest-edit", "revoked", "expired", "owner")


def routes(app):
    """(endpoint, method, url) for every route, with a stand-in value for each URL part."""
    for rule in app.url_map.iter_rules():
        url = re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?\w+>", lambda m: "1" if m.group(1) == "int" else "x", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield rule.endpoint, method, url


def may_enter(endpoint, actor):
    if endpoint in PUBLIC or actor == "owner":
        return True
    need = GUEST.get(endpoint)
    return actor == "guest-edit" and need is not None or actor == "guest-view" and need == "view"


def refused(r):
    return r.status_code == 401 or (r.status_code == 302 and "/login" in r.headers["Location"])


@pytest.fixture
def world(conn, note, other, link, expire):
    view, view_token = link(note["id"], "view")
    edit, edit_token = link(note["id"], "edit")
    revoked, _ = link(note["id"], "edit")
    shares.revoke(conn, revoked["id"])
    expired, _ = link(note["id"], "edit", "2099-01-01T00:00:00Z")
    expire(expired["id"])
    return {"guest-view": view["id"], "guest-edit": edit["id"], "revoked": revoked["id"], "expired": expired["id"],
            "tokens": {"view": view_token, "edit": edit_token}}


@pytest.fixture
def visitors(world, browser, owner):
    made = {actor: browser(world.get(actor)) for actor in ACTORS if actor != "owner"}
    return {**made, "owner": owner}


def test_every_route_that_lets_others_in_is_listed(app):
    unlisted = []
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        public = rule.endpoint == "static" or getattr(view, "public", False)
        mode = getattr(view, "guest_mode", None)
        if public != (rule.endpoint in PUBLIC) or mode != GUEST.get(rule.endpoint):
            unlisted.append(f"{rule.rule} ({rule.endpoint}): public={public} guest={mode}")
    assert not unlisted, "classify these in tests/share/test_share_access.py:\n" + "\n".join(unlisted)


@pytest.mark.parametrize("actor", [a for a in ACTORS if a != "owner"])
def test_every_route_refuses_whom_it_should(app, visitors, actor):
    visitor = visitors[actor]
    wrong = []
    for endpoint, method, url in routes(app):
        security._hits.clear()
        r = visitor._send(method.lower(), url, json={}) if method != "GET" else visitor.get(url)
        if refused(r) == may_enter(endpoint, actor):
            wrong.append(f"{method} {url} ({endpoint}) -> {r.status_code}")
    assert not wrong, f"{actor}:\n" + "\n".join(wrong)


def test_the_owner_passes_every_gate(app):
    # Only the gate (auth.enforce), not the view: the owner's routes do real work (sync, sources).
    stuck = []
    for endpoint, method, url in routes(app):
        with app.test_request_context(url, method=method):
            session["owner"] = True
            auth.load_actor()
            try:
                passed = auth.enforce() is None
            except HTTPException:
                passed = False
            if not passed:
                stuck.append(f"{method} {url} ({endpoint})")
    assert not stuck


# --- The right note, the right mode ---------------------------------------------------------

def post(visitor, url, body):
    security._hits.clear()
    return visitor.post(url, json=body)


@pytest.fixture
def sessions(owner, note, other):
    """The owner is editing both notes: {note id: live session id}."""
    return {n["id"]: post(owner, "/api/collab/join", {"slug": n["slug"], "client": "omanik"}).get_json()["session"] for n in (note, other)}


@pytest.fixture
def pictures(app, conn, note, other):
    """{note id: a picture only that note shows}."""
    found = {}
    with app.app_context():
        for n, data in ((note, b"png-jagatud"), (other, b"png-teine")):
            url = images.store(conn, data, "p.png", "Omanik")
            conn.execute("UPDATE notes SET body = body || ? WHERE id = ?", (f"\n![]({url})\n", n["id"]))
            images.allow_shown(conn, n["id"])  # as if it was there when the links were made
            found[n["id"]] = url
    return found


def op_body(client, base=0):
    return {"client": client, "seen": 0, "op": [1, "text", {"es": [0, "x"]}], "op_id": f"{client}-1", "base": base}


@pytest.mark.parametrize("actor", ["guest-view", "guest-edit"])
def test_a_guest_reaches_only_the_shared_note_and_only_as_far_as_the_mode(conn, visitors, note, other, pictures, sessions, actor):
    guest = visitors[actor]
    edit = actor == "guest-edit"
    mine, theirs = sessions[note["id"]], sessions[other["id"]]
    assert post(guest, "/api/collab/join", {"slug": note["slug"], "client": "g"}).status_code == 200
    assert post(guest, "/api/collab/join", {"slug": other["slug"], "client": "g"}).status_code == 404
    assert post(guest, f"/api/collab/{mine}/sync", {"client": "g", "seen": 0}).status_code == 200
    assert post(guest, f"/api/collab/{theirs}/sync", {"client": "g", "seen": 0}).status_code == 403
    assert post(guest, f"/api/collab/{theirs}/sync", op_body("g")).status_code == 403
    assert post(guest, f"/api/collab/{mine}/sync", op_body("g")).status_code == (200 if edit else 403)
    assert post(guest, f"/api/collab/{theirs}/save", {"body": "x", "seq": 1}).status_code == (403 if edit else 401)
    assert post(guest, f"/api/collab/{mine}/save", {"body": "# Jagatud\n", "seq": 0}).status_code == (204 if edit else 401)
    assert post(guest, f"/api/collab/{theirs}/leave", {"client": "g"}).status_code == 403
    assert post(guest, f"/api/collab/{mine}/leave", {"client": "g"}).status_code == 204
    assert guest.get(pictures[note["id"]]).status_code == 200
    assert guest.get(pictures[other["id"]]).status_code == 404
    assert guest.get("/api/images").status_code == 401
    upload = guest.post("/pildid", data={"file[]": (io.BytesIO(b"uus"), "u.png")}, content_type="multipart/form-data")
    assert upload.status_code == (200 if edit else 401)
    current = guest.get("/api/share/current").get_json()
    assert current["note"]["slug"] == note["slug"] and current["mode"] == ("edit" if edit else "view")
    ops = conn.execute("SELECT count(*) FROM collab_ops WHERE session_id = ?", (theirs,)).fetchone()[0]
    assert ops == 0
    assert notes.get(conn, other["id"])["body"].startswith("# Teine")


@pytest.mark.parametrize("actor", ["anonymous", "revoked", "expired"])
def test_without_a_valid_link_nothing_of_the_note_is_reached(visitors, note, pictures, sessions, actor):
    visitor = visitors[actor]
    mine = sessions[note["id"]]
    for url, body in (("/api/collab/join", {"slug": note["slug"], "client": "g"}), (f"/api/collab/{mine}/sync", {"client": "g", "seen": 0}),
                      (f"/api/collab/{mine}/sync", op_body("g")), (f"/api/collab/{mine}/save", {"body": "x", "seq": 1}),
                      (f"/api/collab/{mine}/leave", {"client": "g"})):
        assert post(visitor, url, body).status_code == 401, url
    assert visitor.get(pictures[note["id"]]).status_code == 401
    assert visitor.get("/api/share/current").status_code == 401


def test_a_guest_cannot_use_another_notes_session_id(conn, visitors, other, sessions):
    guest = visitors["guest-edit"]
    theirs = sessions[other["id"]]
    for url, body in ((f"/api/collab/{theirs}/sync", {"client": "g", "seen": 0}), (f"/api/collab/{theirs}/sync", op_body("g")),
                      (f"/api/collab/{theirs}/save", {"body": "# Võetud\n", "seq": 5}), (f"/api/collab/{theirs}/leave", {"client": "g", "error": "x"})):
        assert post(guest, url, body).status_code == 403, url
    assert conn.execute("SELECT closed FROM collab_sessions WHERE id = ?", (theirs,)).fetchone()["closed"] == 0
    assert post(guest, "/api/collab/999/sync", {"client": "g", "seen": 0}).status_code == 404


def test_a_link_revoked_mid_session_is_refused_at_the_next_request(conn, owner, browser, world, note):
    guest = browser()
    assert guest.get(f"/j/{world['tokens']['edit']}").status_code == 200
    joined = post(guest, "/api/collab/join", {"slug": note["slug"], "client": "g"}).get_json()
    sid = joined["session"]
    assert post(guest, f"/api/collab/{sid}/sync", op_body("g")).status_code == 200
    assert owner.delete(f"/api/shares/{world['guest-edit']}").status_code == 204
    assert post(guest, f"/api/collab/{sid}/sync", {"client": "g", "seen": 0}).status_code == 401
    assert post(guest, f"/api/collab/{sid}/sync", op_body("g2", base=1)).status_code == 401
    assert post(guest, f"/api/collab/{sid}/save", {"body": "# Pärast\n", "seq": 1}).status_code == 401
    assert guest.get("/api/share/current").status_code == 401
    assert "Pärast" not in notes.get(conn, note["id"])["body"]
    assert conn.execute("SELECT count(*) FROM collab_ops WHERE session_id = ?", (sid,)).fetchone()[0] == 1
    with guest.c.session_transaction() as s:
        assert "share_id" not in s  # forgotten: reloading the page shows the 404 page


def test_a_view_guest_posting_an_op_gets_403_and_nothing_is_stored(conn, browser, world, note):
    guest = browser()
    guest.get(f"/j/{world['tokens']['view']}")
    sid = post(guest, "/api/collab/join", {"slug": note["slug"], "client": "g"}).get_json()["session"]
    r = post(guest, f"/api/collab/{sid}/sync", op_body("g"))
    assert r.status_code == 403
    assert conn.execute("SELECT count(*) FROM collab_ops").fetchone()[0] == 0
    assert collab.participants(sid) == ["Külaline (külaline)"]
