"""Read-only client for gbrain's HTTP MCP server (docs/spikes/gbrain.md, spikes/gbrain/client.py).

gbrain answers stateless JSON-RPC on /mcp (no initialize, no session id), with SSE replies whose
tool payload is JSON in result.content[0].text, so plain httpx is enough. The app never writes to
gbrain: notes reach it through git, and the gbrain container syncs the clone itself.

Config: setting "gbrain_url" (else env GBRAIN_URL, else the compose address). Token: vault
"gbrain_token" when the owner stored one, else the read-only token the gbrain container writes
to <DATA_DIR>/gbrain/app_token on its first start (env GBRAIN_TOKEN_FILE overrides the path).
"""

import itertools
import json
import logging
import os
import re
from pathlib import Path

import httpx

from . import notes, vault

log = logging.getLogger("markmik.gbrain")

DEFAULT_URL = "http://gbrain:8083/mcp"
TIMEOUT = 3.0
_ids = itertools.count(1)


class GbrainError(RuntimeError):
    pass


def search(query, limit=8, *, conn=None):
    """[{slug, title, snippet, score}], one per page, best first; [] when gbrain is unreachable."""
    try:
        return _search(conn, query, limit)
    except GbrainError as exc:
        log.warning("gbrain search failed: %s", exc)
        return []


def related(conn, note, *, raise_errors=False):
    """Brain pages related to a note (same shape as search), the note's own page left out.
    The query is the title and the first lines, any word matching (gbrain search ANDs words)."""
    words = []
    for line in [note["title"], *note["body"].splitlines()[:8]]:
        for word in re.findall(r"\w{4,}", notes.strip_markdown(line).lower()):
            if word not in words:
                words.append(word)
    try:
        hits = _search(conn, " or ".join(words[:10]), 9) if words else []
    except GbrainError as exc:
        if raise_errors:
            raise
        log.warning("gbrain related failed: %s", exc)
        return []
    own = f"/{note['slug']}"
    return [h for h in hits if not (h["slug"].startswith("notes/") and h["slug"].endswith(own))][:8]


def _search(conn, query, limit):
    """Search returns chunks, several per page: keep each page's best."""
    best = {}
    try:
        for hit in _call(conn, "search", {"query": query, "limit": limit * 3}) or []:
            slug = hit.get("slug")
            if slug and (slug not in best or hit.get("score", 0) > best[slug]["score"]):
                best[slug] = {"slug": slug, "title": hit.get("title") or slug, "score": hit.get("score", 0),
                              "snippet": _plain(str(hit.get("chunk_text", "")))[:200]}
        return sorted(best.values(), key=lambda h: -h["score"])[:limit]
    except (TypeError, AttributeError) as exc:
        raise GbrainError(f"search: unexpected reply ({exc.__class__.__name__})") from None


def _plain(markdown):
    return " ".join(filter(None, (notes.strip_markdown(line) for line in markdown.splitlines())))


def _call(conn, tool, arguments):
    url, token = _config(conn)
    if not token:
        raise GbrainError("no token")
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    try:
        r = httpx.post(url, json=body, timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",  # Streamable HTTP wants both
        })
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            data = [line[5:].strip() for line in r.text.splitlines() if line.startswith("data:")]
            msg = json.loads(data[-1])
        else:
            msg = r.json()
        if "error" in msg:
            raise GbrainError(f"{tool}: {msg['error'].get('message', 'error')}")
        result = msg["result"]
        text = result["content"][0]["text"] if result.get("content") else "null"
        if result.get("isError"):
            raise GbrainError(f"{tool}: {text[:200]}")
        return json.loads(text)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GbrainError(f"{tool}: {exc.__class__.__name__}") from None


def _config(conn):
    url = token = None
    if conn is not None:
        row = conn.execute("SELECT value FROM settings WHERE key = 'gbrain_url'").fetchone()
        url = row["value"] if row else None
        try:
            token = vault.get(conn, "gbrain_token")
        except vault.SecretsError:
            token = None
    url = url or os.environ.get("GBRAIN_URL") or DEFAULT_URL
    if not token:
        path = os.environ.get("GBRAIN_TOKEN_FILE") or Path(os.environ.get("DATA_DIR", "data")) / "gbrain" / "app_token"
        try:
            token = Path(path).read_text().strip()
        except OSError:
            token = None
    return url, token
