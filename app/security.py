"""Response headers and rate limits. The CSP forbids inline scripts: every script is a file
under /static or /muya, so a guest's HTML in a note can never run as the owner."""

import time
from functools import wraps

from flask import abort, g, request

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


def headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Share tokens are in /j/<token> URLs: never send them to another site.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
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


def init_app(app):
    app.after_request(headers)
