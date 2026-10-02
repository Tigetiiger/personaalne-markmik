"""Response headers, rate limits, and the real client behind the Cloudflare Tunnel. The CSP
forbids inline scripts: every script is a file under /static or /muya, so a guest's HTML in a
note can never run as the owner."""

import ipaddress
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app, g, request
from flask.sessions import SecureCookieSessionInterface

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    # Muya and FullCalendar position popups with inline style attributes.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

# The app's other address (docker-compose.yml): always an allowed Host, alongside PUBLIC_URL's.
TAILSCALE_HOST = "100.122.97.7"


def headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Share tokens are in /j/<token> URLs: never send them to another site.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if response.mimetype in ("text/html", "application/json") and (g.get("owner") or g.get("share")):
        response.headers["Cache-Control"] = "no-store"
    return response


# {(bucket, address): [request times]}. In memory: one gunicorn worker.
_hits = {}


def rate_limit(bucket, limit, per_s):
    """At most `limit` requests per `per_s` seconds per client address, else 429."""
    def wrap(view):
        @wraps(view)
        def limited(*args, **kwargs):
            now = time.monotonic()
            key = (bucket, request.remote_addr)
            recent = [t for t in _hits.get(key, []) if now - t < per_s]
            if len(recent) >= limit:
                _hits[key] = recent
                abort(429)
            _hits[key] = recent + [now]
            return view(*args, **kwargs)
        return limited
    return wrap


class TunnelProxy:
    """WSGI middleware: the client address and scheme from the Cloudflare Tunnel's headers.

    Through the tunnel every request's TCP peer is the cloudflared container, so without this all
    visitors share one address: one rate limit and one login lockout for everyone, and anyone
    could lock the owner out. CF-Connecting-IP (set by Cloudflare's edge) and X-Forwarded-Proto
    are believed only when the peer is in TRUSTED_PROXY_NET, compose's fixed address of
    cloudflared. From any other peer (Tailscale) they are ignored, so nobody can spoof an address
    by sending them. X-Forwarded-For is not used: its leftmost entries come from the client."""

    def __init__(self, wsgi_app, trusted):
        self.wsgi_app = wsgi_app
        self.trusted = trusted

    def __call__(self, environ, start_response):
        if self.trusted and _in(environ.get("REMOTE_ADDR"), self.trusted):
            client = environ.get("HTTP_CF_CONNECTING_IP", "").strip()
            if _in(client, None):
                environ["REMOTE_ADDR"] = client
            proto = environ.get("HTTP_X_FORWARDED_PROTO", "").strip().lower()
            if proto in ("http", "https"):
                environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


def _in(address, network):
    """True when `address` is an IP address (inside `network`, when one is given)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return network is None or ip in network


def cookie_secure():
    """Secure cookies always with HTTPS_ONLY=1, else only for a request that came over HTTPS (the
    tunnel, once TunnelProxy has read X-Forwarded-Proto). Plain-HTTP Tailscale keeps them without
    Secure, or the browser would drop them there and nobody could log in."""
    return current_app.config["SESSION_COOKIE_SECURE"] or request.is_secure


def public_base():
    """The origin to build absolute links on (share links, the ICS feed address): PUBLIC_URL when
    set, so a copied link works for anyone even when the owner is on the Tailscale address; else
    this request's own host."""
    return current_app.config["PUBLIC_URL"] or request.host_url.rstrip("/")


MEDIA_SHARES = "meedia_jaga"  # the blueprint of Meedia's public share pages


def check_host():
    """Only PUBLIC_URL's host, MEEDIA_PUBLIC_URL's host, the Tailscale address and
    localhost/127.0.0.1 (tests) are served, so a forged Host header cannot poison a generated
    share link or ICS feed URL.

    MEEDIA_PUBLIC_URL's host serves Meedia's share pages and nothing else, and they are served
    nowhere else: no login, owner page or file of the app is reachable through it."""
    allowed = {"localhost", "127.0.0.1", TAILSCALE_HOST}
    public, media = current_app.config["PUBLIC_URL"], current_app.config.get("MEEDIA_PUBLIC_URL")
    if public:
        allowed.add(urlsplit(public).hostname)
    if media:
        allowed.add(urlsplit(media).hostname)
    host = urlsplit(f"//{request.host}").hostname
    if host not in allowed:
        abort(400)
    if media and (host == urlsplit(media).hostname) != (request.blueprint == MEDIA_SHARES):
        abort(404)


class SessionInterface(SecureCookieSessionInterface):
    """Files under /static and /muya leave the session cookie alone. Every request reads the
    session (auth.load_actor), so Flask would add "Vary: Cookie", and the cookie is re-signed on
    every response: Firefox then asked again for every cached file on every page."""

    def get_cookie_secure(self, app):
        return cookie_secure()

    def save_session(self, app, session, response):
        if request.endpoint in ("static", "core.muya"):
            return
        super().save_session(app, session, response)


def init_app(app):
    app.before_request(check_host)
    app.session_interface = SessionInterface()
    app.after_request(headers)
    trusted = app.config.get("TRUSTED_PROXY_NET")
    app.wsgi_app = TunnelProxy(app.wsgi_app, ipaddress.ip_network(trusted) if trusted else None)
