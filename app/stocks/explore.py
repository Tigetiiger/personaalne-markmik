"""Avasta: stock ideas outside the watchlist, on demand (stock-oracle's research/explore.py,
rewritten without WebFetch). A discovery call whose only tool is WebSearch names pages; its prompt
holds the focus, the count, the date and the followed tickers, nothing else. Code fetches the
pages; a call with no tools writes the ideas from them. An idea's links survive only when they are
pages fetched here; an idea left without a link, or for a stock already followed, is dropped, and
every drop is kept in `dropped_json` with its reason."""

import json
import logging
import re

from . import aicall, common, config
from .prompts import load
from .research import (CATALYST, DISCOVER_TIMEOUT, DISCOVER_TURNS, MAX_PAGES, RESEARCH_TIMEOUT, candidates, catalysts,
                       fence, fetch_pages, held_ids, page_blocks, say, text, texts, web_url)
from .sources.models import normalize_url

log = logging.getLogger("markmik.stocks.ai")

MIN_IDEAS, MAX_IDEAS, DEFAULT_IDEAS = 3, 10, 6
FOCUS_MAX = 200
KINDS = ("stock", "etf")
HORIZONS = ("weeks", "months", "years")
CONVICTIONS = ("low", "medium", "high")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-^=]{0,19}")
# The reasons stored in dropped_json are stock-oracle's, so imported runs read the same.
NOT_FETCHED, NO_LINKS, FOLLOWED, DUPLICATE, INVALID = "not fetched", "no verified links", "already followed", "duplicate", "invalid"

DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "https address of the page itself"},
            "title": {"type": "string"},
            "symbol": {"type": "string", "description": "the ticker the page is about"},
            "why": {"type": "string", "description": "one sentence: what the page shows"},
        },
        "required": ["url", "title", "symbol", "why"],
        "additionalProperties": False,
    }}},
    "required": ["candidates"],
    "additionalProperties": False,
}

_LINK = {"type": "object", "properties": {"url": {"type": "string"}, "title": {"type": "string"}, "why": {"type": "string"}},
         "required": ["url", "title", "why"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "market_context": {"type": "string"},
        "ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "name": {"type": "string"},
                "exchange": {"type": "string"},
                "kind": {"type": "string", "enum": list(KINDS)},
                "what_it_is": {"type": "string"},
                "why_interesting": {"type": "string"},
                "catalysts": {"type": "array", "items": CATALYST},
                "risks": {"type": "array", "items": {"type": "string"}},
                "horizon": {"type": "string", "enum": list(HORIZONS)},
                "conviction": {"type": "string", "enum": list(CONVICTIONS)},
                "links": {"type": "array", "items": _LINK},
            },
            "required": ["symbol", "name", "exchange", "kind", "what_it_is", "why_interesting", "catalysts", "risks",
                         "horizon", "conviction", "links"],
            "additionalProperties": False,
        }},
    },
    "required": ["market_context", "ideas"],
    "additionalProperties": False,
}


def clamp_count(count):
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_IDEAS
    return max(MIN_IDEAS, min(MAX_IDEAS, count))


def followed(conn):
    """(symbol, name) of everything on the watchlist or held."""
    rows = {r["id"]: r for r in common.followed(conn)}
    for iid in held_ids(conn) - rows.keys():
        if (row := common.instrument(conn, iid)) is not None:
            rows[iid] = row
    return sorted((r["symbol"], r["name"]) for r in rows.values())


def symbol_key(symbol):
    """"SAP.DE" and "sap" both compare as "SAP": another listing of a followed stock is still the
    same company."""
    return symbol.strip().upper().split(".")[0]


