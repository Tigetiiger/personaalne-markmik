"""docs/TUNNEL-PLAN.md, package wp/tunnel: the real client address, Secure cookies and HSTS
behind the Cloudflare Tunnel (security.TunnelProxy, security.cookie_secure), share links and the
ICS feed URL built on PUBLIC_URL, the Host allowlist, and the compose file with and without the
`tunnel` profile."""

import ipaddress
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import auth, create_app, notes
from app.db import connect
from app.security import TunnelProxy
from tests.conftest import OWNER_PASSWORD, CsrfClient

ROOT = Path(__file__).resolve().parents[2]
CLOUDFLARED = "172.30.85.2"      # compose's fixed address of cloudflared
TAILSCALE = "100.64.0.9"
OWNER = "203.0.113.5"
OTHER = "198.51.100.7"


def tunnel(client_ip):
    """What a request through cloudflared looks like to web: plain HTTP from cloudflared's address.
    (The test client sends Secure cookies back over http too, as cloudflared does.)"""
    return {"environ_base": {"REMOTE_ADDR": CLOUDFLARED},
            "headers": {"CF-Connecting-IP": client_ip, "X-Forwarded-Proto": "https"}}


def tailscale():
    return {"environ_base": {"REMOTE_ADDR": TAILSCALE}}


def spoofed():
    """A Tailscale client sending the tunnel's headers itself."""
    return {"environ_base": {"REMOTE_ADDR": TAILSCALE},
            "headers": {"CF-Connecting-IP": OWNER, "X-Forwarded-Proto": "https"}}


class Via(CsrfClient):
    """A browser whose every request arrives the way `how` says (tunnel(), tailscale(), ...)."""

    def __init__(self, app, how):
        super().__init__(app.test_client())
        self.how = how

    def _with(self, kw):
        kw = {**{k: v for k, v in self.how.items() if k != "headers"}, **kw}
        kw["headers"] = {**self.how.get("headers", {}), **(kw.get("headers") or {})}
        return kw

    def get(self, url, **kw):
        return super().get(url, **self._with(kw))

    def _send(self, method, url, **kw):
        return super()._send(method, url, **self._with(kw))


def make_app(tmp_path, **config):
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "DATA_DIR": str(tmp_path),
        "OWNER_PASSWORD": OWNER_PASSWORD,
        "OWNER_PASSWORD_HASH": None,  # an exported .env must not replace the test password
        "SECRET_KEY": "test",
        "TRUSTED_PROXY_NET": f"{CLOUDFLARED}/32",
        **config,
    })


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def seen(trusted, environ):
    """(REMOTE_ADDR, wsgi.url_scheme) the app sees behind TunnelProxy."""
    got = {}

    def inner(env, start_response):
        got.update(addr=env["REMOTE_ADDR"], scheme=env["wsgi.url_scheme"])
        return []

    net = ipaddress.ip_network(trusted) if trusted else None
    TunnelProxy(inner, net)({"wsgi.url_scheme": "http", **environ}, None)
    return got["addr"], got["scheme"]


# --- Header trust --------------------------------------------------------------------------------

def test_headers_honoured_from_the_tunnel_peer():
    env = {"REMOTE_ADDR": CLOUDFLARED, "HTTP_CF_CONNECTING_IP": OWNER, "HTTP_X_FORWARDED_PROTO": "https"}
    assert seen(f"{CLOUDFLARED}/32", env) == (OWNER, "https")


def test_headers_ignored_from_a_tailscale_peer():
    env = {"REMOTE_ADDR": TAILSCALE, "HTTP_CF_CONNECTING_IP": OWNER, "HTTP_X_FORWARDED_PROTO": "https"}
    assert seen(f"{CLOUDFLARED}/32", env) == (TAILSCALE, "http")


def test_the_tunnel_networks_gateway_is_not_trusted():
    # Docker's userland proxy and the host reach web from the network's gateway, .1.
    env = {"REMOTE_ADDR": "172.30.85.1", "HTTP_CF_CONNECTING_IP": OWNER}
    assert seen(f"{CLOUDFLARED}/32", env) == ("172.30.85.1", "http")


def test_nothing_trusted_without_trusted_proxy_net():
    env = {"REMOTE_ADDR": CLOUDFLARED, "HTTP_CF_CONNECTING_IP": OWNER, "HTTP_X_FORWARDED_PROTO": "https"}
    assert seen(None, env) == (CLOUDFLARED, "http")


