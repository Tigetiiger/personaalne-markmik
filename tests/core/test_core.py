"""Wave 0 foundation: schema, login, CSRF, default deny, headers, secrets, jobs."""

import getpass
import re
import sqlite3
from datetime import datetime, timezone

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import auth, connections, create_app, hashpw, jobs, vault
from app.db import connect, init_db, tx
from app.security import CSP


def test_schema_applies_twice(tmp_path):
    path = tmp_path / "x.db"
    init_db(path)
    init_db(path)
    c = connect(path)
    names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"notes", "events", "collab_sessions", "shares", "secrets", "sources"} <= names


def test_fts_follows_notes(conn):
    conn.execute("INSERT INTO notes (slug, title, body) VALUES ('a', 'Töö', 'kodune töö')")
    assert conn.execute("SELECT rowid FROM notes_fts WHERE notes_fts MATCH 'too'").fetchone()
    conn.execute("UPDATE notes SET body = 'muu' , title = 'x' WHERE slug = 'a'")
    assert conn.execute("SELECT rowid FROM notes_fts WHERE notes_fts MATCH 'too'").fetchone() is None


def test_tx_rolls_back(conn):
    with pytest.raises(RuntimeError):
        with tx(conn):
            conn.execute("INSERT INTO settings (key, value) VALUES ('a', 'b')")
            raise RuntimeError
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'a'").fetchone() is None


def test_anonymous_is_redirected_or_401(client):
    r = client.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/api/anything-owned").status_code == 404  # unknown route stays 404


def test_login_logout(client):
    assert client.post("/login", data={"parool": "vale"}).status_code == 403
    r = client.post("/login", data={"parool": "testi-parool-123"})
    assert r.status_code == 302
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 302


def test_login_next_stays_on_site(client):
    r = client.post("/login?next=//evil.example/x", data={"parool": "testi-parool-123"})
    assert r.headers["Location"] == "/"


def test_csrf_required(owner):
    r = owner.c.post("/logout")  # no token
    assert r.status_code == 403


def form_token(client):
    page = client.get("/login").get_data(as_text=True)
    return re.search(r'<input type="hidden" name="csrf" value="([^"]+)">', page).group(1)


def test_login_through_the_form_with_its_token(client):
    r = client.c.post("/login", data={"parool": "testi-parool-123", "csrf": form_token(client)})
    assert r.status_code == 302
    assert client.get("/api/notes/tree").status_code == 200


def test_login_without_a_valid_token_is_refused(app, client):
    form_token(client)
    for data in ({}, {"csrf": ""}, {"csrf": "vale"}, {"csrf": "ä"}):
        assert client.c.post("/login", data={"parool": "testi-parool-123", **data}).status_code == 403
    assert client.get("/api/notes/tree").status_code == 401
    # A cross-site POST brings no session cookie, so there is no token its empty one could match.
    assert app.test_client().post("/login", data={"parool": "testi-parool-123", "csrf": ""}).status_code == 403


def test_a_login_page_whose_cookie_is_gone_shows_the_form_again(app):
    stale = app.test_client()  # the tab still shows the form; the cookie is gone
    r = stale.post("/login", data={"parool": "testi-parool-123", "csrf": "vana-token"})
    assert r.status_code == 403 and "Leht aegus, proovi uuesti." in r.get_data(as_text=True)
    assert stale.get("/api/notes/tree").status_code == 401
    token = re.search(r'name="csrf" value="([^"]+)"', r.get_data(as_text=True)).group(1)
    assert stale.post("/login", data={"parool": "testi-parool-123", "csrf": token}).status_code == 302


def test_login_keeps_its_rate_limit_and_failure_count(client):
    token = form_token(client)
    r = client.c.post("/login", data={"parool": "vale", "csrf": token})
    assert r.status_code == 403 and "Vale parool." in r.get_data(as_text=True)
    # 20 requests a minute, the form's GET included.
    statuses = [client.c.post("/login", data={"parool": "vale", "csrf": token}).status_code for _ in range(19)]
    assert statuses == [403] * 18 + [429]
    assert len(auth._failures["127.0.0.1"]) == 19


def start(tmp_path, **config):
    return create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "h.db"), "DATA_DIR": str(tmp_path),
                       "SECRET_KEY": "t", "OWNER_PASSWORD": None, "OWNER_PASSWORD_HASH": None, **config})


def test_owner_password_hash_wins_over_the_plain_password(tmp_path):
    hashed = generate_password_hash("rasi-parool-1")
    app = start(tmp_path, OWNER_PASSWORD_HASH=hashed, OWNER_PASSWORD="plain-parool-2")
    conn = connect(app.config["DATABASE_PATH"])
    assert conn.execute("SELECT password_hash FROM owner").fetchone()[0] == hashed
    assert auth.check_login(conn, "rasi-parool-1", "a") and not auth.check_login(conn, "plain-parool-2", "a")


def test_later_starts_ignore_the_hash_and_the_password(tmp_path):
    first = generate_password_hash("esimene-parool")
    start(tmp_path, OWNER_PASSWORD_HASH=first)
    app = start(tmp_path, OWNER_PASSWORD_HASH=generate_password_hash("teine-parool"), OWNER_PASSWORD="kolmas-parool")
    conn = connect(app.config["DATABASE_PATH"])
    assert conn.execute("SELECT password_hash FROM owner").fetchone()[0] == first


GOOD = generate_password_hash("rasi-parool-1")