def check_ideas(answer, fetched_urls, followed_symbols):
    """(ideas kept, dropped [{kind: "link" | "idea", symbol, url?, reason}])."""
    allowed = {k for u in fetched_urls if (k := normalize_url(u))}
    skip = {symbol_key(s) for s in followed_symbols}
    seen, ideas, dropped = set(), [], []
    for idea in answer.get("ideas") or []:
        if not isinstance(idea, dict):
            dropped.append({"kind": "idea", "symbol": None, "reason": INVALID})
            continue
        raw = idea.get("symbol")
        symbol = raw.strip().upper() if isinstance(raw, str) else ""
        name = text(idea.get("name"), 200)
        if not SYMBOL.fullmatch(symbol) or name is None or idea.get("kind") not in KINDS:
            dropped.append({"kind": "idea", "symbol": str(raw)[:20], "reason": INVALID})
            continue
        key = symbol_key(symbol)
        if key in skip:
            dropped.append({"kind": "idea", "symbol": raw, "reason": FOLLOWED})
            continue
        if key in seen:
            dropped.append({"kind": "idea", "symbol": raw, "reason": DUPLICATE})
            continue
        links = []
        for link in idea.get("links") or []:
            url = web_url(link.get("url")) if isinstance(link, dict) else None
            if url and normalize_url(url) in allowed:
                links.append({"url": url, "title": text(link.get("title"), 200) or url, "why": text(link.get("why"), 300) or ""})
            else:
                dropped.append({"kind": "link", "symbol": raw, "url": link.get("url") if isinstance(link, dict) else None,
                                "reason": NOT_FETCHED})
        if not links:
            dropped.append({"kind": "idea", "symbol": raw, "reason": NO_LINKS})
            continue
        seen.add(key)
        ideas.append({
            "symbol": symbol, "name": name, "exchange": text(idea.get("exchange"), 40) or "", "kind": idea["kind"],
            "what_it_is": text(idea.get("what_it_is"), 1000) or "", "why_interesting": text(idea.get("why_interesting"), 2000) or "",
            "catalysts": catalysts(idea.get("catalysts")), "risks": texts(idea.get("risks"), 8, 500),
            "horizon": idea.get("horizon") if idea.get("horizon") in HORIZONS else None,
            "conviction": idea.get("conviction") if idea.get("conviction") in CONVICTIONS else None,
            "links": links[:5],
        })
    return ideas, dropped


def discovery_prompt(today, count, focus, symbols, fence_=None):
    """-> (prompt, prompt_version). Public facts and tickers only: this call has WebSearch."""
    fence_ = fence_ or fence()
    prompt = load("explore_discover")
    return prompt.render(fence=fence_, as_of=today.isoformat(), count=count, pages=MAX_PAGES, focus=focus or "none",
                         followed=", ".join(symbols) or "(nothing yet)"), prompt.version


def writeup_prompt(today, count, focus, follows, pages, fence_=None):
    """-> (prompt, prompt_version)"""
    fence_ = fence_ or fence()
    prompt = load("explore")
    return prompt.render(fence=fence_, as_of=today.isoformat(), count=count, focus=focus or "none",
                         followed="\n".join(f"- {s} ({n})" for s, n in follows) or "(nothing yet)",
                         page_count=len(pages), pages=page_blocks(pages, fence_)), prompt.version


def explore(conn, focus, count, today, progress=None):
    """One Avasta run; returns the explorations id. Raises runner.AIUnavailable, or RuntimeError
    (Estonian) when nothing could be written; nothing is stored then."""
    count = clamp_count(count)
    focus = " ".join(focus.split())[:FOCUS_MAX] if isinstance(focus, str) and focus.strip() else None
    model = config.settings(conn)["research_model"]
    follows = followed(conn)

    say(progress, "Otsin allikaid…")
    prompt, version = discovery_prompt(today, count, focus, [s for s, _ in follows])
    found = aicall.call(conn, "discover", prompt, DISCOVER_SCHEMA, model=model, prompt_version=version, web_search=True,
                        timeout=DISCOVER_TIMEOUT, max_turns=DISCOVER_TURNS)
    pages = fetch_pages(conn, candidates(found), progress)
    if not pages:
        raise RuntimeError("Ühtegi lehte ei õnnestunud lugeda. Proovi hiljem uuesti.")

    say(progress, "Kirjutan…")
    prompt, version = writeup_prompt(today, count, focus, follows, pages)
    answer, ai_run_id = aicall.call_logged(conn, "explore", prompt, SCHEMA, model=model, prompt_version=version,
                                           timeout=RESEARCH_TIMEOUT, thinking=True)
    context = text(answer.get("market_context"), 3000)
    if context is None:
        raise RuntimeError("AI vastus on vigane (turu taust puudub)")
    ideas, dropped = check_ideas(answer, [p["url"] for p in pages], [s for s, _ in follows])
    if dropped:
        log.warning("explore: dropped %s", dropped)
    return conn.execute(
        "INSERT INTO explorations (ai_run_id, focus, requested_ideas, market_context, ideas_json, dropped_json, "
        "prompt_version, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ai_run_id, focus, count, context, json.dumps(ideas, ensure_ascii=False), json.dumps(dropped, ensure_ascii=False),
         version, model)).lastrowid
