"""Share links for kept files (docs/MEEDIA-PLAN.md, calls 19-23). A link shows one file or a
selection, on its own host (MEEDIA_PUBLIC_URL). The token is 256 random bits; only its SHA-256 is
stored, so the owner sees a link's URL only when making it, as with note links (app/shares.py)."""

import secrets
from datetime import timedelta

from .. import auth
from ..db import tx
from ..util import iso, now_iso, now_utc

MAX_ITEMS = 500
EXPIRY_DAYS = (1, 7, 30, 365)

# A link is open while it is neither revoked nor past its expiry.
OPEN = "s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > ?)"


def create(conn, names, *, title="", days=None, download=False):
    """New link -> (row, token). names: kept files, in the order shown. days: one of EXPIRY_DAYS,
    or None (never expires). ValueError (Estonian) for a bad request."""
    names = list(dict.fromkeys(names))
    if not names:
        raise ValueError("Vali vähemalt üks fail.")
    if len(names) > MAX_ITEMS:
        raise ValueError(f"Ühe lingiga saab jagada kuni {MAX_ITEMS} faili.")
    if days is not None and days not in EXPIRY_DAYS:
        raise ValueError("Vigane aegumise aeg.")
    token = secrets.token_urlsafe(32)
    with tx(conn):
        known = {r[0] for r in conn.execute(
            f"SELECT name FROM media WHERE name IN ({','.join('?' * len(names))})", names)}
        if len(known) != len(names):
            raise ValueError("Mõnda faili pole enam.")
        share_id = conn.execute(
            "INSERT INTO media_shares (token_hash, title, download, expires_at) VALUES (?, ?, ?, ?)",
            (auth.token_hash(token), title.strip()[:200], int(bool(download)),
             iso(now_utc() + timedelta(days=days)) if days else None)).lastrowid
        conn.executemany("INSERT INTO media_share_items (share_id, position, name) VALUES (?, ?, ?)",
                         [(share_id, i, n) for i, n in enumerate(names)])
    return get(conn, share_id), token


def get(conn, share_id):
    return conn.execute("SELECT * FROM media_shares WHERE id = ?", (share_id,)).fetchone()


def by_token(conn, token):
    """The open link of `token`, or None (unknown, revoked or expired: all the same to a visitor)."""
    return conn.execute(f"SELECT s.* FROM media_shares s WHERE s.token_hash = ? AND {OPEN}",
                        (auth.token_hash(token), now_iso())).fetchone()


def items(conn, share_id):
    """The link's files still kept, in order, as media rows."""
    return conn.execute(
        "SELECT m.* FROM media_share_items i JOIN media m ON m.name = i.name WHERE i.share_id = ? ORDER BY i.position",
        (share_id,)).fetchall()


def item(conn, share_id, name):
    return conn.execute(
        "SELECT m.* FROM media_share_items i JOIN media m ON m.name = i.name WHERE i.share_id = ? AND i.name = ?",
        (share_id, name)).fetchone()


def list_open(conn, name=None):
    """Open links, newest first, each with its file count and first file; only those holding
    `name` when given."""
    where, args = OPEN, [now_iso()]
    if name is not None:
        where += " AND s.id IN (SELECT share_id FROM media_share_items WHERE name = ?)"
        args.append(name)
    return conn.execute(
        f"SELECT s.*, (SELECT count(*) FROM media_share_items i WHERE i.share_id = s.id) AS files, "
        f"(SELECT i.name FROM media_share_items i WHERE i.share_id = s.id ORDER BY i.position LIMIT 1) AS first "
        f"FROM media_shares s WHERE {where} ORDER BY s.created_at DESC, s.id DESC", args).fetchall()


def revoke(conn, share_id):
    """True when an open link was revoked."""
    return conn.execute("UPDATE media_shares SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                        (now_iso(), share_id)).rowcount == 1
