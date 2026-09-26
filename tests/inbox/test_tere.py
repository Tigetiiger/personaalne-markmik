"""First-run wizard state and redirect, and the password change on Seaded."""

from app import auth

HTML = {"Accept": "text/html"}
PASSWORD = "testi-parool-123"  # tests/conftest.py OWNER_PASSWORD


def test_onboarding_state(owner):
    state = owner.get("/api/onboarding").get_json()
    assert [s["key"] for s in state["steps"]] == ["claude", "ekool", "google", "brain", "ntfy", "ics", "phone"]
    assert all(s["status"] is None for s in state["steps"]) and state["remaining"] == 7

    assert owner.post("/api/onboarding/claude", json={"status": "done"}).get_json()["remaining"] == 6
    state = owner.post("/api/onboarding/ekool", json={"status": "skipped"}).get_json()
    assert [(s["key"], s["status"]) for s in state["steps"][:3]] == [("claude", "done"), ("ekool", "skipped"), ("google", None)]
    assert state["steps"][0]["title"] == "Claude"
    # Answering again changes the answer.
    assert owner.post("/api/onboarding/ekool", json={"status": "done"}).get_json()["steps"][1]["status"] == "done"

    r = owner.post("/api/onboarding/ekool", json={"status": "later"})
    assert r.status_code == 400 and "error" in r.get_json()
    assert owner.post("/api/onboarding/tundmatu", json={"status": "done"}).status_code == 404


def test_start_pages_lead_to_the_wizard_on_first_run(owner):
    for path in ("/", "/tana"):
        r = owner.get(path, headers=HTML)
        assert r.status_code == 302 and r.headers["Location"] == "/tere", path
    # Never a loop, never other pages, never the API, never a non-browser request.
    assert owner.get("/tere", headers=HTML).status_code == 200
    assert owner.get("/seaded", headers=HTML).status_code == 200
    assert owner.get("/postkast", headers=HTML).status_code == 200
    r = owner.get("/api/inbox/count", headers=HTML)
    assert r.status_code == 200 and r.get_json() == {"count": 0}
    assert owner.get("/").status_code == 200
    assert owner.get("/static/app.js", headers=HTML).status_code == 200


def test_login_lands_on_the_wizard(client):
    r = client.post("/login", data={"parool": PASSWORD})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert client.get("/", headers=HTML).headers["Location"] == "/tere"


def test_any_answer_ends_the_redirect(owner):
    owner.post("/api/onboarding/phone", json={"status": "skipped"})
    assert owner.get("/", headers=HTML).status_code == 200


def test_anonymous_is_sent_to_login_not_the_wizard(client):
    r = client.get("/", headers=HTML)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/login", headers=HTML).status_code == 200


def test_password_change(owner, conn):
    r = owner.post("/api/settings/password", json={"old": "vale-parool", "new": "uus-parool-456"})
    assert r.status_code == 400 and r.get_json() == {"error": "Vana parool on vale."}
    r = owner.post("/api/settings/password", json={"old": PASSWORD, "new": "lühike"})
    assert r.status_code == 400 and r.get_json() == {"error": "Parool peab olema vähemalt 8 märki."}
    assert owner.post("/api/settings/password", json={"old": PASSWORD}).get_json() == {"error": "Sisesta vana ja uus parool."}
    r = owner.post("/api/settings/password", json={"old": PASSWORD, "new": "x" * 1025})
    assert r.status_code == 400 and r.get_json() == {"error": "Parool on liiga pikk (üle 1024 märgi)."}

    assert owner.post("/api/settings/password", json={"old": PASSWORD, "new": "uus-parool-456"}).status_code == 204
    assert auth.check_login(conn, "uus-parool-456", "t") and not auth.check_login(conn, PASSWORD, "t")
