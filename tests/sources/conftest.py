import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.sources import gmail

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture(path):
    return json.loads((FIXTURES / path).read_text())


def _ekool_items():
    return [{
        "id": t["id"], "subject": t["subjectName"],
        "date": datetime.strptime(t["deadLine"], "%d.%m.%Y").date().isoformat(),
        "title": t["title"], "content_html": t["content"] or "", "is_test": bool(t["isTest"]),
        "is_done": bool(t["isDone"]),
    } for t in fixture("ekool/todolist.json")["eventList"]]


def _eval_raw(name, **extra):
    case = fixture(f"extract_eval/{name}.json")
    return {"id": name, "thread_id": name, "message_id": f"<{name}@test>", "date_header": case["date_header"],
            "internal_date": None, "from": case["from"], "subject": case["subject"], "text": case["body"],
            "labels": ["INBOX"], "authenticated": True, **extra}


@pytest.fixture
def load():
    return fixture


@pytest.fixture
def ekool_items():
    """tests/fixtures/ekool/todolist.json (legacy mKool shape) as the transport's internal items."""
    return _ekool_items()


@pytest.fixture
def eval_raw():
    """eval_raw(name, **extra): an extract_eval case as the raw item the Gmail adapter stores."""
    return _eval_raw


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in these tests reaches Google or eKool: an unrouted request fails loudly."""
    def refuse(request):
        raise AssertionError(f"unexpected request {request.method} {request.url}")
    monkeypatch.setattr(gmail, "transport", httpx.MockTransport(refuse))


class GoogleFake:
    """Routes for httpx.MockTransport: token endpoint, profile, history, messages. Records requests."""

    def __init__(self):
        self.requests = []
        self.token_replies = []          # queued (status, json) for POST /token; default: fresh token
        self.history = [fixture("gmail/history.json")]   # pages, in order
        self.history_status = 200
        self.listed = []                 # ids returned by messages.list, paged by maxResults
        self.failing = set()             # message ids answered with 503
        self.messages = {m["id"]: m for m in (fixture(f"gmail/{n}.json") for n in (
            "message_1_multipart_mixed_attachment", "message_2_ekool_html_only", "message_3_multipart_alternative"))}
        self.unauthorized_once = False

    def __call__(self, request):
        self.requests.append(request)
        url = request.url
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            if self.token_replies:
                status, body = self.token_replies.pop(0)
                return httpx.Response(status, json=body)
            return httpx.Response(200, json={"access_token": "ya29.fresh", "expires_in": 3599,
                                             "token_type": "Bearer", "scope": gmail.SCOPE})
        if url.host == "oauth2.googleapis.com" and url.path == "/revoke":
            return httpx.Response(200)
        assert url.host == "gmail.googleapis.com", url
        if self.unauthorized_once:
            self.unauthorized_once = False
            return httpx.Response(401, json={"error": {"code": 401}})
        path = url.path.removeprefix("/gmail/v1/users/me")
        if path == "/profile":
            return httpx.Response(200, json=fixture("gmail/profile.json"))
        if path == "/history":
            if self.history_status == 404:
                return httpx.Response(404, json=fixture("gmail/history_404.json"))
            token = url.params.get("pageToken")
            page = int(token) if token else 0
            return httpx.Response(200, json=self.history[page])
        if path == "/messages":
            start, size = int(url.params.get("pageToken", 0)), int(url.params.get("maxResults", 100))
            reply = {"messages": [{"id": i, "threadId": i} for i in self.listed[start:start + size]],
                     "resultSizeEstimate": len(self.listed)}
            if start + size < len(self.listed):
                reply["nextPageToken"] = str(start + size)
            return httpx.Response(200, json=reply)
        if path.startswith("/messages/"):
            message_id = path.rsplit("/", 1)[1]
            if message_id in self.failing:
                return httpx.Response(503)
            msg = self.messages.get(message_id)
            if msg is None:
                return httpx.Response(404, json={"error": {"code": 404}})
            if url.params.get("format") == "metadata":  # headers only, as Gmail sends them
                headers = [h for h in msg["payload"]["headers"] if h["name"] in url.params.get_list("metadataHeaders")]
                msg = {**msg, "payload": {"mimeType": msg["payload"]["mimeType"], "headers": headers}}
            return httpx.Response(200, json=msg)
        raise AssertionError(f"unrouted {url}")

    def paths(self):
        return [r.url.path.removeprefix("/gmail/v1/users/me") for r in self.requests if r.url.host == "gmail.googleapis.com"]

    def fetched(self, fmt):
        return [r.url.path.rsplit("/", 1)[1] for r in self.requests
                if "/messages/" in r.url.path and r.url.params.get("format") == fmt]


@pytest.fixture
def google(monkeypatch):
    fake = GoogleFake()
    monkeypatch.setattr(gmail, "transport", httpx.MockTransport(fake))
    return fake


ACCOUNT = "opilane.naidis@gmail.com"
CLIENT = {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test-secret",
          "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": gmail.TOKEN_URI,
          "redirect_uris": ["http://localhost"]}


@pytest.fixture
def gmail_source(conn):
    """A connected Gmail account with a valid access token. -> sources.id"""
    import time

    from app import vault
    from app.sources.base import upsert_source
    vault.set_json(conn, "google_client", CLIENT)
    vault.set_json(conn, f"google_account:{ACCOUNT}", {
        "email": ACCOUNT, "refresh_token": "1//refresh", "access_token": "ya29.valid", "expires_at": time.time() + 3000})
    return upsert_source(conn, "gmail", ACCOUNT)
