"""Seaded → Ühendused: cards, saving each card, the Google paste flow, ICS feeds, Testi, Katkesta,
and that no secret ever reaches the browser."""

import io
import json
import sys

import pytest

from app import connections, vault

HTML = {"Accept": "text/html"}
KEYS = ["claude", "ekool", "google", "brain", "ntfy", "ics", "phone"]


def cards(client):
    return {c["key"]: c for c in client.get("/api/connections").get_json()}


def test_every_card_in_order_before_the_owners_exist(owner, missing_owners):
    listed = owner.get("/api/connections").get_json()
    assert [c["key"] for c in listed] == KEYS
    by_key = {c["key"]: c for c in listed}
    assert by_key["google"]["title"] == "Google'i kontod" and by_key["phone"]["title"] == "Telefoni kalender"
    # Cards that need an owner's helpers wait for it; secret-only cards work already.
    assert {k for k, c in by_key.items() if not c["available"]} == {"ekool", "google", "ics", "phone"}
    assert {k for k, c in by_key.items() if c["testable"]} == {"claude"}
    for method, path, body in [("post", "/api/connections/ekool", {"username": "a", "password": "b"}),
                               ("post", "/api/connections/ics", {"url": "https://x.example/a.ics"}),
                               ("post", "/api/connections/google/start", None),
                               ("post", "/api/connections/google/finish", {"pasted_url": "http://127.0.0.1/?code=1"}),
                               ("post", "/api/connections/ekool/test", None),
                               ("delete", "/api/connections/ics/1", None)]:
        r = getattr(owner, method)(path, json=body)
        assert r.status_code == 503 and r.get_json() == {"error": "Pole veel saadaval."}, path


def test_registry_title_and_order_win(owner, owners, monkeypatch):
    monkeypatch.setitem(connections._registry, "ntfy", connections.Connection("ntfy", "Teavitused", 5, owners.run("ntfy")))
    listed = owner.get("/api/connections").get_json()
    assert listed[0]["key"] == "ntfy" and listed[0]["title"] == "Teavitused"


