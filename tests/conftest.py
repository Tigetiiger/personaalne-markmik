"""Fixtures shared by every package's tests. Owner: lead. A package adds its own fixtures in
tests/<package>/conftest.py, never here."""

import os
import re
import subprocess

import pytest
from cryptography.fernet import Fernet

from app import auth, create_app, security
from app.ai import runner
from app.db import connect

os.environ.setdefault("MARKMIK_KEY", Fernet.generate_key().decode())
OWNER_PASSWORD = "testi-parool-123"


@pytest.fixture(autouse=True)
def _ai_fake(monkeypatch):
    """No test ever calls the real model unless it is marked live."""
    monkeypatch.setenv("AI_FAKE", "1")
    monkeypatch.setattr(runner, "fake_structured", runner._default_fake_structured)


@pytest.fixture(autouse=True)
def _fresh_limits():
    """Rate limits and login throttling live in module memory; every test starts clean."""
    security._hits.clear()
    auth._failures.clear()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "DATA_DIR": str(tmp_path),
        "OWNER_PASSWORD": OWNER_PASSWORD,
        "OWNER_PASSWORD_HASH": None,  # an exported .env must not replace the test password
        "SECRET_KEY": "test",
    })


@pytest.fixture
def conn(app):
    c = connect(app.config["DATABASE_PATH"])
    yield c
    c.close()


class CsrfClient:
    """Test client that sends the session's CSRF token with every state-changing request."""

    def __init__(self, client):
        self.c = client

    def token(self):
        with self.c.session_transaction() as s:
            if "csrf" not in s:
                s["csrf"] = "t" * 43
            return s["csrf"]

    def get(self, *a, **kw):
        return self.c.get(*a, **kw)

    def _send(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        headers.setdefault("X-CSRF", self.token())
        return getattr(self.c, method)(url, headers=headers, **kw)

    def post(self, url, **kw):
        return self._send("post", url, **kw)

    def patch(self, url, **kw):
        return self._send("patch", url, **kw)

    def put(self, url, **kw):
        return self._send("put", url, **kw)

    def delete(self, url, **kw):
        return self._send("delete", url, **kw)


@pytest.fixture
def client(app):
    """Anonymous visitor."""
    return CsrfClient(app.test_client())


@pytest.fixture
def owner(app):
    """Logged-in owner."""
    c = CsrfClient(app.test_client())
    r = c.post("/login", data={"parool": OWNER_PASSWORD})
    assert r.status_code == 302, r.data
    return c


@pytest.fixture
def brain_remote(tmp_path):
    """A bare git repo standing in for Tigetiiger/brain, with one page committed."""
    remote = tmp_path / "brain.git"
    work = tmp_path / "brain-seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "pages").mkdir()
    (work / "pages" / "naide.md").write_text("---\ntitle: Näide\ntype: concept\n---\n\n# Näide\n")
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "seed"], check=True)
    subprocess.run(git + ["push", "-q", str(remote), "main"], check=True)
    return remote


def csrf_from(html):
    m = re.search(r'name="csrf" content="([^"]+)"', html)
    return m.group(1) if m else None
