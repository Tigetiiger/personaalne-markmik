"""Who is asking, and may they. Default deny: every endpoint needs the owner's session unless it
is marked @public, or @guest(mode) and the visitor holds a valid share link for the note.

g.owner   True when the owner is logged in.
g.share   the shares row granted to this browser (valid, not revoked or expired), or None.
g.actor   {"name", "is_guest"} for authorship and cursor labels.

A guest endpoint must still check the note: require_note(note_id, mode).
"""

import hashlib
import secrets
import time
import unicodedata
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, tx
from .util import now_iso

MIN_PASSWORD = 8
OWNER_NAME = "Omanik"
# Bumped by every password change. A session carries the epoch it logged in under, so a change
# ends every other session (signed cookies have no server-side record to delete).
EPOCH_KEY = "owner_epoch"

# Brute force: after MAX_FAILURES failed logins from one address it is locked for LOCK_S.
# In memory: one gunicorn worker.
MAX_FAILURES = 10
LOCK_S = 15 * 60
_failures = {}


def public(view):
    view.public = True
    return view


def guest(mode):
    """Allow share-link visitors with at least `mode` ('view' or 'edit')."""
    assert mode in ("view", "edit")

    def mark(view):
        view.guest_mode = mode
        return view
    return mark


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _valid_share(conn, share_id):
    # A deleted note takes its links with it (a restore brings them back).
    row = conn.execute(
        "SELECT s.* FROM shares s JOIN notes n ON n.id = s.note_id WHERE s.id = ? AND n.deleted_at IS NULL", (share_id,)
    ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    if row["expires_at"] is not None and row["expires_at"] <= now_iso():
        return None
    return row


def enter_share(conn, token, guest_name=None):
    """Called by the /j/<token> page: remembers the share in this browser's session.
    Returns the share row, or None for an unknown, revoked or expired link."""
    row = conn.execute("SELECT id FROM shares WHERE token_hash = ?", (token_hash(token),)).fetchone()
    share = _valid_share(conn, row["id"]) if row else None
    if share is None:
        return None
    session["share_id"] = share["id"]
    if guest_name is not None:
        session["guest_name"] = clean_name(guest_name)
    conn.execute("UPDATE shares SET last_used_at = ? WHERE id = ?", (now_iso(), share["id"]))
    return share


def clean_name(name):
    """A guest's name as others see it by their caret: one line, no control or format characters
    (a right-to-left override or a zero-width space would disguise it), at most 40 characters."""
    name = "".join(c for c in " ".join(name.split()) if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp"))
    return name[:40].strip() or "Külaline"


def require_note(note_id, mode="view"):
    """Abort 403 unless the owner, or a guest whose share covers this note with `mode`."""
    if g.owner:
        return
    share = g.share
    if share is None or share["note_id"] != note_id or (mode == "edit" and share["mode"] != "edit"):
        abort(403)


def csrf_token():
    """Per-session token every state-changing request must carry: `X-CSRF` header (fetch), `csrf`
    form field, or `csrf` in a JSON body (sendBeacon). Another site cannot read it."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def owner_epoch(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (EPOCH_KEY,)).fetchone()
    return row["value"] if row else "0"


def load_actor():
    # A session from before the epoch key existed carries none: it lasts until the first change.
    g.owner = session.get("owner") is True and session.get("epoch", "0") == owner_epoch(get_db())
    if session.get("owner") and not g.owner:
        session.pop("owner", None)
    share_id = session.get("share_id")
    g.share = _valid_share(get_db(), share_id) if share_id else None
    if share_id and g.share is None:
        session.pop("share_id", None)
    if g.owner:
        g.actor = {"name": OWNER_NAME, "is_guest": False}
    else:
        g.actor = {"name": session.get("guest_name") or "Külaline", "is_guest": True}


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint == "core.login":
        return
    try:
        body = request.get_json(silent=True) if request.is_json else None
    except RecursionError:  # absurdly nested JSON: reject rather than 500
        abort(400)
    sent = request.headers.get("X-CSRF") or request.form.get("csrf")
    if not sent and isinstance(body, dict):
        sent = body.get("csrf")
    if not isinstance(sent, str) or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(403)


def enforce():
    view = current_app.view_functions.get(request.endpoint)
    if view is None or request.endpoint == "static" or getattr(view, "public", False):
        return
    if g.owner:
        return
    mode = getattr(view, "guest_mode", None)
    if mode and g.share is not None and (mode == "view" or g.share["mode"] == "edit"):
        return
    if request.method == "GET" and request.accept_mimetypes.accept_html and not request.path.startswith("/api/"):
        return redirect(url_for("core.login", next=request.full_path.rstrip("?")))
    abort(401)


def init_app(app):
    app.before_request(load_actor)
    app.before_request(check_csrf)
    app.before_request(enforce)
    app.context_processor(lambda: {"csrf_token": csrf_token, "actor": g.get("actor"), "is_owner": g.get("owner")})


# --- Owner account -------------------------------------------------------------------------

def bootstrap_owner(conn, password):
    """First start: OWNER_PASSWORD becomes the owner's password. Later starts ignore it."""
    if password and conn.execute("SELECT 1 FROM owner").fetchone() is None:
        conn.execute("INSERT INTO owner (id, password_hash) VALUES (1, ?)", (generate_password_hash(password),))


def set_password(conn, password):
    """Also logs out every session; the caller gives its own the new owner_epoch."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Parool peab olema vähemalt {MIN_PASSWORD} märki.")
    with tx(conn):
        conn.execute(
            "INSERT INTO owner (id, password_hash) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET password_hash = excluded.password_hash",
            (generate_password_hash(password),),
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (EPOCH_KEY,),
        )


# Checked when there is no owner yet, so the answer takes as long either way.
_DUMMY_HASH = generate_password_hash("puudub")


def check_login(conn, password, address, now=None):
    """True when the password is the owner's. An address with too many failures is refused."""
    now = time.monotonic() if now is None else now
    for key in [k for k, ts in _failures.items() if now - ts[-1] >= LOCK_S]:
        del _failures[key]
    recent = [t for t in _failures.get(address, []) if now - t < LOCK_S]
    if len(recent) >= MAX_FAILURES:
        _failures[address] = recent + [now]
        return False
    row = conn.execute("SELECT password_hash FROM owner WHERE id = 1").fetchone()
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password[:1024]) and row is not None
    if ok:
        _failures.pop(address, None)
    else:
        _failures[address] = recent + [now]
    return ok
