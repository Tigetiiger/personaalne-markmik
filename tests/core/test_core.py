"""Wave 0 foundation: schema, login, CSRF, default deny, headers, secrets, jobs."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app import auth, connections, jobs, vault
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
    assert client.c.post("/login", data={"parool": "vale"}).status_code == 403
    r = client.c.post("/login", data={"parool": "testi-parool-123"})
    assert r.status_code == 302
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 302


def test_login_next_stays_on_site(client):
    r = client.c.post("/login?next=//evil.example/x", data={"parool": "testi-parool-123"})
    assert r.headers["Location"] == "/"


def test_csrf_required(owner):
    r = owner.c.post("/logout")  # no token
    assert r.status_code == 403


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
