"""/j/<token> pages, /api/share/current and the owner's /api/shares."""

import re

from app import collab, notes, shares


def page_text(r):
    return r.get_data(as_text=True)


def csrf_from(html):
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def test_view_link_shows_the_rendered_note_privately(conn, note, link, browser):
    _, token = link(note["id"], "view")
    guest = browser()
    r = guest.get(f"/j/{token}")
    body = page_text(r)
    assert r.status_code == 200
    assert 'data-page="share-view"' in body and "Ainult vaatamiseks" in body
    assert "<h1>Jagatud</h1>" in body
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["X-Robots-Tag"] == "noindex"
    assert r.headers["Cache-Control"] == "no-store"
    assert 'id="nav"' not in body and "Logi välja" not in body
    # The owner's other notes are text only; outside links stay links.
    assert '<a rel="noopener noreferrer">teist</a>' in body and "/m/teine" not in body
    assert 'href="https://example.com/a"' in body
    with guest.c.session_transaction() as s:
        assert s["share_id"] and "guest_name" not in s


def test_edit_link_asks_a_name_first_and_it_can_be_changed(conn, note, link, browser):
    row, token = link(note["id"], "edit")
    guest = browser()
    r = guest.get(f"/j/{token}")
    assert r.status_code == 200 and 'data-page="share-name"' in page_text(r) and "Sinu nimi" in page_text(r)
    assert 'id="share-editor"' not in page_text(r)
    csrf = csrf_from(page_text(r))
    # Without the CSRF field the name is refused.
    assert guest.c.post(f"/j/{token}", data={"nimi": "Mari"}).status_code == 403
    r = guest.c.post(f"/j/{token}", data={"nimi": "  Mari  ", "csrf": csrf})
    assert r.status_code == 302 and r.headers["Location"] == f"/j/{token}"
    r = guest.get(f"/j/{token}")
    body = page_text(r)
    assert 'data-page="share-edit"' in body and f'data-slug="{note["slug"]}"' in body
    assert '<b id="share-me">Mari</b>' in body and 'href="?nimi"' in body
    r = guest.get(f"/j/{token}?nimi")
    assert 'data-page="share-name"' in page_text(r) and 'value="Mari"' in page_text(r)
    guest.c.post(f"/j/{token}", data={"nimi": "<b>Jüri</b>", "csrf": csrf})
    body = page_text(guest.get(f"/j/{token}"))
    assert "&lt;b&gt;Jüri&lt;/b&gt;" in body and "<b>Jüri</b>" not in body
    assert shares.get(conn, row["id"])["last_used_at"] is not None


def test_the_owner_opens_an_edit_link_without_a_name(owner, note, link):
    _, token = link(note["id"], "edit")
    body = page_text(owner.get(f"/j/{token}"))
    assert 'data-page="share-edit"' in body and 'id="share-me"' not in body


def test_unknown_revoked_expired_and_deleted_links_look_the_same(conn, note, other, link, browser, expire):
    revoked, revoked_token = link(note["id"], "edit")
    shares.revoke(conn, revoked["id"])
    expired, expired_token = link(note["id"], "view")
    expire(expired["id"])
    _, deleted_token = link(other["id"], "view")
    notes.soft_delete(conn, other["id"])
    guest = browser()
    answers = [guest.get(f"/j/{t}") for t in ("pole-olemas-token-1234", revoked_token, expired_token, deleted_token)]
    assert {r.status_code for r in answers} == {404}
    assert len({page_text(r) for r in answers}) == 1
    assert len({tuple(sorted((k, v) for k, v in r.headers.items() if k != "Date")) for r in answers}) == 1
    body = page_text(answers[0])
    assert "Linki ei leitud" in body and "Jagatud" not in body
    assert answers[0].headers["Referrer-Policy"] == "no-referrer"
    with guest.c.session_transaction() as s:
        assert "share_id" not in s
    # A POST (the name form) gets the same answer.
    assert guest.post(f"/j/{revoked_token}", data={"nimi": "x"}).status_code == 404


def test_share_links_are_rate_limited(note, link, browser):
    _, token = link(note["id"], "view")
    guest = browser()
    codes = [guest.get(f"/j/{token if i % 2 else 'vale'}").status_code for i in range(31)]
    assert codes[:30] == [404, 200] * 15 and codes[30] == 429


def test_current_follows_the_note(conn, note, link, browser):
    _, token = link(note["id"], "view")
    guest = browser()
    guest.get(f"/j/{token}")
    data = guest.get("/api/share/current").get_json()
    assert data["note"] == {"slug": note["slug"], "title": "Jagatud"} and data["mode"] == "view"
    assert '<a rel="noopener noreferrer">teist</a>' in data["html"] and 'href="https://example.com/a"' in data["html"]
    notes.write_body(conn, note["id"], "# Jagatud\n\n<img src=x onerror=alert(1)><style>body{display:none}</style>Uus tekst\n")
    later = guest.get("/api/share/current").get_json()
    assert later["rev"] == data["rev"] + 1
    assert "Uus tekst" in later["html"]
    assert "onerror" not in later["html"] and "<style" not in later["html"] and "display" not in later["html"]


