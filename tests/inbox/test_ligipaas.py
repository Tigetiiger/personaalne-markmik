"""Who may reach E2's pages and API: only the owner, only with the CSRF token, and the test-only
routes exist only in browser-test mode."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import vault

HTML = {"Accept": "text/html"}
PAGES = ["/postkast", "/seaded", "/seaded/uhendused", "/tere"]
READS = ["/api/inbox", "/api/inbox/count", "/api/connections", "/api/onboarding"]
WRITES = [
    ("post", "/api/inbox/1/confirm"), ("post", "/api/inbox/1/reject"), ("post", "/api/inbox/confirm-all"),
    ("post", "/api/connections/ntfy"), ("post", "/api/connections/ntfy/test"), ("post", "/api/connections/ekool/refresh"),
    ("post", "/api/connections/google/client"), ("post", "/api/connections/google/start"),
    ("post", "/api/connections/google/finish"), ("post", "/api/onboarding/claude"), ("post", "/api/settings/password"),
    ("delete", "/api/connections/ntfy"), ("delete", "/api/connections/google/a@b.ee"), ("delete", "/api/connections/ics/1"),
]


@pytest.fixture
def guest(app, conn, client):
    """A browser holding a valid edit share for one note."""
    note = conn.execute("INSERT INTO notes (slug, title, body) VALUES ('jagatud', 'Jagatud', '')").lastrowid
    share = conn.execute("INSERT INTO shares (token_hash, note_id, mode) VALUES ('x', ?, 'edit')", (note,)).lastrowid
    with client.c.session_transaction() as s:
        s["share_id"] = share
    return client


def refused(r):
    return r.status_code == 401 or (r.status_code == 302 and "/login" in r.headers["Location"])


@pytest.mark.parametrize("who", ["anonymous", "guest"])
def test_only_the_owner_gets_anything(request, who, conn, proposed):
    visitor = request.getfixturevalue("client" if who == "anonymous" else "guest")
    proposed("Salajane kontrolltöö")
    vault.set(conn, "brain_token", "github_pat_ligipaas_test_1234")
    for path in PAGES + READS:
        r = visitor.get(path, headers=HTML)
        assert refused(r), (who, path, r.status_code)
        assert "Salajane" not in r.get_data(as_text=True) and "1234" not in r.get_data(as_text=True)
    for method, path in WRITES:
        assert getattr(visitor, method)(path, json={}).status_code == 401, (who, path)
    assert conn.execute("SELECT status FROM events").fetchone()["status"] == "pakutud"


def test_every_write_needs_the_csrf_token(owner, conn, proposed):
    eid = proposed()
    raw = owner.c  # the test client without the X-CSRF header
    for method, path in WRITES:
        assert getattr(raw, method)(path.replace("/1/", f"/{eid}/"), json={}).status_code == 403, path
    upload = {"file": (io.BytesIO(b'{"installed": {}}'), "client.json")}
    assert raw.post("/api/connections/google/client", data=upload, content_type="multipart/form-data").status_code == 403
    assert raw.post(f"/api/inbox/{eid}/reject", json={"csrf": "vale"}).status_code == 403
    assert conn.execute("SELECT status FROM events WHERE id = ?", (eid,)).fetchone()["status"] == "pakutud"


def test_test_routes_exist_only_in_browser_test_mode(tmp_path):
    code = ("from app import create_app\n"
            f"app = create_app({{'DATABASE_PATH': {str(tmp_path / 'x.db')!r}, 'DATA_DIR': {str(tmp_path)!r}, 'SECRET_KEY': 'x'}})\n"
            "print(sorted(r.rule for r in app.url_map.iter_rules() if r.rule.startswith('/_test/inbox')))")
    env = {k: v for k, v in os.environ.items() if k != "MARKMIK_TEST_PAGES"}
    root = Path(__file__).resolve().parents[2]

    def rules(extra):
        out = subprocess.run([sys.executable, "-c", code], env={**env, **extra}, cwd=root,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip().splitlines()[-1]
    assert rules({}) == "[]"
    assert rules({"MARKMIK_TEST_PAGES": "1"}) == "['/_test/inbox/fakes', '/_test/inbox/fakes', '/_test/inbox/google', '/_test/inbox/seed']"
