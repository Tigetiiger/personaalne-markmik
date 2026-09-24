"""Minimal gbrain HTTP MCP client (spike S1).

gbrain `serve --http` answers stateless JSON-RPC on /mcp: no initialize
handshake or Mcp-Session-Id is needed, so plain httpx is enough and the app
avoids the mcp SDK (whose 2.x client API changed shape). Replies arrive as SSE
(`data: {...}`); a tool's payload is JSON inside result.content[0].text.

Run:  GBRAIN_TOKEN=... uv run --with httpx spikes/gbrain/client.py "query"
"""

import itertools
import json
import os
import sys

import httpx

GBRAIN_URL = os.environ.get("GBRAIN_URL", "http://127.0.0.1:8083/mcp")
_ids = itertools.count(1)


class GbrainError(RuntimeError):
    pass


def _call(tool, arguments, token=None, timeout=10.0):
    token = token or os.environ["GBRAIN_TOKEN"]
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    r = httpx.post(GBRAIN_URL, json=body, timeout=timeout, headers={
        "Authorization": f"Bearer {token}",
        # Streamable HTTP requires the client to accept both.
        "Accept": "application/json, text/event-stream",
    })
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        data = [l[5:].strip() for l in r.text.splitlines() if l.startswith("data:")]
        msg = json.loads(data[-1])
    else:
        msg = r.json()
    if "error" in msg:
        raise GbrainError(msg["error"].get("message", str(msg["error"])))
    result = msg["result"]
    text = result["content"][0]["text"] if result.get("content") else "null"
    if result.get("isError"):
        raise GbrainError(text)
    return json.loads(text)


def search(query, limit=10, token=None):
    """Keyword search -> [{slug, title, score, snippet}], one row per page."""
    best = {}
    for hit in _call("search", {"query": query, "limit": limit * 3}, token):
        if hit["slug"] not in best or hit["score"] > best[hit["slug"]]["score"]:
            best[hit["slug"]] = {
                "slug": hit["slug"],
                "title": hit.get("title") or hit["slug"],
                "score": hit["score"],
                "snippet": " ".join(hit.get("chunk_text", "").split())[:200],
            }
    return sorted(best.values(), key=lambda h: -h["score"])[:limit]


def get_page(slug, token=None):
    return _call("get_page", {"slug": slug}, token)


def backlinks(slug, token=None):
    return _call("get_backlinks", {"slug": slug}, token)


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "kontrolltöö"
    hits = search(q, limit=5)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    if hits:
        page = get_page(hits[0]["slug"])
        print("get_page:", page["slug"], "|", page["title"], "|", page["type"])
        print("backlinks:", [b["from_slug"] for b in backlinks(hits[0]["slug"])])