@pytest.mark.parametrize("value", ["", "nonsense", "1.2.3.4, 5.6.7.8"])
def test_a_missing_or_malformed_client_ip_keeps_the_peer(value):
    env = {"REMOTE_ADDR": CLOUDFLARED, "HTTP_CF_CONNECTING_IP": value}
    assert seen(f"{CLOUDFLARED}/32", env)[0] == CLOUDFLARED


def test_x_forwarded_for_is_never_used():
    env = {"REMOTE_ADDR": CLOUDFLARED, "HTTP_X_FORWARDED_FOR": OWNER}
    assert seen(f"{CLOUDFLARED}/32", env)[0] == CLOUDFLARED


def test_an_unknown_proto_keeps_the_scheme():
    env = {"REMOTE_ADDR": CLOUDFLARED, "HTTP_CF_CONNECTING_IP": OWNER, "HTTP_X_FORWARDED_PROTO": "gopher"}
    assert seen(f"{CLOUDFLARED}/32", env) == (OWNER, "http")


# --- The login lockout and the rate limit see the real client ---------------------------------

def login(app, password, how, client=None):
    return (client or Via(app, how)).post("/login", data={"parool": password})


def test_lockout_keys_on_the_tunnel_clients_address(app):
    c = Via(app, tunnel(OTHER))
    for _ in range(auth.MAX_FAILURES):
        login(app, "vale-parool-1", None, c)
    assert len(auth._failures[OTHER]) >= auth.MAX_FAILURES
    assert CLOUDFLARED not in auth._failures
    # The owner comes through the same tunnel from another address and still gets in.
    r = login(app, OWNER_PASSWORD, tunnel(OWNER))
    assert r.status_code == 302


def test_a_spoofed_header_cannot_lock_out_another_address(app):
    c = Via(app, spoofed())
    for _ in range(auth.MAX_FAILURES):
        login(app, "vale-parool-1", None, c)
    assert len(auth._failures[TAILSCALE]) >= auth.MAX_FAILURES
    assert OWNER not in auth._failures


def test_rate_limit_keys_on_the_tunnel_clients_address(app):
    c = Via(app, tunnel(OTHER))
    codes = [login(app, "vale-parool-1", None, c).status_code for _ in range(25)]
    assert codes[-1] == 429
    # The owner, through the same cloudflared, is not limited by someone else's requests.
    auth._failures.clear()
    assert login(app, OWNER_PASSWORD, tunnel(OWNER)).status_code == 302


def test_a_spoofed_header_is_limited_on_the_real_address(app):
    c = Via(app, spoofed())
    codes = [login(app, "vale-parool-1", None, c).status_code for _ in range(25)]
    assert codes[-1] == 429
    auth._failures.clear()
    assert login(app, OWNER_PASSWORD, tunnel(OWNER)).status_code == 302


@pytest.mark.parametrize("how", [tailscale, lambda: tunnel(OWNER)])
def test_owner_logs_in_both_ways(app, how):
    c = Via(app, how())
    assert login(app, OWNER_PASSWORD, None, c).status_code == 302
    assert c.get("/").status_code == 200


# --- Secure cookies and HSTS -----------------------------------------------------------------

def session_cookie(response):
    found = [h for h in response.headers.getlist("Set-Cookie") if h.startswith("session=")]
    assert found, "no session cookie"
    return found


def test_cookie_secure_and_hsts_through_the_tunnel(app):
    r = login(app, OWNER_PASSWORD, tunnel(OWNER))
    assert r.status_code == 302
    assert all("; Secure" in h for h in session_cookie(r))
    assert r.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_cookie_not_secure_and_no_hsts_over_tailscale(app):
    r = login(app, OWNER_PASSWORD, tailscale())
    assert r.status_code == 302
    assert not any("; Secure" in h for h in session_cookie(r))
    assert "Strict-Transport-Security" not in r.headers


def test_spoofed_proto_does_not_make_tailscale_cookies_secure(app):
    r = login(app, OWNER_PASSWORD, spoofed())
    assert not any("; Secure" in h for h in session_cookie(r))
    assert "Strict-Transport-Security" not in r.headers


def test_https_only_makes_cookies_always_secure(tmp_path):
    app = make_app(tmp_path, SESSION_COOKIE_SECURE=True)
    r = login(app, OWNER_PASSWORD, tailscale())
    assert all("; Secure" in h for h in session_cookie(r))