def test_claude_token(owner, conn, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-from-env-0042")
    c = cards(owner)["claude"]
    assert c["configured"] and c["data"]["from_env"] and c["masked"]["claude_token"] == "••••0042"

    assert owner.post("/api/connections/claude", json={"token": ""}).get_json() == {"error": "Kleebi token."}
    r = owner.post("/api/connections/claude", json={"token": "sk-ant oat01"})
    assert r.status_code == 400 and "token" in r.get_json()["error"]

    r = owner.post("/api/connections/claude", json={"token": "  sk-ant-oat01-uus-token-abcd  "})
    assert r.status_code == 200
    assert r.get_json()["masked"]["claude_token"] == "••••abcd" and not r.get_json()["data"]["from_env"]
    assert vault.get(conn, "claude_token") == "sk-ant-oat01-uus-token-abcd"


def test_ekool_credentials_go_to_the_owner(owner, owners):
    r = owner.post("/api/connections/ekool", json={"username": " mari ", "password": " parool 1 "})
    assert r.status_code == 200
    assert owners.calls == [("save_credentials", "mari", " parool 1 ")]  # passwords are not trimmed
    assert r.get_json()["configured"] and r.get_json()["masked"]["ekool_password"] == "••••"

    assert owner.post("/api/connections/ekool", json={"username": "mari"}).get_json() == {"error": "Sisesta eKooli kasutajanimi ja parool."}
    r = owner.post("/api/connections/ekool", json={"username": "mari", "password": "vale"})
    assert r.status_code == 400 and r.get_json() == {"error": "eKool ei tundnud kasutajat ära."}


def test_brain_token_and_remote(owner, conn):
    r = owner.post("/api/connections/brain", json={"token": "github_pat_abcdefgh1234", "remote": ""})
    assert r.status_code == 200 and r.get_json()["masked"]["brain_token"] == "••••1234"
    assert vault.get(conn, "brain_token") == "github_pat_abcdefgh1234"
    assert conn.execute("SELECT value FROM settings WHERE key = 'brain_remote'").fetchone()[0] == "https://github.com/Tigetiiger/brain.git"

    # An empty token field keeps the stored token.
    r = owner.post("/api/connections/brain", json={"token": "", "remote": "https://github.com/Teine/brain.git"})
    assert r.status_code == 200 and r.get_json()["data"]["remote"] == "https://github.com/Teine/brain.git"
    assert vault.get(conn, "brain_token") == "github_pat_abcdefgh1234"

    r = owner.post("/api/connections/brain", json={"token": "", "remote": "https://github.com/Teine/brain"})
    assert r.status_code == 200 and r.get_json()["data"]["remote"] == "https://github.com/Teine/brain"

    # The token goes to this host, so only GitHub (docs/SECURITY-REVIEW.md N3).
    for remote in ("https://x:github_pat_abc@github.com/Tigetiiger/brain.git", "git@github.com:Tigetiiger/brain.git",
                   "http://github.com/Tigetiiger/brain.git", "https://github.com:x/a.git",
                   "https://github.com/Tigetiiger/brain.git?access_token=abc", "https://github.com/T/brain.git#main",
                   "https://gitlab.com/Tigetiiger/brain.git", "https://github.com.evil.example/Tigetiiger/brain.git",
                   "https://github.com:443/Tigetiiger/brain.git", "https://github.com/Tigetiiger",
                   "https://github.com/Tigetiiger/brain/tree/main", "https://github.com/Tigetiiger/brain.git/",
                   "file:///srv/brain.git", "/srv/brain.git", "https://github.com/Tigetiiger/brain.git?", "https://github.com/T/b#"):
        r = owner.post("/api/connections/brain", json={"token": "", "remote": remote})
        assert r.status_code == 400 and r.get_json()["error"].startswith("Repo aadress peab olema"), remote
    assert conn.execute("SELECT value FROM settings WHERE key = 'brain_remote'").fetchone()[0] == "https://github.com/Teine/brain"


def test_brain_needs_a_token_the_first_time(owner):
    assert owner.post("/api/connections/brain", json={"remote": ""}).get_json() == {"error": "Kleebi GitHubi token."}


def test_ntfy(owner, conn):
    r = owner.post("/api/connections/ntfy", json={"url": "", "topic": "markmik-teated-1234", "token": ""})
    assert r.status_code == 200
    assert vault.get_json(conn, "ntfy") == {"url": "http://100.122.97.7:8090", "topic": "markmik-teated-1234", "token": None}
    data = r.get_json()["data"]
    assert data == {"url": "http://100.122.97.7:8090", "topic": "••••1234", "token": None}

    # Empty topic and token keep what is stored; a new token replaces none.
    r = owner.post("/api/connections/ntfy", json={"url": "https://ntfy.example/", "topic": "", "token": "tk_uustoken5678"})
    assert r.status_code == 200
    assert vault.get_json(conn, "ntfy") == {"url": "https://ntfy.example", "topic": "markmik-teated-1234", "token": "tk_uustoken5678"}

    for body, message in [({"topic": "tühikuga teema"}, "Teema nimes võivad olla"),
                          ({"url": "ftp://x", "topic": "a"}, "Serveri aadress peab algama"),
                          ({"url": "https://user:pw@ntfy.example", "topic": "a"}, "Serveri aadress peab algama"),
                          ({"url": "https://ntfy.example/?auth=tk_salajane", "topic": "a"}, "Serveri aadress peab algama"),
                          ({"url": "https://ntfy.example/#x", "topic": "a"}, "Serveri aadress peab algama"),
                          ({"url": "https://ntfy.example/?", "topic": "a"}, "Serveri aadress peab algama")]:
        r = owner.post("/api/connections/ntfy", json=body)
        assert r.status_code == 400 and r.get_json()["error"].startswith(message), body


def test_ntfy_needs_a_topic(owner):
    assert owner.post("/api/connections/ntfy", json={"url": ""}).get_json() == {"error": "Teema on puudu."}


def test_save_validates_the_request(owner):
    assert owner.post("/api/connections/ntfy", data="x", content_type="text/plain").status_code == 400
    assert owner.post("/api/connections/ntfy", json=["x"]).get_json() == {"error": "Vigane päring."}
    assert owner.post("/api/connections/tundmatu", json={}).status_code == 404
    assert owner.post("/api/connections/claude", json={"token": "x" * 501}).get_json() == {"error": "Sisestatud väärtus on liiga pikk."}


def test_google_paste_flow(owner, owners, conn):
    assert owner.post("/api/connections/google/start").get_json() == {"error": "Laadi kõigepealt OAuth-klient üles."}

    r = owner.post("/api/connections/google/client", data={"file": (io.BytesIO(b'{"web": {}}'), "client.json")},
                   content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json() == {"error": "See pole Google'i Desktop app kliendi JSON."}
    r = owner.post("/api/connections/google/client", data={"file": (io.BytesIO(owners.client_json.encode()), "client.json")},
                   content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["data"]["has_client"] and r.get_json()["masked"]["google_client"] == "••••"

    r = owner.post("/api/connections/google/start")
    assert r.get_json() == {"url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=123&state=s1"}

    assert owner.post("/api/connections/google/finish", json={"pasted_url": " "}).status_code == 400
    r = owner.post("/api/connections/google/finish", json={"pasted_url": "http://127.0.0.1:53682/?state=s1"})
    assert r.status_code == 400 and r.get_json() == {"error": "Aadressist ei leitud Google'i koodi."}
    pasted = "http://127.0.0.1:53682/?state=s1&code=4/0Aabc&scope=https://www.googleapis.com/auth/gmail.readonly"
    r = owner.post("/api/connections/google/finish", json={"pasted_url": f"  {pasted}\n"})
    assert r.status_code == 200 and r.get_json() == {"email": owners.email}
    assert ("finish_auth", pasted) in owners.calls

    google = cards(owner)["google"]
    assert google["configured"]
    assert google["data"]["accounts"] == [{"email": owners.email, "ok": True, "last_error": None, "last_ok_at": "2026-09-24T08:00:00Z"}]

    assert owner.delete(f"/api/connections/google/{owners.email}").status_code == 204
    assert ("remove_account", owners.email) in owners.calls
    assert cards(owner)["google"]["data"]["accounts"] == []


def test_google_client_as_pasted_json_and_limits(owner, owners, conn):
    r = owner.post("/api/connections/google/client", json={"client_json": owners.client_json})
    assert r.status_code == 200 and vault.get(conn, "google_client") == owners.client_json
    assert owner.post("/api/connections/google/client", json={"client_json": ""}).get_json() == {"error": "Vali JSON-fail või kleebi selle sisu."}
    big = io.BytesIO(b"{" + b" " * (64 * 1024) + b"}")
    r = owner.post("/api/connections/google/client", data={"file": (big, "x.json")}, content_type="multipart/form-data")
    assert r.get_json() == {"error": "Fail on liiga suur; see pole Google'i kliendi JSON."}
    r = owner.post("/api/connections/google/client", data={"file": (io.BytesIO(b"\xff\xfe{"), "x.json")}, content_type="multipart/form-data")
    assert r.get_json() == {"error": "Fail pole loetav tekst; vali allalaaditud JSON-fail."}


def test_gmail_sender_filter(owner, owners, conn):
    r = owner.post("/api/connections/google", json={"senders": " GAG.ee \n\nopetaja@kool.example\n"})
    assert r.status_code == 200 and r.get_json()["data"]["senders"] == ["gag.ee", "opetaja@kool.example"]
    assert conn.execute("SELECT value FROM settings WHERE key = 'gmail_senders'").fetchone()[0] == '["gag.ee", "opetaja@kool.example"]'
    for bad in ("gag", "nimi @gag.ee", "x" * 190 + ".example.com"):
        r = owner.post("/api/connections/google", json={"senders": bad})
        assert r.status_code == 400 and r.get_json()["error"].startswith("Kirjuta iga saatja eraldi reale"), bad
    # Empty: the owner's default list applies again.
    assert owner.post("/api/connections/google", json={"senders": ""}).get_json()["data"]["senders"] is None
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'gmail_senders'").fetchone() is None


def test_ics_feeds(owner, owners):
    r = owner.post("/api/connections/ics", json={"url": "webcal://kool.example/kalender/private-abc123.ics", "name": " Kool "})
    assert r.status_code == 200
    assert owners.calls == [("add_feed", "https://kool.example/kalender/private-abc123.ics", "Kool")]
    # Feeds may carry their key in the query string.
    owner.post("/api/connections/ics", json={"url": "https://kool.example/feed.ics?key=abc#x", "name": "Päring"})
    assert owners.calls[-1] == ("add_feed", "https://kool.example/feed.ics?key=abc#x", "Päring")
    owners.feeds.pop()
    owner.post("/api/connections/ics", json={"url": "https://moodle.example/cal.ics"})
    assert owners.calls[-1] == ("add_feed", "https://moodle.example/cal.ics", "moodle.example")

    feeds = cards(owner)["ics"]["data"]["feeds"]
    # Only the host: a private calendar address is a credential of its own.
    assert feeds[0] == {"id": 1, "name": "Kool", "host": "kool.example", "last_error": None, "last_ok_at": None}
    assert "private-abc123" not in owner.get("/api/connections").get_data(as_text=True)

    assert owner.delete("/api/connections/ics/1").status_code == 204
    assert owners.calls[-1] == ("remove_feed", 1)
    for url in ("", "kool.example/a.ics", "http://kool.example/a.ics", "file:///etc/passwd", "javascript:alert(1)"):
        r = owner.post("/api/connections/ics", json={"url": url})
        assert r.status_code == 400 and r.get_json() == {"error": "Kalendri aadress peab algama https:// või webcal://."}, url


def test_varskenda_runs_the_cards_sources(owner, owners, conn, run_now):
    conn.execute("INSERT INTO sources (kind, name, last_ok_at, last_error) VALUES "
                 "('ekool', 'eKool', '2026-09-24T05:00:00Z', 'eKool ei vastanud')")
    c = cards(owner)
    assert {k for k, v in c.items() if v["refreshable"]} == {"ekool", "google", "ics"}
    assert c["ekool"]["data"]["sources"] == [{"name": "eKool", "enabled": 1, "last_run_at": None,
                                             "last_ok_at": "2026-09-24T05:00:00Z", "last_error": "eKool ei vastanud"}]

    assert owner.post("/api/connections/ekool/refresh").get_json() == {"ok": True, "items": 3, "error": None}
    assert owner.post("/api/connections/google/refresh").get_json()["ok"] is True
    assert owner.post("/api/connections/ics/refresh").get_json() == {"ok": False, "items": 3, "error": "Kool: voog ei vasta"}
    assert run_now == ["ekool", "gmail", "ics"]
    assert owner.post("/api/connections/ntfy/refresh").status_code == 404
    owner.post("/api/connections/ekool/refresh")  # the fifth in five minutes is the last one allowed
    assert owner.post("/api/connections/ekool/refresh").status_code == 429


def test_varskenda_waits_for_the_sources_package(owner, owners, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.sources.base", None)
    assert not any(c["refreshable"] for c in cards(owner).values())
    assert owner.post("/api/connections/ekool/refresh").get_json() == {"error": "Pole veel saadaval."}


def test_phone_card_shows_the_private_address(owner, owners):
    phone = cards(owner)["phone"]
    assert phone["available"] and phone["data"]["url"] == "http://localhost/ics/tok123.ics"


def test_testi_stores_and_shows_the_result(owner, owners):
    r = owner.post("/api/connections/ntfy/test")
    assert r.status_code == 200 and r.get_json()["ok"] is True and r.get_json()["summary"] == "ntfy korras"
    ntfy = cards(owner)["ntfy"]
    assert (ntfy["ok"], ntfy["summary"], ntfy["error"]) == (True, "ntfy korras", None) and ntfy["at"]

    owners.results["ekool"] = {"ok": False, "summary": "", "error": "Vale parool"}
    assert owner.post("/api/connections/ekool/test").get_json()["error"] == "Vale parool"
    owners.results["google"] = RuntimeError("Google ei vasta")
    assert owner.post("/api/connections/google/test").get_json()["error"] == "Google ei vasta"
    c = cards(owner)
    assert (c["ekool"]["ok"], c["ekool"]["error"]) == (False, "Vale parool")
    assert (c["google"]["ok"], c["google"]["error"]) == (False, "Google ei vasta")


def test_saving_forgets_the_old_result(owner, owners):
    owner.post("/api/connections/ntfy/test")
    assert cards(owner)["ntfy"]["ok"] is True
    owner.post("/api/connections/ntfy", json={"topic": "uus-teema"})
    assert cards(owner)["ntfy"]["ok"] is None


def test_testi_is_rate_limited(owner, owners):
    for _ in range(10):
        assert owner.post("/api/connections/ntfy/test").status_code == 200
    assert owner.post("/api/connections/ntfy/test").status_code == 429


def test_katkesta_removes_only_that_cards_secrets(owner, owners, conn, stored_secrets):
    owner.post("/api/connections/ekool/test")
    assert owner.delete("/api/connections/ekool").status_code == 204
    names = set(vault.names(conn))
    assert not names & {"ekool_username", "ekool_password", "ekool_tokens"}
    assert {"claude_token", "google_client", "brain_token", "ntfy", f"google_account:{owners.email}"} <= names
    assert cards(owner)["ekool"]["ok"] is None and not cards(owner)["ekool"]["configured"]

    assert owner.delete("/api/connections/google").status_code == 204
    assert ("remove_account", owners.email) in owners.calls
    assert not any(n.startswith("google") for n in vault.names(conn))  # google_auth_pending too

    for key, left in (("claude", "claude_token"), ("brain", "brain_token"), ("ntfy", "ntfy")):
        assert left in vault.names(conn)
        assert owner.delete(f"/api/connections/{key}").status_code == 204
        assert left not in vault.names(conn)
    assert vault.names(conn) == []
    assert owner.delete("/api/connections/tundmatu").status_code == 404


def test_secrets_never_reach_the_browser(owner, owners, stored_secrets):
    owners.feeds.append({"id": 1, "name": "Kool", "url": f"https://kool.example/{stored_secrets['ics_private']}.ics"})
    for key in KEYS:
        owner.post(f"/api/connections/{key}/test")
    bodies = [owner.get(path, headers=HTML).get_data(as_text=True)
              for path in ("/api/connections", "/seaded/uhendused", "/tere", "/seaded", "/api/onboarding")]
    # Saving with empty secret fields answers with the card too.
    bodies.append(owner.post("/api/connections/ntfy", json={"url": "", "topic": "", "token": ""}).get_data(as_text=True))
    bodies.append(owner.post("/api/connections/brain", json={"remote": ""}).get_data(as_text=True))
    everything = "\n".join(bodies)
    for name, value in stored_secrets.items():
        assert value not in everything, name
        assert json.dumps(value)[1:-1] not in everything, name

    listed = cards(owner)
    assert listed["claude"]["masked"]["claude_token"] == "••••8841"
    assert listed["ekool"]["masked"] == {"ekool_username": "••••5521", "ekool_password": "••••"}
    assert listed["brain"]["masked"]["brain_token"] == "••••2231"
    assert listed["ntfy"]["data"]["topic"] == "••••7719" and listed["ntfy"]["data"]["token"] == "••••6612"
    assert listed["google"]["masked"]["google_client"] == "••••"


def test_pages(owner):
    for path, page in (("/seaded/uhendused", "connections"), ("/tere", "wizard"), ("/seaded", "settings")):
        r = owner.get(path, headers=HTML)
        assert r.status_code == 200 and f'data-page="{page}"' in r.get_data(as_text=True)
        assert "<script>" not in r.get_data(as_text=True)
    assert 'href="/seaded/uhendused"' in owner.get("/seaded").get_data(as_text=True)