def test_links_to_this_site_are_plain_for_guests(app, conn, note, link, browser):
    body = "\n\n".join([
        "# Jagatud",
        "[a](/m/teine) [b](m/teine) [c](../x) [d](//evil.example/x) [e](http://localhost/m/teine)",
        "[f](https://example.com/?a=1&b=2) [g](mailto:a@b.ee) [h](#alla)",
        'Tekst <a href="/m/teine" title="x">i</a> lõpp',
    ])
    notes.write_body(conn, note["id"], body + "\n")
    _, token = link(note["id"], "view")
    html = browser().get(f"/j/{token}").get_data(as_text=True)
    doc = html.split('id="share-doc"', 1)[1]
    for text in "abcdei":
        assert re.search(rf"<a(?: [^>]*)?>{text}</a>", doc) and not re.search(rf'<a href="[^"]*"[^>]*>{text}</a>', doc), text
    assert '<a href="https://example.com/?a=1&amp;b=2"' in doc
    assert '<a href="mailto:a@b.ee"' in doc and '<a href="#alla"' in doc
    assert "/m/teine" not in doc


def test_owner_creates_lists_and_revokes_links(conn, owner, note, browser):
    r = owner.post("/api/shares", json={"slug": note["slug"], "mode": "edit", "expires_at": "2099-01-01T00:00:00Z"})
    assert r.status_code == 201
    made = r.get_json()
    assert set(made) == {"id", "url", "mode", "created_at", "expires_at", "last_used_at"}
    assert made["mode"] == "edit" and made["expires_at"] == "2099-01-01T00:00:00Z" and made["last_used_at"] is None
    assert re.fullmatch(r"http://localhost/j/[A-Za-z0-9_-]{22}", made["url"])
    token = made["url"].rsplit("/", 1)[1]
    view = owner.post("/api/shares", json={"slug": note["slug"], "mode": "view"}).get_json()
    guest = browser()
    assert guest.get(f"/j/{token}").status_code == 200

    listed = owner.get(f"/api/shares?slug={note['slug']}").get_json()
    assert [s["id"] for s in listed] == [view["id"], made["id"]]
    assert all(s["url"] is None for s in listed)  # the URL is shown once, at creation
    assert listed[1]["last_used_at"] is not None

    assert owner.delete(f"/api/shares/{made['id']}").status_code == 204
    assert [s["id"] for s in owner.get(f"/api/shares?slug={note['slug']}").get_json()] == [view["id"]]
    assert guest.get(f"/j/{token}").status_code == 404
    assert guest.get("/api/share/current").status_code == 401


def test_owner_api_errors(owner, note):
    assert owner.get("/api/shares?slug=puudub").status_code == 404
    assert owner.get("/api/shares").status_code == 404
    assert owner.post("/api/shares", json={"slug": "puudub", "mode": "view"}).status_code == 404
    for body in ({"slug": note["slug"], "mode": "admin"}, {"slug": note["slug"]}, {"slug": note["slug"], "mode": "view", "expires_at": "2000-01-01T00:00:00Z"},
                 {"slug": note["slug"], "mode": "view", "expires_at": "homme"}, {"slug": note["slug"], "mode": "view", "expires_at": 5}):
        r = owner.post("/api/shares", json=body)
        assert r.status_code == 400 and r.get_json()["error"], body
    assert owner.post("/api/shares", data="[]", content_type="application/json").status_code == 400
    assert owner.delete("/api/shares/999").status_code == 404
    assert owner.c.post("/api/shares", json={"slug": note["slug"], "mode": "view"}).status_code == 403  # no CSRF


def test_a_view_page_carries_no_inline_script_and_the_editor_page_loads_none_of_its_own(note, link, browser):
    _, view = link(note["id"], "view")
    _, edit = link(note["id"], "edit")
    guest = browser()
    for token in (view, edit):
        body = page_text(guest.get(f"/j/{token}"))
        assert not re.search(r"<script(?![^>]*\bsrc=)", body)


def test_guest_edit_page_joins_only_its_note(conn, note, other, link, browser):
    _, token = link(note["id"], "edit")
    guest = browser()
    guest.get(f"/j/{token}")
    joined = guest.post("/api/collab/join", json={"slug": note["slug"], "client": "t1"})
    assert joined.status_code == 200 and joined.get_json()["guest"] is True
    assert guest.post("/api/collab/join", json={"slug": other["slug"], "client": "t1"}).status_code == 404
    assert collab.participants(joined.get_json()["session"]) == ["Külaline (külaline)"]
