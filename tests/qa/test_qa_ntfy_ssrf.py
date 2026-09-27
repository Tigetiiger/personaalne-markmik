"""The ntfy URL is owner-set and gets a bearer token, so it must not reach localhost or the
cloud-metadata endpoint (SSRF). The tailnet ntfy (CGNAT 100.64/10) and private nets are allowed;
loopback, link-local, multicast and unspecified are not. IP literals need no DNS."""

import pytest

from app import ics, reminders, vault


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.53", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
])
def test_local_hosts_are_blocked(host):
    assert ics.resolves_to_local(host) is True


@pytest.mark.parametrize("host", [
    "100.122.97.7",   # the user's tailnet ntfy
    "100.64.0.1",     # CGNAT range Tailscale uses
    "192.168.1.10",   # a home LAN ntfy
    "1.1.1.1",        # public
])
def test_allowed_hosts_pass(host):
    assert ics.resolves_to_local(host) is False


def test_publish_refuses_a_local_url(monkeypatch):
    hits = []
    monkeypatch.setattr(reminders.httpx, "post", lambda *a, **k: hits.append(a))
    with pytest.raises(RuntimeError):
        reminders.publish({"url": "http://127.0.0.1:8082", "topic": "t", "token": None},
                          "Title", "Body")
    assert hits == []  # nothing left the server


def test_publish_reaches_an_allowed_url(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200

    def fake_post(url, **kw):
        sent["url"] = url
        sent["follow_redirects"] = kw.get("follow_redirects")
        return Resp()

    monkeypatch.setattr(reminders.httpx, "post", fake_post)
    reminders.publish({"url": "http://100.122.97.7:8090", "topic": "t", "token": None}, "T", "B")
    assert sent["url"] == "http://100.122.97.7:8090"
    assert sent["follow_redirects"] is False  # a redirect could otherwise reach a blocked address


def test_saving_a_local_ntfy_url_is_refused(owner, conn):
    r = owner.post("/api/connections/ntfy",
                   json={"url": "http://127.0.0.1:8999", "topic": "markmik-test"})
    assert r.status_code == 400
    assert vault.get_json(conn, "ntfy") is None  # not stored


def test_saving_the_tailnet_ntfy_url_works(owner, conn):
    r = owner.post("/api/connections/ntfy",
                   json={"url": "http://100.122.97.7:8090", "topic": "markmik-test"})
    assert r.status_code == 200
    assert vault.get_json(conn, "ntfy")["url"] == "http://100.122.97.7:8090"
