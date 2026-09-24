"""Fixtures for WP-E2. The eKool, Gmail, ICS-feed and phone helpers are built by other packages in
parallel; E2 only calls them, so tests put fakes in sys.modules and in the connection registry
(both restored after each test)."""

import sys
import types

import pytest

from app import connections, events, vault

# Known secret values: none of them may ever appear in a response.
SECRET = {
    "claude_token": "sk-ant-oat01-SALAJANE-claude-8841",
    "ekool_username": "salajane.kasutaja.5521",
    "ekool_password": "Salajane-Parool-9931",
    "ekool_access": "ekool-access-SALAJANE-6612",
    "google_client_secret": "GOCSPX-SALAJANE-client-3321",
    "google_refresh": "1//SALAJANE-refresh-token-4411",
    "brain_token": "github_pat_SALAJANE_brain_2231",
    "ntfy_topic": "salajane-teema-7719",
    "ntfy_token": "tk_SALAJANEntfytoken6612",
    "ics_private": "private-SALAJANE-ics-5577",
    "google_pending": "pkce-verifier-SALAJANE-3141",
}
CLIENT_JSON = ('{"installed": {"client_id": "123.apps.googleusercontent.com", "client_secret": "%s", '
               '"redirect_uris": ["http://localhost"]}}' % SECRET["google_client_secret"])
EMAIL = "opilane@example.com"


@pytest.fixture
def proposed(conn):
    """proposed(title, **columns) -> id of a new proposed event (a timed one by default)."""
    def make(title="Matemaatika kontrolltöö", **fields):
        return events.create(conn, {
            "title": title, "kind": "kontrolltöö", "status": "pakutud", "source": "ai",
            "start_at": "2026-10-02T07:15:00Z", "source_quote": "Reedel kell 10.15 on kontrolltöö.",
            "confidence": "low", **fields,
        })
    return make


class Owners:
    """Fake owner modules. `results[key]` is what that connection's Testi returns (an Exception is
    raised); `calls` records every helper call without its conn."""

    client_json = CLIENT_JSON
    email = EMAIL

    def __init__(self):
        self.calls = []
        self.results = {}
        self.feeds = []
        self.ekool = types.SimpleNamespace(save_credentials=self._save_credentials)
        self.gmail = types.SimpleNamespace(
            save_client=self._save_client, auth_url=self._auth_url, finish_auth=self._finish_auth,
            accounts=self._accounts, remove_account=self._remove_account)
        self.icsfeed = types.SimpleNamespace(feeds=lambda conn: list(self.feeds), add_feed=self._add_feed,
                                             remove_feed=self._remove_feed)
        self.ics = types.SimpleNamespace(feed_url=lambda conn: "/ics/tok123.ics")

    def _save_credentials(self, conn, username, password):
        self.calls.append(("save_credentials", username, password))
        if password == "vale":
            raise ValueError("eKool ei tundnud kasutajat ära.")
        vault.set(conn, "ekool_username", username)
        vault.set(conn, "ekool_password", password)

    def _save_client(self, conn, text):
        self.calls.append(("save_client",))
        if '"installed"' not in text:
            raise ValueError("See pole Google'i Desktop app kliendi JSON.")
        vault.set(conn, "google_client", text)

    def _auth_url(self, conn):
        if vault.get(conn, "google_client") is None:
            raise ValueError("Laadi kõigepealt OAuth-klient üles.")
        return "https://accounts.google.com/o/oauth2/v2/auth?client_id=123&state=s1"

    def _finish_auth(self, conn, pasted_url):
        self.calls.append(("finish_auth", pasted_url))
        if "code=" not in pasted_url:
            raise ValueError("Aadressist ei leitud Google'i koodi.")
        vault.set_json(conn, "google_account:" + EMAIL, {"refresh_token": SECRET["google_refresh"]})
        return EMAIL

    def _accounts(self, conn):
        # An extra key on purpose: E2 must pass on only email, ok, last_error and last_ok_at.
        return [{"email": n.split(":", 1)[1], "ok": True, "last_error": None, "last_ok_at": "2026-09-24T08:00:00Z",
                 "refresh_token": vault.get_json(conn, n)["refresh_token"]}
                for n in vault.names(conn, "google_account:")]

    def _remove_account(self, conn, email):
        self.calls.append(("remove_account", email))
        vault.delete(conn, "google_account:" + email)

    def _add_feed(self, conn, url, name):
        self.calls.append(("add_feed", url, name))
        self.feeds.append({"id": len(self.feeds) + 1, "name": name, "url": url, "last_error": None, "last_ok_at": None})
        return len(self.feeds)

    def _remove_feed(self, conn, source_id):
        self.calls.append(("remove_feed", source_id))
        self.feeds = [f for f in self.feeds if f["id"] != source_id]

    def run(self, key):
        def test(conn):
            result = self.results.get(key, {"ok": True, "summary": f"{key} korras", "error": None})
            if isinstance(result, Exception):
                raise result
            return result
        return test


@pytest.fixture
def owners(monkeypatch):
    fake = Owners()
    for name, module in (("app.sources.ekool", fake.ekool), ("app.sources.gmail", fake.gmail),
                         ("app.sources.icsfeed", fake.icsfeed), ("app.ics", fake.ics)):
        monkeypatch.setitem(sys.modules, name, module)
    for key, title, order in (("ekool", "eKool", 20), ("google", "Google'i kontod", 30), ("brain", "Brain repo", 40),
                              ("ntfy", "ntfy teavitused", 50), ("ics", "Kalendrivood (ICS)", 60), ("phone", "Telefoni kalender", 70)):
        monkeypatch.setitem(connections._registry, key, connections.Connection(key, title, order, fake.run(key)))
    return fake


@pytest.fixture
def missing_owners(monkeypatch):
    """As before the other packages are merged: no owner modules, only Wave 0's claude registered."""
    for name in ("app.sources.ekool", "app.sources.gmail", "app.sources.icsfeed", "app.ics"):
        monkeypatch.setitem(sys.modules, name, None)
    for key in ("ekool", "google", "brain", "ntfy", "ics", "phone"):
        monkeypatch.delitem(connections._registry, key, raising=False)


@pytest.fixture
def stored_secrets(conn):
    """Stores a known value for every card's secrets; returns the values that must stay hidden."""
    vault.set(conn, "claude_token", SECRET["claude_token"])
    vault.set(conn, "ekool_username", SECRET["ekool_username"])
    vault.set(conn, "ekool_password", SECRET["ekool_password"])
    vault.set_json(conn, "ekool_tokens", {"access": SECRET["ekool_access"]})
    vault.set(conn, "google_client", CLIENT_JSON)
    vault.set_json(conn, "google_account:" + EMAIL, {"refresh_token": SECRET["google_refresh"]})
    vault.set_json(conn, "google_auth_pending", {"verifier": SECRET["google_pending"], "state": "s1"})
    vault.set(conn, "brain_token", SECRET["brain_token"])
    vault.set_json(conn, "ntfy", {"url": "http://100.122.97.7:8090", "topic": SECRET["ntfy_topic"], "token": SECRET["ntfy_token"]})
    return SECRET
