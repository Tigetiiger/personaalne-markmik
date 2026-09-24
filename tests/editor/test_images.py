"""Picture upload and serving (/pildid, /api/images) and the SVG sanitiser. Ported from Steam's
test_edit.py, plus the guest rules."""

import base64
import io

from app import collab, notes, svg

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def upload(c, *files):
    return c.post("/pildid", data={"file[]": [(io.BytesIO(data), name) for name, data in files]}, content_type="multipart/form-data")


def test_upload_requires_login(client):
    assert upload(client, ("a.png", PNG)).status_code == 401
    assert client.get("/api/images").status_code == 401


def test_upload_saves_images_and_serves_them(owner, app):
    body = upload(owner, ("foto õue.png", PNG), ("paha.exe", b"x")).get_json()
    assert body["code"] == 0
    assert body["data"]["errFiles"] == ["paha.exe"]
    url = body["data"]["succMap"]["foto õue.png"]
    assert url.startswith("/pildid/") and url.endswith(".png")
    assert owner.get(url).data == PNG
    assert (app.config["DATA_DIR"] and (__import__("pathlib").Path(app.config["DATA_DIR"]) / "pildid" / url.split("/")[-1]).is_file())
    # Same bytes again under another name: same file, no duplicate.
    again = upload(owner, ("teine.png", PNG)).get_json()
    assert again["data"]["succMap"]["teine.png"] == url


def test_gallery_lists_every_picture_newest_first(owner):
    first = upload(owner, ("robot.png", PNG)).get_json()["data"]["succMap"]["robot.png"]
    upload(owner, ("teine.gif", b"GIF89a"))
    listing = owner.get("/api/images").get_json()
    assert [(p["original_name"], p["url"].startswith("/pildid/")) for p in listing] == [("teine.gif", True), ("robot.png", True)]
    assert listing[1] == {"name": first.split("/")[-1], "url": first, "original_name": "robot.png"}
    assert owner.get("/api/images").headers["Cache-Control"] == "no-store"


def test_unknown_names_are_404(owner):
    assert owner.get("/pildid/../test.db").status_code == 404
    assert owner.get("/pildid/" + "a" * 40 + ".png").status_code == 404


def test_oversized_file_is_refused(owner, monkeypatch):
    from app import images
    monkeypatch.setattr(images, "MAX_BYTES", 10)
    assert upload(owner, ("suur.png", PNG)).get_json()["data"]["errFiles"] == ["suur.png"]


def test_guest_sees_only_pictures_their_note_shows(owner, guest, conn):
    shown = upload(owner, ("naha.png", PNG)).get_json()["data"]["succMap"]["naha.png"]
    hidden = upload(owner, ("peidus.gif", b"GIF89a")).get_json()["data"]["succMap"]["peidus.gif"]
    live = upload(owner, ("uus.gif", b"GIF89a-uus")).get_json()["data"]["succMap"]["uus.gif"]
    note = notes.create(conn, "Jagatud", body=f"# Jagatud\n\n![a]({shown})\n")
    other = notes.create(conn, "Teine", body=f"# Teine\n\n![b]({hidden})\n")
    g = guest(note["id"], "view")
    assert g.get(shown).data == PNG
    assert g.get(hidden).status_code == 403
    assert g.get("/api/images").status_code == 401  # no gallery for guests
    # Inserted by a co-editor a moment ago, not saved yet: in the live session's ops.
    s = collab.join(conn, note["id"], "k1", {"name": "Omanik"})["session"]
    assert g.get(live).status_code == 403
    collab.sync(conn, s, "k1", {"name": "Omanik"}, 0, [2, {"i": {"name": "paragraph", "text": f"![u]({live})"}}], "a1", 0)
    assert g.get(live).status_code == 200
    assert other["id"] != note["id"]


def test_guest_upload_needs_an_edit_share(guest, note):
    viewer = guest(note["id"], "view")
    assert upload(viewer, ("a.png", PNG)).status_code == 401
    editor = guest(note["id"], "edit")
    url = upload(editor, ("a.png", PNG)).get_json()["data"]["succMap"]["a.png"]
    assert editor.get(url).data == PNG  # their own upload, before the note is saved with it
    assert viewer.get(url).status_code == 403


