"""/api/failid (upload, by address, refresh, search) and /failid/<name> (download)."""

import hashlib
from pathlib import Path

import httpcore
import pytest

from app import failid, ics, notes
from fail_helpers import CSV, upload, xlsx


def refused(r):
    return r.status_code == 401 or (r.status_code == 302 and "/login" in r.headers["Location"])


def test_owner_uploads_and_downloads_a_file(owner, app, conn):
    r = upload(owner, "../kaust/Mõõtmised 2024.csv", CSV)
    assert r.status_code == 201
    name = r.json["name"]
    assert name == hashlib.sha256(CSV).hexdigest() + ".csv"
    assert (r.json["original_name"], r.json["ext"], r.json["size"], r.json["author"], r.json["url"]) == \
        ("Mõõtmised 2024.csv", ".csv", len(CSV), "omanik", f"/failid/{name}")
    assert (Path(app.config["DATA_DIR"]) / "failid" / name).read_bytes() == CSV
    got = owner.get(f"/failid/{name}")
    assert got.status_code == 200 and got.data == CSV
    assert got.headers["Content-Disposition"] == "attachment; filename*=UTF-8''M%C3%B5%C3%B5tmised%202024.csv"
    assert got.headers["Content-Type"] == "application/octet-stream"
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    assert got.headers["Content-Security-Policy"].startswith("sandbox; default-src 'none'")
    # The same bytes again under another name: one file, the first name stays.
    again = upload(owner, "teine.csv", CSV)
    assert again.json["name"] == name and again.json["original_name"] == "Mõõtmised 2024.csv"
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1


def test_refused_files_are_not_stored(owner, app, conn):
    for filename, data, reason in (("pilt.png", b"\x89PNG", "failitüüpi"), ("andmed.csv", b"\x00\x01", "tekstifail"),
                                   ("tabel.xlsx", xlsx({"a.xml": b"x"}), "töövihik")):
        r = upload(owner, filename, data)
        assert r.status_code == 400 and reason in r.json["error"]
    assert owner.post("/api/failid", data={}, content_type="multipart/form-data").status_code == 400
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert not list((Path(app.config["DATA_DIR"]) / "failid").glob("*"))


def test_upload_size_cap(owner, monkeypatch):
    monkeypatch.setattr(failid, "MAX_BYTES", 10)
    r = upload(owner, "suur.csv", CSV)
    assert r.status_code == 400 and "liiga suur" in r.json["error"]


def test_unknown_names_are_404(owner):
    assert owner.get("/failid/../test.db").status_code == 404
    assert owner.get("/failid/" + "a" * 64 + ".csv").status_code == 404
    assert owner.get("/failid/" + "a" * 64 + ".exe").status_code == 404


def test_upload_into_an_unknown_note_is_404(owner):
    assert upload(owner, "a.csv", CSV, slug="pole-olemas").status_code == 404


def test_anonymous_is_refused_everything(client, owner):
    name = upload(owner, "a.csv", CSV).json["name"]
    assert refused(upload(client, "a.csv", CSV))
    assert refused(client.get(f"/failid/{name}"))
    assert refused(client.get("/api/failid"))
    assert refused(client.post("/api/failid/url", json={"url": "https://example.com/a.csv"}))
    assert refused(client.post(f"/api/failid/{name}/refresh", json={}))


def test_a_guest_downloads_only_the_files_allowed_for_its_note(owner, guest, conn):
    shared = notes.create(conn, "Jagatud", body="# Jagatud\n")
    mine = upload(owner, "andmed.csv", CSV, slug=shared["slug"]).json["name"]
    elsewhere = notes.create(conn, "Teine", body="# Teine\n")
    theirs = upload(owner, "salajane.csv", b"salajane\n", slug=elsewhere["slug"]).json["name"]
    # Linking it in the shared note's text is not enough: an edit guest could type any name there.
    notes.write_body(conn, shared["id"], f"# Jagatud\n\n[andmed.csv](/failid/{mine}) [s.csv](/failid/{theirs})\n")
    for mode in ("view", "edit"):
        g = guest(shared["id"], mode)
        got = g.get(f"/failid/{mine}")
        assert got.status_code == 200 and got.data == CSV
        assert got.headers["Cache-Control"] == "no-store"
        assert g.get(f"/failid/{theirs}").status_code == 404
        # Guests add nothing and search nothing.
        assert upload(g, "uus.csv", b"a\n", slug=shared["slug"]).status_code == 401
        assert g.get("/api/failid").status_code == 401
        assert g.post("/api/failid/url", json={"url": "https://example.com/a.csv"}).status_code == 401
        assert g.post(f"/api/failid/{mine}/refresh", json={}).status_code == 401
    assert guest(elsewhere["id"]).get(f"/failid/{mine}").status_code == 404


def test_search_by_name_newest_first_with_the_notes_linking_each(owner, conn):
    first = upload(owner, "hinnad.csv", b"a\n1\n").json["name"]
    second = upload(owner, "Hinnad_2.json", b"[1]").json["name"]
    upload(owner, "muu.txt", b"tere\n")
    conn.execute("UPDATE files SET created_at = '2026-01-01T00:00:00Z' WHERE name = ?", (first,))
    notes.create(conn, "Kasutab", body=f"[hinnad.csv](/failid/{first})")
    gone = notes.create(conn, "Kustutatud", body=f"[hinnad.csv](/failid/{first})")
    notes.soft_delete(conn, gone["id"])
    found = owner.get("/api/failid?q=hinnad").json
    assert [f["name"] for f in found] == [second, first]
    assert found[1]["notes"] == [{"slug": "kasutab", "title": "Kasutab"}] and found[0]["notes"] == []
    assert found[0]["url"] == f"/failid/{second}" and found[0]["original_name"] == "Hinnad_2.json"
    assert len(owner.get("/api/failid").json) == 3
    assert owner.get("/api/failid?q=%25").json == []  # LIKE's wildcards are plain characters


