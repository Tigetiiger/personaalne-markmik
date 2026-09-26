"""Picture upload and serving (/pildid, /api/images) and the SVG sanitiser. Ported from Steam's
test_edit.py, plus the guest rules."""

import base64
import io

from app import notes, svg

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


def test_guest_sees_only_the_pictures_allowed_for_their_note(owner, guest, conn):
    shown = upload(owner, ("naha.png", PNG)).get_json()["data"]["succMap"]["naha.png"]
    hidden = upload(owner, ("peidus.gif", b"GIF89a")).get_json()["data"]["succMap"]["peidus.gif"]
    note = notes.create(conn, "Jagatud", body=f"# Jagatud\n\n![a]({shown})\n")
    notes.create(conn, "Teine", body=f"# Teine\n\n![b]({hidden})\n")
    g = guest(note["id"], "edit")
    assert g.get(shown).data == PNG  # in the note when the link was made
    assert g.get(hidden).status_code == 404
    assert g.get("/api/images").status_code == 401  # no gallery for guests
    # The guest types another picture's name into the note: saved, and live in the session.
    session = g.post("/api/collab/join", json={"slug": note["slug"], "client": "g"}).get_json()["session"]
    op = [2, {"i": {"name": "paragraph", "text": f"![x]({hidden})"}}]
    assert g.post(f"/api/collab/{session}/sync", json={"client": "g", "seen": 0, "op": op, "op_id": "g1", "base": 0}).status_code == 200
    assert g.post(f"/api/collab/{session}/save", json={"body": f"# Jagatud\n\n![a]({shown})\n\n![x]({hidden})\n", "seq": 1}).status_code == 204
    assert hidden in notes.get(conn, note["id"])["body"]
    assert g.get(hidden).status_code == 404
    # The owner is not restricted.
    assert owner.get(hidden).status_code == 200 and owner.get(shown).status_code == 200


def test_the_owners_later_uploads_into_the_note_reach_its_guests(owner, guest, conn):
    note = notes.create(conn, "Jagatud", body="# Jagatud\n")
    other = notes.create(conn, "Teine", body="# Teine\n")
    g = guest(note["id"], "view")

    def owner_upload(name, data, slug):
        r = owner.post("/pildid", data={"file[]": [(io.BytesIO(data), name)], "slug": slug}, content_type="multipart/form-data")
        return r.get_json()["data"]["succMap"][name]
    later = owner_upload("hiljem.png", PNG, note["slug"])
    elsewhere = owner_upload("mujal.gif", b"GIF89a-mujal", other["slug"])
    unplaced = upload(owner, ("galerii.gif", b"GIF89a-galerii")).get_json()["data"]["succMap"]["galerii.gif"]
    notes.write_body(conn, note["id"], f"# Jagatud\n\n![a]({later})\n\n![b]({elsewhere})\n\n![c]({unplaced})\n")
    assert g.get(later).data == PNG
    assert g.get(elsewhere).status_code == 404
    assert g.get(unplaced).status_code == 404


def test_a_gallery_pick_reaches_the_notes_guests(owner, guest, conn):
    pic = upload(owner, ("galerii.png", PNG)).get_json()["data"]["succMap"]["galerii.png"]
    name = pic.split("/")[-1]
    note = notes.create(conn, "Jagatud", body="# Jagatud\n")
    g = guest(note["id"], "edit")
    notes.write_body(conn, note["id"], f"# Jagatud\n\n![g]({pic})\n")
    assert g.get(pic).status_code == 404  # in the text is not enough
    assert owner.post("/api/images", json={"slug": note["slug"], "name": name}).status_code == 204
    assert g.get(pic).data == PNG
    assert owner.post("/api/images", json={"slug": note["slug"], "name": "../test.db"}).status_code == 400
    assert owner.post("/api/images", json={"slug": "puudub", "name": name}).status_code == 404
    assert g.post("/api/images", json={"slug": note["slug"], "name": name}).status_code == 401


