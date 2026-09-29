"""Registry of outside accounts shown on Seaded → Ühendused and in the first-run wizard.
Each owning module registers its connection at import time:

    connections.register("ekool", "eKool", order=20, test=test_connection)

test(conn) -> {"ok": bool, "summary": str, "error": str | None}; it must never return a secret.
The last result is kept in settings under "connection:<key>".
"""

import json
from dataclasses import dataclass, field

from .util import now_iso

# Modules that register connections. create_app() and the worker import them; one that does not
# exist yet is skipped (work in progress).
CONNECTION_MODULES = (
    "app.sources.ekool",
    "app.sources.gmail",
    "app.sources.icsfeed",
    "app.gitsync",
    "app.reminders",
    "app.ics",
    "app.stocks.config",
)


@dataclass(frozen=True)
class Connection:
    key: str
    title: str
    order: int
    test: object
    secret_names: tuple = field(default=())


_registry = {}


def register(key, title, order, test, secret_names=()):
    _registry[key] = Connection(key, title, order, test, tuple(secret_names))


def all_connections():
    return sorted(_registry.values(), key=lambda c: c.order)


def get(key):
    return _registry.get(key)


def run_test(conn, key):
    c = _registry[key]
    try:
        result = c.test(conn)
    except Exception as exc:  # a test failing is a result to show, not a crash
        result = {"ok": False, "summary": "", "error": str(exc) or exc.__class__.__name__}
    result = {"ok": bool(result.get("ok")), "summary": result.get("summary", ""), "error": result.get("error"), "at": now_iso()}
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (f"connection:{key}", json.dumps(result)),
    )
    return result


def last_result(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"connection:{key}",)).fetchone()
    return json.loads(row["value"]) if row else None