@pytest.mark.parametrize("bad", [
    "rasi-parool-1",                        # the password itself
    GOOD.split("$")[0],                     # an unquoted .env line in docker compose: "$..." expanded away
    GOOD[:-1],                              # truncated copy
    GOOD.upper(),
    f"'{GOOD}'",                            # quotes kept by a tool that does not strip them
    "md5$sool$" + "0" * 32,
    "scrypt:x:y:z$sool$" + "0" * 128,
])
def test_a_malformed_hash_stops_the_start(tmp_path, bad):
    with pytest.raises(ValueError, match="OWNER_PASSWORD_HASH is not a werkzeug password hash"):
        start(tmp_path, OWNER_PASSWORD_HASH=bad, OWNER_PASSWORD="plain-parool-2")
    assert connect(str(tmp_path / "h.db")).execute("SELECT 1 FROM owner").fetchone() is None
    start(tmp_path, OWNER_PASSWORD="plain-parool-2")
    with pytest.raises(ValueError):  # with an owner too: the mistake shows before it matters
        start(tmp_path, OWNER_PASSWORD_HASH=bad)


def test_other_werkzeug_methods_are_hashes_too():
    assert auth.is_password_hash(GOOD)
    assert auth.is_password_hash(generate_password_hash("x", method="pbkdf2:sha256:1000"))


def fake_getpass(monkeypatch, *answers):
    answers = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))


def test_hashpw_prints_only_the_hash(monkeypatch, capsys):
    fake_getpass(monkeypatch, "rasi-parool-1", "rasi-parool-1")
    hashpw.main()
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and auth.is_password_hash(out.strip())
    assert check_password_hash(out.strip(), "rasi-parool-1")


@pytest.mark.parametrize("answers", [("rasi-parool-1", "rasi-parool-2"), ("", "")])
def test_hashpw_refuses_a_mismatch_or_an_empty_password(monkeypatch, capsys, answers):
    fake_getpass(monkeypatch, *answers)
    with pytest.raises(SystemExit) as exit:
        hashpw.main()
    assert exit.value.code != 0 and capsys.readouterr().out == ""


def test_login_throttle(conn):
    for _ in range(auth.MAX_FAILURES):
        assert not auth.check_login(conn, "vale", "1.2.3.4", now=100.0)
    # The right password is refused too while the address is locked.
    assert not auth.check_login(conn, "testi-parool-123", "1.2.3.4", now=101.0)
    assert auth.check_login(conn, "testi-parool-123", "1.2.3.4", now=101.0 + auth.LOCK_S)


def test_security_headers(owner):
    r = owner.get("/")
    assert r.headers["Content-Security-Policy"] == CSP
    assert "unsafe-inline" not in CSP.split("script-src")[1].split(";")[0]
    assert r.headers["Cache-Control"] == "no-store"
    assert "<script>" not in r.get_data(as_text=True)  # no inline scripts anywhere in the shell


def test_secrets_round_trip_and_mask(conn):
    vault.set(conn, "brain_token", "github_pat_abcdefghijklmnop1234")
    assert vault.get(conn, "brain_token") == "github_pat_abcdefghijklmnop1234"
    assert vault.masked(conn, "brain_token") == "••••1234"
    raw = conn.execute("SELECT ciphertext FROM secrets").fetchone()[0]
    assert b"github_pat" not in raw
    vault.delete(conn, "brain_token")
    assert vault.get(conn, "brain_token") is None


def test_secrets_env_fallback(conn, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-env")
    assert vault.get(conn, "claude_token") == "from-env"


def test_connections_registry(conn):
    assert connections.get("claude") is not None
    result = connections.run_test(conn, "claude")
    assert result["ok"] and connections.last_result(conn, "claude")["ok"]


def test_jobs_next_run_every_and_at():
    j = jobs.Job("x", None, 300, (), 0)
    now = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    assert jobs.next_run(j, None, now) == now
    assert (jobs.next_run(j, now, now) - now).total_seconds() == 300
    daily = jobs.Job("y", None, None, ("07:00", "13:00"), 0)
    # 10:00 UTC = 13:00 Tallinn (summer time): the next slot after 13:00 is tomorrow 07:00.
    nxt = jobs.next_run(daily, now, now)
    assert nxt.astimezone(timezone.utc) == datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)


def test_jobs_at_across_dst():
    daily = jobs.Job("y", None, None, ("07:00",), 0)
    last = datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)  # 07:00 EEST
    nxt = jobs.next_run(daily, last, last)
    assert nxt.astimezone(timezone.utc) == datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)  # 07:00 EET


def test_jobs_at_spring_forward_keeps_slot_order():
    # 2027-03-28: 03:00 EET jumps to 04:00 EEST, so the missing 03:30 lands at 04:30 EEST.
    night = jobs.Job("y", None, None, ("03:30", "04:00"), 0)
    last = datetime(2027, 3, 27, 22, 0, tzinfo=timezone.utc)  # 00:00 EET
    first = jobs.next_run(night, last, last)
    assert first == datetime(2027, 3, 28, 1, 0, tzinfo=timezone.utc)  # 04:00 EEST
    assert jobs.next_run(night, first, first) == datetime(2027, 3, 28, 1, 30, tzinfo=timezone.utc)


def test_worker_runs_due_jobs(conn, monkeypatch):
    from app import worker
    ran = []
    monkeypatch.setattr(jobs, "REGISTRY", {"t": jobs.Job("t", lambda c: ran.append(1), 60, (), 0)})
    now = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    worker.run_due(conn, now)
    worker.run_due(conn, now)  # not due again yet
    assert ran == [1]


def test_deeply_nested_json_is_rejected_not_500(owner):
    body = "[" * 50000 + "]" * 50000
    r = owner.post("/logout", data=body, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 403)
