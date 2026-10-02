"""Share links on their own host (docs/MEEDIA-PLAN.md, step 5)."""

import re

import pytest

from app import create_app
from app.meedia import index, shares, store
from tests.conftest import OWNER_PASSWORD, CsrfClient
from tests.meedia.samples import jpeg, video

HOST = "meedia.example.ee"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app with MEEDIA_PUBLIC_URL set, as when the tunnel serves the media host."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return create_app({
        "TESTING": True, "DATABASE_PATH": str(tmp_path / "test.db"), "DATA_DIR": str(tmp_path),
        "OWNER_PASSWORD": OWNER_PASSWORD, "OWNER_PASSWORD_HASH": None, "SECRET_KEY": "test",
        "MEEDIA_PUBLIC_URL": f"https://{HOST}",
    })


@pytest.fixture
def owner(app):
    c = CsrfClient(app.test_client())
    assert c.post("/login", data={"parool": OWNER_PASSWORD}).status_code == 302
    return c


class Guest:
    """A visitor on the media host, as Discord's bot or a friend."""

    def __init__(self, client):
        self.c = client

    def get(self, path, **kw):
        return self.c.get(path, base_url=f"https://{HOST}", **kw)


@pytest.fixture
def guest(app):
    return Guest(app.test_client())


@pytest.fixture
def media(app, conn):
    root = store.default_root()
    store.ensure(root)
    jpeg(root / "koer.jpg")
    jpeg(root / "kass.jpg", color=(9, 9, 9))
    video(root / "klipp.mp4")
    for name in ("koer.jpg", "kass.jpg", "klipp.mp4"):
        index.intake(conn, root, name, source="upload")
    index.update(conn, root, "koer.jpg", {"title": "Rex", "description": "Koer pargis"})
    return root


def make(owner, names, **kw):
    r = owner.post("/api/meedia/jagamised", json={"names": names, **kw})
    assert r.status_code == 201, r.json
    assert r.json["url"].startswith(f"https://{HOST}/s/")
    return r.json, r.json["url"].removeprefix(f"https://{HOST}")


def meta(html, prop):
    m = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)"', html)
    return m.group(1) if m else None


def test_picture_link_unfurls_without_cookies_or_scripts(owner, guest, media):
    share, path = make(owner, ["koer.jpg"])
    r = guest.get(path)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert meta(html, "og:title") == "Rex" and meta(html, "og:description") == "Koer pargis"
    assert meta(html, "og:image") == f"https://{HOST}{path}/p/koer.jpg?s=1600"
    assert meta(html, "og:video") is None
    assert "Set-Cookie" not in r.headers
    assert r.headers["Cache-Control"] == "private, max-age=300"
    assert "<script" not in html
    preview = guest.get(f"{path}/p/koer.jpg?s=1600")
    assert preview.status_code == 200 and preview.mimetype == "image/jpeg"
    assert "Laadi alla" not in html
    assert guest.get(f"{path}/f/koer.jpg").status_code == 404  # no download: the preview only


def test_video_link_plays_in_discord(owner, guest, media):
    _, path = make(owner, ["klipp.mp4"])
    html = guest.get(path).get_data(as_text=True)
    assert meta(html, "og:video") == f"https://{HOST}{path}/f/klipp.mp4"
    assert (meta(html, "og:video:type"), meta(html, "og:video:width"), meta(html, "og:video:height")) == ("video/mp4", "320", "240")
    assert meta(html, "og:image") == f"https://{HOST}{path}/p/klipp.mp4?s=1600"
    r = guest.get(f"{path}/f/klipp.mp4", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.data) == 10