# --- The Host allowlist ----------------------------------------------------------------------

def test_bad_host_gets_400(app):
    c = app.test_client()
    assert c.get("/login", headers={"Host": "evil.example"}).status_code == 400


def test_tailscale_host_is_allowed(app):
    c = app.test_client()
    assert c.get("/login", headers={"Host": "100.122.97.7:8082"}).status_code == 200


def test_public_url_host_is_allowed_and_others_refused(tmp_path):
    app = make_app(tmp_path, PUBLIC_URL="https://markmik.tigetiiger.ee")
    c = app.test_client()
    assert c.get("/login", headers={"Host": "markmik.tigetiiger.ee"}).status_code == 200
    assert c.get("/login", headers={"Host": "evil.example"}).status_code == 400


# --- PUBLIC_URL: share links and the ICS feed address -------------------------------------------

def owner_client(app):
    c = CsrfClient(app.test_client())
    assert c.post("/login", data={"parool": OWNER_PASSWORD}).status_code == 302
    return c


def test_share_link_uses_public_url(tmp_path):
    app = make_app(tmp_path, PUBLIC_URL="https://markmik.tigetiiger.ee")
    conn = connect(app.config["DATABASE_PATH"])
    note = notes.create(conn, "Jagatud", body="# Jagatud\n")
    r = owner_client(app).post("/api/shares", json={"slug": note["slug"], "mode": "view"})
    assert r.status_code == 201
    assert r.get_json()["url"].startswith("https://markmik.tigetiiger.ee/j/")


def test_share_link_falls_back_to_the_request_host_without_public_url(app):
    conn = connect(app.config["DATABASE_PATH"])
    note = notes.create(conn, "Jagatud", body="# Jagatud\n")
    r = owner_client(app).post("/api/shares", json={"slug": note["slug"], "mode": "view"})
    assert r.get_json()["url"].startswith("http://localhost/j/")


def test_ics_feed_url_uses_public_url(tmp_path):
    app = make_app(tmp_path, PUBLIC_URL="https://markmik.tigetiiger.ee")
    cards = {c["key"]: c for c in owner_client(app).get("/api/connections").get_json()}
    assert cards["phone"]["data"]["url"].startswith("https://markmik.tigetiiger.ee/ics/")


# --- docker-compose.yml ---------------------------------------------------------------------------

@pytest.fixture
def compose_dir(tmp_path):
    """A copy of docker-compose.yml with a throwaway .env: never the real one."""
    if not shutil.which("docker"):
        pytest.skip("docker not installed")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    return tmp_path


def compose(directory, *args):
    return subprocess.run(["docker", "compose", *args], cwd=directory, capture_output=True, text=True)


@pytest.mark.parametrize("token", ["", "dummy-token"])
def test_compose_valid_without_the_profile(compose_dir, token):
    (compose_dir / ".env").write_text(f"COMPOSE_PROJECT_NAME=mcheck\nTUNNEL_TOKEN={token}\n")
    assert compose(compose_dir, "config", "-q").returncode == 0
    services = compose(compose_dir, "config", "--services").stdout.split()
    assert sorted(services) == ["arvutus", "gbrain", "ntfy", "web", "worker"]


def test_compose_valid_with_the_profile(compose_dir):
    (compose_dir / ".env").write_text("COMPOSE_PROJECT_NAME=mcheck\nTUNNEL_TOKEN=dummy-token\n")
    r = compose(compose_dir, "--profile", "tunnel", "config", "-q")
    assert r.returncode == 0, r.stderr
    services = compose(compose_dir, "--profile", "tunnel", "config", "--services").stdout.split()
    assert "cloudflared" in services


def test_compose_trusts_exactly_cloudflareds_address(compose_dir):
    (compose_dir / ".env").write_text("COMPOSE_PROJECT_NAME=mcheck\nTUNNEL_TOKEN=dummy-token\n")
    r = compose(compose_dir, "--profile", "tunnel", "config", "--format", "json")
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    address = services["cloudflared"]["networks"]["tunnelnet"]["ipv4_address"]
    assert services["web"]["environment"]["TRUSTED_PROXY_NET"] == f"{address}/32" == f"{CLOUDFLARED}/32"
    assert services["cloudflared"]["environment"]["TUNNEL_TOKEN"] == "dummy-token"
