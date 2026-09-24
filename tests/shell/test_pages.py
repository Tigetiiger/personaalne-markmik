"""Shell pages (/, /m/<slug>, /tana) and /api/tags."""

import pytest

from app import notes


@pytest.mark.parametrize("path", ["/", "/m/whatever", "/tana"])
def test_pages_need_login(client, path):
    r = client.get(path, headers={"Accept": "text/html"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]


@pytest.mark.parametrize("method, path", [("post", "/api/capture"), ("get", "/api/tags")])
def test_api_needs_login(client, method, path):
    assert getattr(client, method)(path, json={"text": "x"}).status_code == 401


def test_capture_needs_csrf(owner):
    assert owner.c.post("/api/capture", json={"text": "x"}).status_code == 403


@pytest.mark.parametrize("path, page, slug", [
    ("/", "notes", ""),
    ("/m/matemaatika", "notes", "matemaatika"),
    ("/m/pole-olemas", "notes", "pole-olemas"),  # unknown: the page says so after the API's 404
    ("/tana", "today", None),
])
def test_pages(owner, path, page, slug):
    r = owner.get(path)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f'data-page="{page}"' in html
    if slug is not None:
        assert f'id="note" data-slug="{slug}"' in html


def test_slug_is_escaped(owner):
    html = owner.get("/m/%22%3E%3Cb%3Ex").get_data(as_text=True)
    assert "<b>x" not in html and 'data-slug="&#34;&gt;&lt;b&gt;x"' in html


def test_tags(owner, conn):
    a = notes.create(conn, "Algebra")
    b = notes.create(conn, "Bioloogia")
    notes.create(conn, "Sildita")
    gone = notes.create(conn, "Kustutatud")
    notes.set_tags(conn, a["id"], ["kool", "Tähtis"])
    notes.set_tags(conn, b["id"], ["kool"])
    notes.set_tags(conn, gone["id"], ["kool"])
    notes.soft_delete(conn, gone["id"])
    assert owner.get("/api/tags").get_json() == [
        {"tag": "kool", "notes": [{"slug": "algebra", "title": "Algebra"}, {"slug": "bioloogia", "title": "Bioloogia"}]},
        {"tag": "Tähtis", "notes": [{"slug": "algebra", "title": "Algebra"}]},
    ]
