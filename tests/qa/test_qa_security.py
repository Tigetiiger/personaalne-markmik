"""Whole-app checks at the seams between packages (docs/SECURITY-REVIEW.md). The per-route
permission matrix lives in tests/share/test_share_access.py."""

import re

import pytest

from app import notes, security, shares
from app.views.core import _safe_next

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.I)
INLINE_HANDLER = re.compile(r"<[^>]+\son[a-z]+\s*=", re.I)


def state_changing(app):
    for rule in app.url_map.iter_rules():
        url = re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?\w+>", lambda m: "1" if m.group(1) == "int" else "x", rule.rule)
        for method in sorted(rule.methods - {"GET", "HEAD", "OPTIONS"}):
            yield rule.endpoint, method, url


def test_every_state_change_needs_the_csrf_token_even_from_the_owner(app, owner):
    raw = owner.c  # the logged-in test client without the CSRF header
    let_through = []
    for endpoint, method, url in state_changing(app):
        if endpoint == "core.login":
            continue
        for kwargs in ({"json": {}}, {"data": {"x": "1"}}, {"data": {"file[]": []}, "content_type": "multipart/form-data"}):
            security._hits.clear()
            r = raw.open(url, method=method, **kwargs)
            if r.status_code != 403:
                let_through.append(f"{method} {url} ({endpoint}) {kwargs} -> {r.status_code}")
    assert not let_through


def test_a_beacon_carries_its_token_in_the_json_body(app, owner, conn):
    note = notes.create(conn, "Märge")
    sid = owner.post("/api/collab/join", json={"slug": note["slug"], "client": "a"}).get_json()["session"]
    raw = owner.c
    assert raw.post(f"/api/collab/{sid}/leave", json={"client": "a", "csrf": "vale"}).status_code == 403
    assert raw.post(f"/api/collab/{sid}/leave", json={"client": "a", "csrf": owner.token()}).status_code == 204


@pytest.mark.parametrize("target", [
    "/\t/evil.example", "/\n/evil.example", "/\\evil.example", "/\\/evil.example", "//evil.example",
    "https://evil.example", "/\x7f/evil.example",
])
def test_login_never_redirects_off_the_site(app, client, target):
    assert _safe_next(target) == "/"
    r = client.c.post("/login", query_string={"next": target}, data={"parool": "vale"})
    assert r.status_code == 403  # wrong password: no redirect at all
    r = client.c.post("/login", query_string={"next": target}, data={"parool": app.config["OWNER_PASSWORD"]})
    assert r.status_code == 302 and r.headers["Location"] == "/"


def test_login_still_returns_to_a_page_on_the_site(app, client):
    r = client.c.post("/login", query_string={"next": "/m/tere?x=1"}, data={"parool": app.config["OWNER_PASSWORD"]})
    assert r.headers["Location"] == "/m/tere?x=1"


def html_pages(app):
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and not rule.rule.startswith(("/api/", "/_test", "/static", "/muya", "/ics/", "/pildid")):
            yield re.sub(r"<[^>]+>", "x", rule.rule)


def test_every_page_has_the_strict_csp_and_no_inline_script(app, owner, conn, client):
    note = notes.create(conn, "Jagatud", body="# Jagatud\n\n<script>alert(1)</script><img src=x onerror=alert(1)>\n")
    notes.create(conn, "x")
    seen = []
    for url in list(html_pages(app)) + ["/m/jagatud"]:
        seen.append((url, owner.get(url)))
    for mode in ("view", "edit"):
        _, token = shares.create(conn, note["id"], mode)
        guest = type(client)(app.test_client())
        seen.append((f"/j/<{mode}>", guest.get(f"/j/{token}")))
    seen.append(("/login (anonymous)", client.get("/login")))
    wrong = []
    for url, r in seen:
        if r.mimetype != "text/html":
            continue
        page = r.get_data(as_text=True)
        if r.headers.get("Content-Security-Policy") != security.CSP:
            wrong.append(f"{url}: CSP {r.headers.get('Content-Security-Policy')!r}")
        if INLINE_SCRIPT.search(page) or INLINE_HANDLER.search(page):
            wrong.append(f"{url}: inline script {(INLINE_SCRIPT.search(page) or INLINE_HANDLER.search(page)).group(0)!r}")
    assert len([1 for _, r in seen if r.mimetype == "text/html" and r.status_code == 200]) >= 10
    assert not wrong


def test_editor_files_stay_inside_the_build(client):
    for name in ("..%2F..%2Fpyproject.toml", "../../pyproject.toml", "..%2F..%2Fapp%2F__init__.py"):
        r = client.get(f"/muya/dev/{name}", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 404, name


def test_a_password_change_logs_out_every_other_session(app, owner):
    other = type(owner)(app.test_client())
    assert other.c.post("/login", data={"parool": app.config["OWNER_PASSWORD"]}).status_code == 302
    assert other.get("/api/notes/tree").status_code == 200

    new = "uus-parool-456"
    r = owner.post("/api/settings/password", json={"old": app.config["OWNER_PASSWORD"], "new": new})
    assert r.status_code == 204
    assert owner.get("/api/notes/tree").status_code == 200  # the session that changed it stays
    assert other.get("/api/notes/tree").status_code == 401
    assert other.get("/api/notes/tree").status_code == 401  # and stays out
    assert other.c.post("/login", data={"parool": app.config["OWNER_PASSWORD"]}).status_code == 403
    assert other.c.post("/login", data={"parool": new}).status_code == 302
    assert other.get("/api/notes/tree").status_code == 200

    # A second change ends the session that made the first one too.
    assert other.post("/api/settings/password", json={"old": new, "new": "kolmas-parool-789"}).status_code == 204
    assert owner.get("/api/notes/tree").status_code == 401
    assert other.get("/api/notes/tree").status_code == 200


def test_a_failed_password_change_logs_out_nobody(app, owner):
    other = type(owner)(app.test_client())
    other.c.post("/login", data={"parool": app.config["OWNER_PASSWORD"]})
    assert owner.post("/api/settings/password", json={"old": app.config["OWNER_PASSWORD"], "new": "lühike"}).status_code == 400
    assert owner.post("/api/settings/password", json={"old": "vale-parool", "new": "piisavalt-pikk"}).status_code == 400
    assert other.get("/api/notes/tree").status_code == 200
