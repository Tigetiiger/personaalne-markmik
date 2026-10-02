"""The iPhone home-screen app (Safari: Jaga → Lisa avakuvale): its icon, title and manifest are
there before login, because iOS reads them from the page it is on, usually the login page."""

import json

import pytest

HTML = {"Accept": "text/html"}


def test_login_page_has_the_home_screen_tags(client):
    page = client.get("/login", headers=HTML).text
    assert '<link rel="apple-touch-icon" href="/static/ikoonid/ikoon-180.png">' in page
    assert '<meta name="apple-mobile-web-app-title" content="Märkmik">' in page
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page
    assert 'viewport-fit=cover' in page  # the safe-area insets in the CSS need it
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page


@pytest.mark.parametrize("size", [180, 192, 512])
def test_png_icons_are_served_without_login(client, size):
    r = client.get(f"/static/ikoonid/ikoon-{size}.png")
    assert r.status_code == 200 and r.mimetype == "image/png"
    # PNG header, then the IHDR width and height: the size the name promises.
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(r.data[16:20], "big") == size and int.from_bytes(r.data[20:24], "big") == size


def test_manifest_lists_the_png_icons_and_opens_standalone(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    m = json.loads(r.data)
    assert m["display"] == "standalone" and m["start_url"] == "/tana"
    pngs = {i["sizes"]: i for i in m["icons"] if i["type"] == "image/png"}
    assert set(pngs) == {"192x192", "512x512"} and all("maskable" in i["purpose"] for i in pngs.values())
    for i in m["icons"]:
        assert client.get(i["src"]).status_code == 200