# --- By web address ---------------------------------------------------------------------------

@pytest.fixture
def web(monkeypatch):
    """ics.download answering from web["pages"]; web["calls"] holds (url, time limit, kwargs)."""
    state = {"pages": {}, "calls": []}

    def download(url, time_limit=60, **kw):
        state["calls"].append((url, time_limit, kw))
        if url not in state["pages"]:
            raise ValueError("Lehte ei saanud alla laadida.")
        return state["pages"][url]
    monkeypatch.setattr(ics, "download", download)
    return state


def test_a_file_by_address_is_fetched_once_and_remembers_the_address(owner, conn, web):
    note = notes.create(conn, "Veeb")
    address = "https://andmed.example/avaandmed/rahvastik%202024.csv?vorm=csv"
    web["pages"][address] = CSV
    r = owner.post("/api/failid/url", json={"url": address, "slug": note["slug"]})
    assert r.status_code == 201
    assert (r.json["original_name"], r.json["source_url"], r.json["author"]) == ("rahvastik 2024.csv", address, "omanik")
    assert r.json["fetched_at"]
    assert web["calls"] == [(address, 30, {"user_agent": failid.USER_AGENT, "max_bytes": 25 * 1024 * 1024})]
    assert failid.allowed(conn, note["id"], r.json["name"])


def test_an_address_without_a_data_file_type_or_a_failed_download_is_refused(owner, conn, web):
    web["pages"]["https://andmed.example/tabel"] = CSV
    web["pages"]["https://andmed.example/pilt.csv"] = b"\x89PNG\x00"
    for address, reason in (("https://andmed.example/tabel", "failitüübiga"), ("https://andmed.example/pilt.csv", "tekstifail"),
                            ("https://andmed.example/puudub.csv", "alla laadida"), ("", "veebiaadress")):
        r = owner.post("/api/failid/url", json={"url": address})
        assert r.status_code == 400 and reason in r.json["error"], address
    assert owner.post("/api/failid/url", json={"aadress": 1}).status_code == 400
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0


@pytest.mark.parametrize("address", [
    "https://127.0.0.1/a.csv", "https://10.0.0.5/a.csv", "https://192.168.1.10/a.csv",
    "https://169.254.169.254/latest/a.csv", "https://100.122.97.7:8090/a.csv", "https://[::1]/a.csv",
])
def test_an_inside_address_is_refused_by_the_real_check(owner, conn, monkeypatch, address):
    connected = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *a, **k: connected.append(a) or pytest.fail("connected"))
    r = owner.post("/api/failid/url", json={"url": address})
    assert r.status_code == 400 and "sisevõrku" in r.json["error"]
    assert "127.0.0.1" not in r.json["error"] and connected == []
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0


def test_the_download_cap_is_the_files_not_the_calendars():
    class Body:
        headers = {}

        def iter_raw(self):
            yield b"x" * (6 * 1024 * 1024)
    assert len(ics._read_body(Body(), float("inf"), max_bytes=failid.MAX_BYTES)) == 6 * 1024 * 1024
    with pytest.raises(ValueError, match="üle 5 MB"):
        ics._read_body(Body(), float("inf"))


def test_refresh_makes_a_new_file_for_new_content(owner, conn, web):
    note = notes.create(conn, "Värske")
    address = "https://andmed.example/hinnad.csv"
    web["pages"][address] = b"hind\n1\n"
    old = owner.post("/api/failid/url", json={"url": address}).json
    web["pages"][address] = b"hind\n2\n"
    new = owner.post(f"/api/failid/{old['name']}/refresh", json={"slug": note["slug"]})
    assert new.status_code == 200
    assert new.json["name"] != old["name"] and new.json["name"] == hashlib.sha256(b"hind\n2\n").hexdigest() + ".csv"
    assert (new.json["original_name"], new.json["source_url"]) == ("hinnad.csv", address)
    assert failid.allowed(conn, note["id"], new.json["name"])
    assert owner.get(old["url"]).data == b"hind\n1\n"  # the old file stays: other notes may link it
    # Unchanged content: the same name, a later fetch time.
    conn.execute("UPDATE files SET fetched_at = '2026-01-01T00:00:00Z'")
    same = owner.post(f"/api/failid/{new.json['name']}/refresh", json={})
    assert same.json["name"] == new.json["name"] and same.json["fetched_at"] > "2026-01-01T00:00:00Z"


def test_refresh_needs_a_fetched_file(owner, web):
    uploaded = upload(owner, "a.csv", CSV).json["name"]
    r = owner.post(f"/api/failid/{uploaded}/refresh", json={})
    assert r.status_code == 400 and "veebiaadressi" in r.json["error"]
    assert owner.post("/api/failid/" + "c" * 64 + ".csv/refresh", json={}).status_code == 404
    assert owner.post("/api/failid/x.csv/refresh", json={}).status_code == 404
    assert web["calls"] == []


def test_fetching_bytes_already_uploaded_records_the_address(owner, web):
    uploaded = upload(owner, "a.csv", CSV).json
    web["pages"]["https://andmed.example/b.csv"] = CSV
    fetched = owner.post("/api/failid/url", json={"url": "https://andmed.example/b.csv"}).json
    assert fetched["name"] == uploaded["name"] and fetched["source_url"] == "https://andmed.example/b.csv"
    assert owner.post(f"/api/failid/{fetched['name']}/refresh", json={}).status_code == 200