def test_svg_upload_is_cleaned_and_served_sandboxed(owner):
    dirty = (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script>'
        b'<a href="javascript:alert(3)"><circle r="4" onclick="x()"/></a>'
        b'<foreignObject><body/></foreignObject><image href="/pildid/a.png"/></svg>'
    )
    body = upload(owner, ("x.svg", dirty), ("paha.svg", b"<svg><!DOCTYPE x []>"), ("ok.png", PNG)).get_json()
    assert body["data"]["errFiles"] == ["paha.svg"] and sorted(body["data"]["succMap"]) == ["ok.png", "x.svg"]
    # Inkscape/Illustrator files: BOM and a plain DOCTYPE are fine, an internal subset is not.
    inkscape = "﻿<?xml version=\"1.0\"?>\n<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\" \"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd\">\n<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"1\" height=\"1\"/></svg>"
    ok = upload(owner, ("ink.svg", inkscape.encode())).get_json()
    assert list(ok["data"]["succMap"]) == ["ink.svg"]
    assert "<rect" in owner.get(ok["data"]["succMap"]["ink.svg"]).get_data(as_text=True)
    r = owner.get(body["data"]["succMap"]["x.svg"])
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.mimetype == "image/svg+xml"
    out = r.get_data(as_text=True)
    assert "script" not in out and "onload" not in out and "onclick" not in out and "javascript" not in out
    assert "foreignObject" not in out and "<circle" in out and 'href="/pildid/a.png"' in out
    assert "sandbox" not in owner.get(body["data"]["succMap"]["ok.png"]).headers["Content-Security-Policy"]


def test_svg_canvas_background_is_removed_but_other_rects_stay():
    out = svg.clean(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" width="100" height="50">'
        b'<rect x="0" y="0" width="100" height="50" fill="#ffffff"/>'
        b'<rect x="0" y="0" width="100" height="50" fill="#ff0000"/>'
        b'<rect x="10" y="10" width="20" height="20" fill="white"/></svg>'
    ).decode()
    assert out.count("<rect") == 2 and 'fill="#ffffff"' not in out and 'fill="white"' in out


def test_svg_entities_stay_text_and_styles_lose_external_fetches():
    out = svg.clean(
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(http://evil.tld/a.css); .a{background:url(http://evil.tld/b.png)} .b{background:url(data:image/png;base64,AA)}</style>'
        b'<text>&LT;style&GT;x&LT;/style&GT;</text></svg>'
    ).decode()
    assert "<style>x</style>" not in out and "&lt;style&gt;" in out
    assert "evil.tld" not in out and "data:image/png" in out


def test_svg_loses_foreign_elements_and_external_urls_in_attributes():
    out = svg.clean(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xhtml">'
        b'<x:iframe src="javascript:alert(1)"/><linearGradient id="g"/>'
        b'<rect style="fill:url(https://evil.example/a)" width="5" height="5"/>'
        b'<rect fill="url(#g)" width="5" height="5"/><circle fill="url( https://evil.example/b)" r="1"/></svg>'
    ).decode()
    assert "iframe" not in out and "evil.example" not in out
    assert 'fill="url(#g)"' in out and "<circle" in out


def test_guest_upload_is_one_file_within_the_share_quota(guest, note, monkeypatch, conn):
    from app.views import images as views
    g = guest(note["id"], "edit")
    two = upload(g, ("a.png", PNG), ("b.gif", b"GIF89a"))
    assert two.status_code == 400 and "error" in two.get_json()
    monkeypatch.setattr(views, "SHARE_QUOTA", len(PNG) + 10)
    assert upload(g, ("a.png", PNG)).status_code == 200
    r = upload(g, ("b.gif", b"GIF89a-veel"))
    assert r.status_code == 413 and "error" in r.get_json()
    # The count is on the server, not in the browser: a fresh browser of the same share is full too.
    share_id = conn.execute("SELECT id FROM shares WHERE note_id = ? AND mode = 'edit'", (note["id"],)).fetchone()["id"]
    other = type(g)(g.c.application.test_client())
    with other.c.session_transaction() as s:
        s["share_id"] = share_id
    assert upload(other, ("c.gif", b"GIF89a-kolmas")).status_code == 413


def test_live_session_counts_only_pictures_its_ops_insert(owner, guest, conn):
    pic = upload(owner, ("p.png", PNG)).get_json()["data"]["succMap"]["p.png"]
    note = notes.create(conn, "Jagatud", body="# Jagatud\n")
    viewer = guest(note["id"], "view")
    s = collab.join(conn, note["id"], "k1", {"name": "Omanik"})["session"]
    # Named as a path key, not inserted text: no reference.
    collab.sync(conn, s, "k1", {"name": "Omanik"}, 0, [pic, {"r": True}], "a1", 0)
    assert viewer.get(pic).status_code == 403
    collab.sync(conn, s, "k1", {"name": "Omanik"}, 1, [1, {"i": {"name": "paragraph", "text": f"![p]({pic})"}}], "a2", 1)
    assert viewer.get(pic).status_code == 200
    # Once the note moves on without that session, its ops count no more.
    conn.execute("UPDATE notes SET rev = rev + 1 WHERE id = ?", (note["id"],))
    assert viewer.get(pic).status_code == 403
