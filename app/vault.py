"""Secrets at rest (the vault): account passwords, OAuth tokens, API tokens. Encrypted with Fernet using
MARKMIK_KEY from the environment. The browser only ever sees masked(); nothing here logs values.

Names in use (docs/CONTRACTS.md): claude_token, ekool_username, ekool_password, ekool_tokens,
google_client, google_account:<email>, brain_token, ntfy.
"""

import json
import os

from cryptography.fernet import Fernet, InvalidToken

# Read from the environment when not stored: the Claude token starts life in .env.
ENV_FALLBACK = {"claude_token": "CLAUDE_CODE_OAUTH_TOKEN"}


class SecretsError(RuntimeError):
    pass


def _fernet():
    key = os.environ.get("MARKMIK_KEY")
    if not key:
        raise SecretsError("MARKMIK_KEY puudub (.env)")
    return Fernet(key.encode())


def get(conn, name, default=None):
    row = conn.execute("SELECT ciphertext FROM secrets WHERE name = ?", (name,)).fetchone()
    if row is None:
        env = ENV_FALLBACK.get(name)
        return os.environ.get(env, default) if env else default
    try:
        return _fernet().decrypt(row["ciphertext"]).decode()
    except InvalidToken:
        raise SecretsError(f"saladust {name!r} ei saa lahti krüpteerida (vale MARKMIK_KEY?)") from None


def get_json(conn, name, default=None):
    value = get(conn, name)
    return default if value is None else json.loads(value)


def set(conn, name, value):
    ciphertext = _fernet().encrypt(value.encode())
    conn.execute(
        "INSERT INTO secrets (name, ciphertext) VALUES (?, ?) "
        "ON CONFLICT (name) DO UPDATE SET ciphertext = excluded.ciphertext, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
        (name, ciphertext),
    )


def set_json(conn, name, value):
    set(conn, name, json.dumps(value))


def delete(conn, name):
    conn.execute("DELETE FROM secrets WHERE name = ?", (name,))


def names(conn, prefix=""):
    return [r["name"] for r in conn.execute(
        "SELECT name FROM secrets WHERE name LIKE ? ORDER BY name", (prefix.replace("%", r"\%") + "%",))]


def masked(conn, name):
    """'••••1234' for display, or None when not set."""
    value = get(conn, name)
    if not value:
        return None
    return "••••" + (value[-4:] if len(value) >= 12 else "")