def test_a_later_link_does_not_adopt_what_a_guest_typed(owner, guest, conn):
    shown = upload(owner, ("naha.png", PNG)).get_json()["data"]["succMap"]["naha.png"]
    hidden = upload(owner, ("peidus.gif", b"GIF89a")).get_json()["data"]["succMap"]["peidus.gif"]
    note = notes.create(conn, "Jagatud", body=f"# Jagatud\n\n![a]({shown})\n")
    first = guest(note["id"], "edit")
    notes.write_body(conn, note["id"], f"# Jagatud\n\n![a]({shown})\n\n![x]({hidden})\n")  # typed by that guest
    second = guest(note["id"], "view")
    assert first.get(shown).data == PNG and second.get(shown).data == PNG
    assert first.get(hidden).status_code == 404 and second.get(hidden).status_code == 404


def test_links_made_before_note_images_keep_their_pictures(app, owner, client, conn):
    from app import create_app
    pic = upload(owner, ("vana.png", PNG)).get_json()["data"]["succMap"]["vana.png"]
    later = upload(owner, ("hiljem.gif", b"GIF89a-hiljem")).get_json()["data"]["succMap"]["hiljem.gif"]
    note = notes.create(conn, "Vana", body=f"# Vana\n\n![a]({pic})\n")
    gone = notes.create(conn, "Tühistatud", body=f"# Tühistatud\n\n![a]({pic})\n")
    # As the deployed database has them: links, no note_images rows, never seeded.
    old = conn.execute("INSERT INTO shares (token_hash, note_id, mode) VALUES ('a', ?, 'edit')", (note["id"],)).lastrowid
    conn.execute("INSERT INTO shares (token_hash, note_id, mode, revoked_at) VALUES ('b', ?, 'edit', '2026-01-01T00:00:00Z')", (gone["id"],))
    conn.execute("DELETE FROM settings WHERE key = 'note_images_seeded'")
    g = type(client)(app.test_client())
    with g.c.session_transaction() as sess:
        sess["share_id"] = old
    assert g.get(pic).status_code == 404
    create_app({"TESTING": True, "DATABASE_PATH": app.config["DATABASE_PATH"], "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    assert g.get(pic).data == PNG
    # A note whose links were all revoked is seeded too: its next link will not be its first.
    assert [r["name"] for r in conn.execute("SELECT name FROM note_images WHERE note_id = ?", (gone["id"],))] == [pic.split("/")[-1]]
    # Once only: what the note shows after that start is not adopted on the next.
    notes.write_body(conn, note["id"], f"# Vana\n\n![a]({pic})\n\n![b]({later})\n")
    create_app({"TESTING": True, "DATABASE_PATH": app.config["DATABASE_PATH"], "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    assert g.get(later).status_code == 404


def test_guest_upload_needs_an_edit_share(guest, note, conn):
    viewer = guest(note["id"], "view")
    assert upload(viewer, ("a.png", PNG)).status_code == 401
    editor = guest(note["id"], "edit")
    url = upload(editor, ("a.png", PNG)).get_json()["data"]["succMap"]["a.png"]
    assert editor.get(url).data == PNG  # their own upload, before the note is saved with it
    assert viewer.get(url).data == PNG  # uploaded into the note: its other guests see it too
    elsewhere = guest(notes.create(conn, "Teine")["id"], "edit")
    assert elsewhere.get(url).status_code == 404


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


def test_rejected_files_do_not_count_against_the_share_quota(guest, note, conn):
    g = guest(note["id"], "edit")
    share_id = conn.execute("SELECT id FROM shares WHERE note_id = ? AND mode = 'edit'", (note["id"],)).fetchone()["id"]
    used = lambda: (conn.execute("SELECT value FROM settings WHERE key = ?", (f"upload_bytes:share:{share_id}",)).fetchone() or {"value": "0"})["value"]
    for name, data in (("a.exe", b"x" * 1000), ("b.svg", b"<notsvg")):
        assert upload(g, (name, data)).get_json()["data"]["errFiles"] == [name]
    assert used() == "0"
    upload(g, ("c.png", PNG))
    assert used() == str(len(PNG))