def test_album_with_download(owner, guest, media):
    share, path = make(owner, ["kass.jpg", "koer.jpg"], title="Loomad", download=True, days=7)
    assert share["expires_at"] and share["download"]
    html = guest.get(path).get_data(as_text=True)
    assert meta(html, "og:title") == "Loomad"
    assert html.index("kass.jpg") < html.index("koer.jpg")
    assert html.count("Laadi alla") == 2
    r = guest.get(f"{path}/f/koer.jpg?lae=1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert guest.get(f"{path}/p/klipp.mp4").status_code == 404  # not in this link


def test_revoked_expired_and_unknown_links_are_the_same_404(owner, guest, media, conn):
    share, path = make(owner, ["koer.jpg"])
    _, old = make(owner, ["kass.jpg"])
    conn.execute("UPDATE media_shares SET expires_at = '2020-01-01T00:00:00Z' WHERE id != ?", (share["id"],))
    assert owner.delete(f"/api/meedia/jagamised/{share['id']}").json == {"ok": True}
    assert owner.delete(f"/api/meedia/jagamised/{share['id']}").status_code == 404
    for url in (path, f"{path}/p/koer.jpg", old, "/s/" + "x" * 43):
        assert guest.get(url).status_code == 404, url


def test_deleted_file_drops_out_of_its_link(owner, guest, media, conn):
    _, path = make(owner, ["koer.jpg", "kass.jpg"])
    index.remove(conn, media, "kass.jpg")
    html = guest.get(path).get_data(as_text=True)
    assert "kass.jpg" not in html and "koer.jpg" in html
    index.remove(conn, media, "koer.jpg")
    assert guest.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/login", "/", "/meedia", "/api/meedia", "/meedia/fail/koer.jpg", "/static/app.js", "/j/abc"])
def test_media_host_serves_nothing_else(guest, owner, media, path):
    assert guest.get(path).status_code == 404


def test_share_pages_are_not_served_on_the_app_host(owner, client, media):
    _, path = make(owner, ["koer.jpg"])
    assert client.get(path).status_code == 404
    assert owner.get(path).status_code == 404


def test_without_media_host_links_work_on_the_app_host(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "DATA_DIR": str(tmp_path),
                      "OWNER_PASSWORD": OWNER_PASSWORD, "OWNER_PASSWORD_HASH": None, "SECRET_KEY": "t"})
    c = CsrfClient(app.test_client())
    c.post("/login", data={"parool": OWNER_PASSWORD})
    root = store.default_root()
    store.ensure(root)
    jpeg(root / "a.jpg")
    from app.db import connect
    db = connect(str(tmp_path / "t.db"))
    index.intake(db, root, "a.jpg", source="upload")
    url = c.post("/api/meedia/jagamised", json={"names": ["a.jpg"]}).json["url"]
    assert url.startswith("http://localhost/s/")
    assert app.test_client().get(url.removeprefix("http://localhost")).status_code == 200


def test_owner_lists_links_and_filters_shared_files(owner, media):
    make(owner, ["koer.jpg"], title="Üks")
    make(owner, ["koer.jpg", "kass.jpg"])
    listed = owner.get("/api/meedia/jagamised?name=kass.jpg").json["shares"]
    assert [(s["files"], s["first"]) for s in listed] == [(2, "koer.jpg")]
    assert "url" not in listed[0]
    assert len(owner.get("/api/meedia/jagamised").json["shares"]) == 2
    assert sorted(i["name"] for i in owner.get("/api/meedia?jagatud=1").json["items"]) == ["kass.jpg", "koer.jpg"]


@pytest.mark.parametrize("body, error", [
    ({"names": []}, "Vali vähemalt üks fail."),
    ({"names": ["puudub.jpg"]}, "Mõnda faili pole enam."),
    ({"names": ["koer.jpg"], "days": 3}, "Vigane aegumise aeg."),
])
def test_bad_requests(owner, media, body, error):
    r = owner.post("/api/meedia/jagamised", json=body)
    assert r.status_code == 400 and r.json["error"] == error


def test_only_the_owner_makes_links(app, media):
    c = CsrfClient(app.test_client())
    assert c.post("/api/meedia/jagamised", json={"names": ["koer.jpg"]}).status_code in (302, 401, 403)


def test_token_is_stored_only_as_a_hash(owner, conn, media):
    _, path = make(owner, ["koer.jpg"])
    token = path.removeprefix("/s/")
    assert conn.execute("SELECT count(*) FROM media_shares WHERE token_hash = ?", (token,)).fetchone()[0] == 0
    assert shares.by_token(conn, token) is not None
