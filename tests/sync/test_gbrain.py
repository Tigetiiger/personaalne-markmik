"""app/gbrain.py against a fake gbrain /mcp (stateless JSON-RPC, SSE replies; docs/spikes/gbrain.md)."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import gbrain, notes, vault

TOKEN = "gbrain_testtoken_0123456789abcdef"
HITS = [
    {"slug": "pages/naide", "title": "Näide", "score": 0.2, "chunk_text": "# Näide\n\nesimene   tükk"},
    {"slug": "pages/naide", "title": "Näide", "score": 0.5, "chunk_text": "teine tükk"},
    {"slug": "notes/kool/keemia", "title": "Keemia", "score": 0.4, "chunk_text": "# Keemia\n\n**Kontrolltöö** [reedel](/m/r)"},
    {"slug": "notes/matemaatika", "title": "Matemaatika", "score": 0.9, "chunk_text": "see ise"},
]


class FakeGbrain(BaseHTTPRequestHandler):
    mode = "ok"
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"body": body, "auth": self.headers.get("Authorization"),
                                    "accept": self.headers.get("Accept")})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}" or self.mode == "401":
            self.send_response(401)
            self.end_headers()
            return
        if self.mode == "slow":
            time.sleep(0.5)
        if self.mode == "rpc-error":
            msg = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "Unknown: x"}}
        else:
            text = json.dumps({"error": "boom"} if self.mode in ("tool-error", "odd-shape") else HITS)
            msg = {"jsonrpc": "2.0", "id": body["id"],
                   "result": {"content": [{"type": "text", "text": text}], "isError": self.mode == "tool-error"}}
        data = f"event: message\ndata: {json.dumps(msg)}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake(app, monkeypatch, tmp_path):
    """A fake gbrain, and its token where the container writes it (<DATA_DIR>/gbrain/app_token)."""
    FakeGbrain.mode, FakeGbrain.requests = "ok", []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGbrain)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    token_file = tmp_path / "gbrain" / "app_token"
    token_file.parent.mkdir()
    token_file.write_text(TOKEN + "\n")
    monkeypatch.setenv("GBRAIN_URL", f"http://127.0.0.1:{server.server_port}/mcp")
    yield FakeGbrain
    server.shutdown()


def test_search_one_row_per_page(fake):
    hits = gbrain.search("tükk", limit=2)
    assert hits == [
        {"slug": "notes/matemaatika", "title": "Matemaatika", "score": 0.9, "snippet": "see ise"},
        {"slug": "pages/naide", "title": "Näide", "score": 0.5, "snippet": "teine tükk"},
    ]
    req = fake.requests[0]
    assert req["body"]["method"] == "tools/call" and req["body"]["params"] == {
        "name": "search", "arguments": {"query": "tükk", "limit": 6}}
    assert req["accept"] == "application/json, text/event-stream"


def test_related_leaves_out_the_note_itself(fake, conn):
    m = notes.create(conn, "Matemaatika", body="# Matemaatika\n\n**Kontrolltöö** reedel, õpik lk 42.\n")
    hits = gbrain.related(conn, m)
    assert [(h["slug"], h["snippet"]) for h in hits] == [
        ("pages/naide", "teine tükk"), ("notes/kool/keemia", "Keemia Kontrolltöö reedel")]
    assert fake.requests[0]["body"]["params"]["arguments"]["query"] == "matemaatika or kontrolltöö or reedel or õpik"


def test_vault_token_and_url_setting_win(fake, conn, monkeypatch, tmp_path):
    conn.execute("INSERT INTO settings (key, value) VALUES ('gbrain_url', ?)", (os.environ["GBRAIN_URL"],))
    monkeypatch.setenv("GBRAIN_URL", "http://127.0.0.1:9/mcp")  # nothing listens there
    (tmp_path / "gbrain" / "app_token").unlink()
    assert gbrain.search("x", conn=conn) == []  # no token anywhere
    vault.set(conn, "gbrain_token", TOKEN)
    assert len(gbrain.search("x", conn=conn)) == 3


@pytest.mark.parametrize("mode", ["401", "rpc-error", "tool-error", "odd-shape", "slow", "down"])
def test_failures_give_an_empty_list_and_a_log_line(fake, conn, monkeypatch, caplog, mode):
    fake.mode = mode
    monkeypatch.setattr(gbrain, "TIMEOUT", 0.2)
    if mode == "down":
        monkeypatch.setenv("GBRAIN_URL", "http://127.0.0.1:9/mcp")
    m = notes.create(conn, "Matemaatika")
    assert gbrain.search("x") == [] and gbrain.related(conn, m) == []
    assert "gbrain" in caplog.text and TOKEN not in caplog.text
    with pytest.raises(gbrain.GbrainError):
        gbrain.related(conn, m, raise_errors=True)


def test_related_endpoint(fake, owner, conn):
    notes.create(conn, "Matemaatika")
    r = owner.get("/api/related/matemaatika")
    assert r.status_code == 200 and [h["slug"] for h in r.get_json()] == ["pages/naide", "notes/kool/keemia"]
    assert owner.get("/api/related/puudub").status_code == 404
    fake.mode = "401"
    r = owner.get("/api/related/matemaatika")
    assert r.status_code == 503 and r.get_json() == {"error": "gbrain pole praegu kättesaadav."}


def test_related_endpoint_is_owner_only(fake, client, conn):
    notes.create(conn, "Matemaatika")
    assert client.get("/api/related/matemaatika").status_code in (302, 401, 403)
    assert fake.requests == []


@pytest.mark.live
def test_live_search():
    """Against a running gbrain: GBRAIN_URL and GBRAIN_TOKEN_FILE set in the environment."""
    if not os.environ.get("GBRAIN_TOKEN_FILE"):
        pytest.skip("GBRAIN_TOKEN_FILE not set")
    hits = gbrain._search(None, "brain", 5)
    assert isinstance(hits, list) and all({"slug", "title", "snippet", "score"} <= set(h) for h in hits)
