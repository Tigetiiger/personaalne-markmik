"""The night's extraction and the deep research (docs/AKTSIAD-PLAN.md, "AI"; stock-oracle's
research/extract.py, synthesize.py and night.py, rewritten).

Extraction: per stock, the documents of the last 7 days without a signal, anonymised ("Company
X"), in batches of at most 25 documents or 60,000 characters, through haiku with no tools. Only
the ids that were sent are kept.

Research never gives the model WebFetch (a way to send anything anywhere). It runs in three steps:
1. discovery: a call whose only tool is WebSearch names candidate pages. Its prompt holds only
   public facts: the company, its ticker, the date and the previous note's summary with anything
   that looks like the owner's position taken out. Never a quantity, a cost, a value or an account.
2. code fetches at most MAX_PAGES of them with ics.download (public addresses only, size and time
   caps) and keeps their text;
3. synthesis: a call with no tools (thinking on) writes the note from those pages, the scored
   documents, the price statistics and trend, the previous note and the position.
A citation survives only when its URL is a page fetched in step 2 or a stored document, and a claim
whose citation is dropped goes with it.

The *_night functions never raise for the model: they return {done, skipped, failed, stopped}
(docs/CONTRACTS.md, "stocks-ai")."""

import json
import logging
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .. import ics
from ..ai import runner
from ..db import tx
from ..util import iso
from . import aicall, common, config, market, portfolio
from .prompts import load
from .sources.models import normalize_url, sha256
from .sources.text import html_to_text

log = logging.getLogger("markmik.stocks.ai")

# --- Nights: the loop every AI step shares ----------------------------------------------------------

DEADLINE_REASON = "Öine AI-aeg sai otsa"
ALREADY_DONE = "juba tehtud"


def clock(now):
    """`now` (aware UTC) or a callable returning it, as a callable: long steps see time pass."""
    return now if callable(now) else (lambda: now)


def run_night(rows, deadline, now, step, *, done_already=None):
    """step(row) for each instrument row in order, until the deadline or runner.AIUnavailable.
    {done: [symbols], skipped: [{symbol, reason}], failed: [{symbol, error}], stopped: reason | None}"""
    now = clock(now)
    out = {"done": [], "skipped": [], "failed": [], "stopped": None}
    for row in rows:
        symbol = row["symbol"]
        if done_already is not None and done_already(row):
            out["skipped"].append({"symbol": symbol, "reason": ALREADY_DONE})
            continue
        if out["stopped"] is None and now() >= deadline:
            out["stopped"] = DEADLINE_REASON
        if out["stopped"] is not None:
            out["skipped"].append({"symbol": symbol, "reason": out["stopped"]})
            continue
        try:
            step(row)
        except runner.AIUnavailable as exc:
            log.warning("stocks AI night stopped at %s: %s", symbol, exc)
            out["stopped"] = str(exc)
            out["skipped"].append({"symbol": symbol, "reason": out["stopped"]})
        except Exception as exc:  # noqa: BLE001 - one stock's failure must not end the night
            if isinstance(exc, RuntimeError):
                log.warning("stocks AI %s failed: %s", symbol, exc)
                error = str(exc)
            else:
                log.exception("stocks AI %s failed", symbol)
                error = f"Ootamatu viga ({type(exc).__name__})"
            out["failed"].append({"symbol": symbol, "error": error[:300]})
        else:
            out["done"].append(symbol)
    return out


def held_ids(conn):
    """Instruments held in any account; an unreadable ledger counts as none (logged)."""
    try:
        return set(portfolio.holdings(conn))
    except portfolio.PortfolioError:
        log.exception("holdings not read")
        return set()


def night_instruments(conn, held):
    """The watchlist, plus holdings that are not on it (a buy adds one, but it may be removed)."""
    rows = {r["id"]: r for r in common.followed(conn)}
    for iid in held - rows.keys():
        row = common.instrument(conn, iid)
        if row is not None and row["kind"] in ("stock", "etf"):
            rows[iid] = row
    return list(rows.values())


def say(progress, text):
    if progress is not None:
        progress(text)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def json_lines(items):
    """A JSON array with one element per line: compact, but diffable in the prompt snapshots."""
    return "[\n" + ",\n".join(dumps(i) for i in items) + "\n]" if items else "[]"


def fence():
    return secrets.token_hex(6)


# --- Checks on what the model returns ----------------------------------------------------------------

def text(value, cap):
    """A non-empty string, whitespace collapsed and capped; None otherwise."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:cap] if value else None


def texts(value, most, cap):
    return [t for v in value if (t := text(v, cap))][:most] if isinstance(value, list) else []


def iso_date(value):
    """A 'YYYY-MM-DD' that is a real date, else None (the CLI does not enforce date formats)."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def catalysts(value):
    out = []
    for c in value if isinstance(value, list) else []:
        if isinstance(c, dict) and (event := text(c.get("event"), 300)):
            out.append({"event": event, "date": iso_date(c.get("date"))})
    return out[:10]


def web_url(value):
    """An http(s) address as the model gave it (trimmed), else None: nothing else becomes a link."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.hostname and len(value) <= 2000 else None


# --- Pages: discovery's candidates, fetched by code ------------------------------------------------

MAX_PAGES = 8
PAGE_SECONDS = 20
PAGE_CHARS = 10_000
MIN_PAGE_CHARS = 200  # less is a cookie wall or an empty shell, not something to cite
BROWSER_AGENT = "Mozilla/5.0 (Markmik)"
DISCOVER_TIMEOUT = 600  # docs/spikes/agent-sdk.md, S5: 36 s for six candidates; slow days take longer
DISCOVER_TURNS = 12

DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "https address of the page itself"},
            "title": {"type": "string"},
            "why": {"type": "string", "description": "one sentence: what the page adds"},
        },
        "required": ["url", "title", "why"],
        "additionalProperties": False,
    }}},
    "required": ["candidates"],
    "additionalProperties": False,
}


def candidates(answer, most=MAX_PAGES):
    """Discovery's pages [{url, title, why, symbol?}]: https only, each page once, at most `most`."""
    out, seen = [], set()
    for c in answer.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        url = c.get("url").strip() if isinstance(c.get("url"), str) else ""
        key = normalize_url(url)
        if not re.fullmatch(r"https://[^\s\"'<>]{4,2000}", url) or key is None or key in seen:
            continue
        seen.add(key)
        cand = {"url": url, "title": text(c.get("title"), 200) or url, "why": text(c.get("why"), 300) or ""}
        if isinstance(c.get("symbol"), str):
            cand["symbol"] = text(c["symbol"], 20) or ""
        out.append(cand)
        if len(out) == most:
            break
    return out


def _agent(conn, url):
    """The User-Agent a site answers: SEC wants contact details (the Turuandmed card's), most news
    and company sites refuse httpx's own. None: do not fetch (SEC without contact details)."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "sec.gov" or host.endswith(".sec.gov"):
        return config.secret(conn, "sec_user_agent")
    return BROWSER_AGENT


def fetch_pages(conn, cands, progress=None):
    """[{url, title, text}] of the candidates that could be read, in their order. A page that
    cannot be fetched, is a PDF or has almost no text is skipped (logged, never raised)."""
    pages = []
    for i, cand in enumerate(cands, 1):
        say(progress, f"Loen {i}/{len(cands)} lehte…")
        agent = _agent(conn, cand["url"])
        if agent is None:
            log.info("page %s skipped: no SEC User-Agent set", cand["url"])
            continue
        try:
            raw = ics.download(cand["url"], PAGE_SECONDS, user_agent=agent)
        except ValueError as exc:
            log.info("page %s skipped: %s", cand["url"], exc)
            continue
        if raw.lstrip()[:5] == b"%PDF-":
            log.info("page %s skipped: a PDF", cand["url"])
            continue
        body = html_to_text(raw.decode("utf-8", errors="replace"))
        if len(body) < MIN_PAGE_CHARS:
            log.info("page %s skipped: %d characters of text", cand["url"], len(body))
            continue
        pages.append({"url": cand["url"], "title": cand["title"], "text": body[:PAGE_CHARS]})
    return pages


def page_blocks(pages, fence_):
    """The pages as fenced data blocks; the address and title are inside, as they come from the
    discovery call."""
    return "\n\n".join(
        f'<data-{fence_} name="page" n="{i}">\nURL: {p["url"]}\nTitle: {p["title"]}\n\n{p["text"]}\n</data-{fence_}>'
        for i, p in enumerate(pages, 1)) or "(none)"


# --- Keeping the owner's position out of a WebSearch prompt ---------------------------------------

_NUMBER = re.compile(r"\d(?:[\d\s\u00a0\u202f.,']*\d)?")
_GAP = re.compile(r"[\s\u00a0\u202f']+")


def private_marks(conn, instrument_id, today):
    """(account names, position figures): what must never reach a prompt that has a tool. Every
    account's name, whether it holds this stock or not."""
    names = [r["name"] for r in conn.execute("SELECT name FROM broker_accounts") if r["name"].strip()]
    try:
        p = portfolio.position(conn, instrument_id, today)
    except portfolio.PortfolioError:
        p = None
    figures = []
    for key in ("qty", "avg_cost", "cost", "value", "cost_eur", "value_eur", "gain_eur"):
        if p and p.get(key) is not None:
            figures.append(abs(Decimal(p[key])))
    if p and p.get("unrealised_pct") is not None:
        figures.append(abs(Decimal(p["unrealised_pct"]) * 100))
    return names, [f for f in figures if f]


def _numbers(sentence):
    """The numbers written in a sentence, in any of the usual forms (1 234,50 · 1,234.50 · 37).
    A space may separate thousands or two numbers ("2025 37"): both readings are returned."""
    tokens = []
    for token in _NUMBER.findall(sentence):
        tokens.append(_GAP.sub("", token))
        if _GAP.search(token):
            tokens += _GAP.split(token)
    out = []
    for token in tokens:
        if "," in token and "." in token:
            token = token.replace(",", "") if token.rfind(".") > token.rfind(",") else token.replace(".", "").replace(",", ".")
        elif token.count(",") == 1 and len(token.split(",")[1]) != 3:
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
        try:
            out.append(Decimal(token.strip(".")))
        except InvalidOperation:
            continue
    return out


def _is_figure(number, figures):
    """The number is one of the figures, as written or rounded to 0-2 decimals."""
    for f in figures:
        if number == f or any(number == f.quantize(Decimal(1).scaleb(-places)) for places in (0, 1, 2)):
            return True
    return False


def scrub(text_, names, figures):
    """`text_` without the sentences that name an account or give a figure of the position. Our
    own earlier note is written by a model that saw the position; it is told never to repeat it,
    and this makes sure it does not go on to a search."""
    if not text_:
        return text_
    kept = []
    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", text_):
        low = sentence.casefold()
        if any(n.casefold() in low for n in names) or any(_is_figure(n, figures) for n in _numbers(sentence)):
            continue
        kept.append(sentence)
    return " ".join(s.strip() for s in kept if s.strip())


# --- Extraction --------------------------------------------------------------------------------------

EXTRACT_MODEL = "haiku"
EXTRACT_TIMEOUT = 300  # 25 documents; stock-oracle saw haiku pass 5 minutes on ~180 headlines
LOOKBACK = timedelta(days=7)
BATCH_CHARS = 60_000   # document text per call: each call quick and a failure cheap
BATCH_DOCS = 25        # the answer grows with the document count, not the input size
DOC_CHARS = 8_000      # the start of an article or filing carries what extraction needs
PER_DOC_OVERHEAD = 120
ANONYMOUS_NAME = "Company X"
ANONYMOUS_SYMBOL = "XXXX"
EVENT_TYPES = ("earnings", "guidance", "m_and_a", "product", "regulatory", "legal", "management", "insider",
               "analyst", "capital_return", "macro", "other")
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|n\.?v|s\.?a|ag|se|sa)\.?$",
    re.IGNORECASE)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"documents": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "document_id": {"type": "integer"},
            "relevance": {"type": "number", "minimum": 0, "maximum": 1},
            "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
            "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
            "novelty": {"type": "number", "minimum": 0, "maximum": 1},
            "key_facts": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "one_line_summary": {"type": "string", "maxLength": 400},
        },
        "required": ["document_id", "relevance", "sentiment", "event_type", "novelty", "key_facts", "one_line_summary"],
        "additionalProperties": False,
    }}},
    "required": ["documents"],
    "additionalProperties": False,
}


def name_variants(name):
    """The full name plus the name without legal suffixes ("NVIDIA Corporation" -> "NVIDIA")."""
    variants = {name.strip()}
    core = name.strip()
    if core.lower().startswith("the "):
        core = core[4:]
    while (stripped := _LEGAL_SUFFIX.sub("", core)) != core:
        core = stripped
    if len(core) >= 3:
        variants.add(core)
    return sorted((v for v in variants if v), key=len, reverse=True)


def anonymise(text_, name, symbol):
    """The company's name and ticker replaced by placeholders, where practical. A bare ticker only
    when 3+ characters and upper case, so "A" or "ON" do not mangle words; "$ON" is still caught."""
    for variant in name_variants(name):
        text_ = re.sub(rf"(?<!\w){re.escape(variant)}(?!\w)", ANONYMOUS_NAME, text_, flags=re.I)
    text_ = re.sub(rf"\${re.escape(symbol)}(?![\w.])", f"${ANONYMOUS_SYMBOL}", text_, flags=re.I)
    if len(symbol) >= 3:
        text_ = re.sub(rf"(?<![\w$]){re.escape(symbol)}(?![\w])", ANONYMOUS_SYMBOL, text_)
    return text_


def _named(text_, row):
    """The placeholders back to the company, for what the owner reads."""
    return text_.replace(ANONYMOUS_NAME, name_variants(row["name"])[-1]).replace(ANONYMOUS_SYMBOL, row["symbol"])


def pending_documents(conn, instrument_id, now):
    """Documents of the stock fetched in the last LOOKBACK with no signal for it under any prompt
    version: reruns score nothing twice, and a prompt change does not re-score the history."""
    return conn.execute(
        "SELECT d.* FROM market_docs d JOIN market_doc_instruments l ON l.document_id = d.id AND l.instrument_id = ? "
        "WHERE d.fetched_at > ? AND d.fetched_at <= ? "
        "AND NOT EXISTS (SELECT 1 FROM doc_signals s WHERE s.document_id = d.id AND s.instrument_id = ?) "
        "ORDER BY d.published_at IS NULL, d.published_at, d.id",
        (instrument_id, iso(now - LOOKBACK), iso(now), instrument_id)).fetchall()


def extraction_doc(doc, row):
    return {"document_id": doc["id"], "source": doc["source"], "published_at": doc["published_at"],
            "title": anonymise(doc["title"] or "", row["name"], row["symbol"]),
            "text": anonymise((doc["body"] or "")[:DOC_CHARS], row["name"], row["symbol"])}


def make_batches(docs, max_chars=BATCH_CHARS, max_docs=BATCH_DOCS):
    """Greedy split in order; a document bigger than the limit gets a batch of its own."""
    batches, current, size = [], [], 0
    for doc in docs:
        doc_size = len(doc["title"]) + len(doc["text"]) + PER_DOC_OVERHEAD
        if current and (size + doc_size > max_chars or len(current) >= max_docs):
            batches.append(current)
            current, size = [], 0
        current.append(doc)
        size += doc_size
    if current:
        batches.append(current)
    return batches


def extraction_prompt(docs, fence_=None):
    """-> (prompt, prompt_version)"""
    fence_ = fence_ or fence()
    prompt = load("extract")
    blocks = "\n\n".join(
        f'<data-{fence_} name="document" id="{d["document_id"]}" source="{d["source"]}" '
        f'published="{d["published_at"] or ""}">\n{d["title"]}\n\n{d["text"]}\n</data-{fence_}>' for d in docs)
    return prompt.render(fence=fence_, documents=blocks), prompt.version


def _score(value, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        return None
    return Decimal(str(round(value, 4)))


def check_signal(item, sent):
    """A doc_signals row's values from one answer item, or None when it is not for a document of
    the batch or a value is out of its range (it stays pending then)."""
    if not isinstance(item, dict):
        return None
    doc_id = item.get("document_id")
    if isinstance(doc_id, bool) or not isinstance(doc_id, int) or doc_id not in sent:
        return None
    rel, sent_, nov = (_score(item.get("relevance"), 0, 1), _score(item.get("sentiment"), -1, 1),
                       _score(item.get("novelty"), 0, 1))
    summary = text(item.get("one_line_summary"), 400)
    if None in (rel, sent_, nov, summary) or item.get("event_type") not in EVENT_TYPES:
        return None
    return {"document_id": doc_id, "relevance": rel, "sentiment": sent_, "novelty": nov,
            "event_type": item["event_type"], "summary": summary, "key_facts": texts(item.get("key_facts"), 8, 300)}


def extract_for(conn, row, now, *, run_id=None):
    """Score the stock's pending documents. {documents, signals, unknown_ids, missing_ids}.
    Raises like aicall.call; batches before a failure keep their signals."""
    docs = [extraction_doc(d, row) for d in pending_documents(conn, row["id"], now)]
    report = {"documents": len(docs), "signals": 0, "unknown_ids": [], "missing_ids": []}
    for batch in make_batches(docs):
        prompt, version = extraction_prompt(batch)
        answer, ai_run_id = aicall.call_logged(conn, "extract", prompt, EXTRACT_SCHEMA, model=EXTRACT_MODEL,
                                               prompt_version=version, instrument_id=row["id"], run_id=run_id,
                                               timeout=EXTRACT_TIMEOUT)
        sent = {d["document_id"] for d in batch}
        accepted = {}
        for item in answer.get("documents") or []:
            signal = check_signal(item, sent)
            if signal is None:
                if isinstance(item, dict) and item.get("document_id") not in sent:
                    report["unknown_ids"].append(item.get("document_id"))
                continue
            accepted.setdefault(signal["document_id"], signal)
        report["missing_ids"] += sorted(sent - accepted.keys())
        with tx(conn):
            for s in accepted.values():
                report["signals"] += conn.execute(
                    "INSERT OR IGNORE INTO doc_signals (document_id, instrument_id, ai_run_id, sentiment, event_type, "
                    "novelty, relevance, summary, key_facts_json, prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (s["document_id"], row["id"], ai_run_id, common.dtext(s["sentiment"]), s["event_type"],
                     common.dtext(s["novelty"]), common.dtext(s["relevance"]), _named(s["summary"], row),
                     json.dumps([_named(f, row) for f in s["key_facts"]], ensure_ascii=False), version)).rowcount
    if report["unknown_ids"]:
        log.warning("extract %s: dropped ids the model invented: %s", row["symbol"], report["unknown_ids"])
    return report


def extract_night(conn, run_id, now, deadline):
    """Night step "extract": every followed stock and ETF, holdings first, then the most pending
    documents. A stock with nothing pending costs no call."""
    now = clock(now)
    held = held_ids(conn)
    rows = night_instruments(conn, held)
    pending = {r["id"]: len(pending_documents(conn, r["id"], now())) for r in rows}
    rows.sort(key=lambda r: (r["id"] not in held, -pending[r["id"]], r["symbol"]))
    return run_night(rows, deadline, now, lambda row: extract_for(conn, row, now(), run_id=run_id))


# --- Research ----------------------------------------------------------------------------------------

RESEARCH_TIMEOUT = 600  # S5: 46 s for 13 k characters with thinking; up to 80 k here
FIRST_LOOKBACK = timedelta(days=30)
MAX_SIGNALS = 40
PREVIOUS_CHARS = 1_500

_CLAIM = {"type": "object", "properties": {"text": {"type": "string"}, "citation": {"type": "integer"}},
          "required": ["text", "citation"], "additionalProperties": False}
CATALYST = {"type": "object", "properties": {"event": {"type": "string"}, "date": {"type": ["string", "null"]}},
            "required": ["event", "date"], "additionalProperties": False}
_CITATION = {"type": "object",
             "properties": {"url": {"type": "string"}, "title": {"type": "string"}, "quote": {"type": "string"},
                            "published_at": {"type": ["string", "null"], "description": "YYYY-MM-DD"}},
             "required": ["url", "title", "quote", "published_at"], "additionalProperties": False}
_STRINGS = {"type": "array", "items": {"type": "string"}}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changes_since_last": {"type": "string"},
        "bull": {"type": "array", "items": _CLAIM},
        "bear": {"type": "array", "items": _CLAIM},
        "catalysts": {"type": "array", "items": CATALYST},
        "risks": _STRINGS,
        "what_would_change_my_mind": _STRINGS,
        "citations": {"type": "array", "items": _CITATION},
    },
    "required": ["summary", "changes_since_last", "bull", "bear", "catalysts", "risks", "what_would_change_my_mind",
                 "citations"],
    "additionalProperties": False,
}


def latest(conn, instrument_id):
    """The stock's latest analysis (deep or on demand)."""
    return conn.execute("SELECT * FROM analyses WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT 1",
                        (instrument_id,)).fetchone()


def claims_of(value):
    """Claims as [{text, citation}]; stock-oracle's imported rows call the text "claim"."""
    out = []
    for c in json.loads(value or "[]"):
        if isinstance(c, dict):
            out.append({"text": c.get("text", c.get("claim", "")), "citation": c.get("citation")})
        else:
            out.append({"text": str(c), "citation": None})
    return out


def previous_payload(row):
    return {
        "as_of": row["as_of"], "summary": row["summary"], "changes_since_last": row["what_changed"],
        "bull": [c["text"] for c in claims_of(row["bull_json"])], "bear": [c["text"] for c in claims_of(row["bear_json"])],
        "catalysts": json.loads(row["catalysts_json"]), "risks": json.loads(row["risks_json"]),
        "what_would_change_my_mind": json.loads(row["change_my_mind_json"]),
    }


def signals_since(conn, instrument_id, since, most):
    """The latest signal per document created after `since`, the most relevant first."""
    rows = conn.execute(
        "SELECT s.*, d.source, d.url, d.title, d.published_at FROM doc_signals s JOIN market_docs d ON d.id = s.document_id "
        "WHERE s.instrument_id = ? AND s.created_at > ? ORDER BY s.created_at DESC, s.id DESC", (instrument_id, since)).fetchall()
    latest_ = {}
    for r in rows:
        latest_.setdefault(r["document_id"], r)
    return sorted(latest_.values(), key=lambda r: (-common.dec(r["relevance"]), -r["document_id"]))[:most]


def market_payload(conn, instrument_id, today):
    return {"stats": market.price_stats(conn, instrument_id, today), "trend": market.trend_summary(conn, instrument_id, today)}


def position_payload(conn, instrument_id, today):
    """The position as the no-tools calls see it (never the account names). held None: the ledger
    could not be read."""
    try:
        p = portfolio.position(conn, instrument_id, today)
    except portfolio.PortfolioError:
        log.exception("position not read")
        return {"held": None}
    if p is None:
        return {"held": False}
    return {"held": True, "qty": p["qty"], "avg_cost": p["avg_cost"], "currency": p["currency"],
            "value_eur": p["value_eur"], "unrealised_pct": p["unrealised_pct"]}


def discovery_prompt(row, today, previous_summary, fence_=None):
    """-> (prompt, prompt_version). Public facts only: this call has WebSearch."""
    fence_ = fence_ or fence()
    prompt = load("discover")
    return prompt.render(fence=fence_, as_of=today.isoformat(), company=row["name"], symbol=row["symbol"],
                         exchange=row["exchange"], pages=MAX_PAGES, previous=previous_summary or "none"), prompt.version


def research_prompt(row, today, *, previous, market_, position, signals, pages, fence_=None):
    """-> (prompt, prompt_version)"""
    fence_ = fence_ or fence()
    prompt = load("research")
    return prompt.render(
        fence=fence_, company=row["name"], symbol=row["symbol"], as_of=today.isoformat(), previous=dumps(previous),
        market=dumps(market_), position=dumps(position), signals=json_lines(signals), page_count=len(pages),
        pages=page_blocks(pages, fence_)), prompt.version


def cross_check_citations(answer, allowed_urls):
    """(citations kept, bull, bear, dropped urls, dropped claims): a citation stays only when its
    URL, normalised, is allowed; claims are re-indexed, and those that lose their citation go."""
    allowed = {k for u in allowed_urls if (k := normalize_url(u))}
    new_index, kept, dropped_urls = {}, [], []
    for i, c in enumerate(answer.get("citations") or []):
        url = web_url(c.get("url")) if isinstance(c, dict) else None
        if url and normalize_url(url) in allowed:
            new_index[i] = len(kept)
            kept.append({"url": url, "title": text(c.get("title"), 300) or url, "quote": text(c.get("quote"), 600) or "",
                         "published_at": iso_date(c.get("published_at"))})
        else:
            dropped_urls.append(c.get("url") if isinstance(c, dict) else None)
    dropped_claims = 0

    def keep(claims):
        nonlocal dropped_claims
        out = []
        for c in claims if isinstance(claims, list) else []:
            index = c.get("citation") if isinstance(c, dict) else None
            words = text(c.get("text"), 1000) if isinstance(c, dict) else None
            if words and not isinstance(index, bool) and index in new_index:
                out.append({"text": words, "citation": new_index[index]})
            else:
                dropped_claims += 1
        return out

    bull, bear = keep(answer.get("bull")), keep(answer.get("bear"))
    return kept, bull, bear, dropped_urls, dropped_claims


def stored_urls(conn, urls):
    """The URLs among `urls` that are stored documents (any stock's)."""
    keys = {sha256(k): u for u in urls if isinstance(u, str) and (k := normalize_url(u))}
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    return [keys[r["url_hash"]] for r in conn.execute(f"SELECT url_hash FROM market_docs WHERE url_hash IN ({marks})", list(keys))]


def research(conn, instrument_id, today, *, kind="deep", run_id=None, progress=None):
    """Discover, fetch, write; the checked note stored in `analyses`. Returns its id. Raises
    runner.AIUnavailable, or RuntimeError when the note could not be written. `progress(text)`
    gets the Estonian steps for the Uuri kohe stream."""
    row = common.instrument(conn, instrument_id)
    model = config.settings(conn)["research_model"]
    prev = latest(conn, instrument_id)

    say(progress, "Otsin allikaid…")
    names, figures = private_marks(conn, instrument_id, today)
    prompt, version = discovery_prompt(row, today, scrub((prev["summary"] or "")[:PREVIOUS_CHARS], names, figures) if prev else None)
    try:
        found = aicall.call(conn, "discover", prompt, DISCOVER_SCHEMA, model=model, prompt_version=version,
                            instrument_id=instrument_id, run_id=run_id, web_search=True, timeout=DISCOVER_TIMEOUT,
                            max_turns=DISCOVER_TURNS)
        cands = candidates(found)
    except runner.AIUnavailable:
        raise
    except RuntimeError as exc:
        # The stored documents still make a note; only the web's extra pages are missing.
        log.info("research %s: discovery failed (%s), writing from stored documents", row["symbol"], exc)
        cands = []
    pages = fetch_pages(conn, cands, progress)

    say(progress, "Kirjutan…")
    since = prev["created_at"] if prev else f"{(today - FIRST_LOOKBACK).isoformat()}T00:00:00Z"
    signals = [{"document_id": s["document_id"], "source": s["source"], "url": s["url"], "title": s["title"],
                "published_at": s["published_at"], "relevance": s["relevance"], "sentiment": s["sentiment"],
                "novelty": s["novelty"], "event_type": s["event_type"], "summary": s["summary"],
                "key_facts": json.loads(s["key_facts_json"])}
               for s in signals_since(conn, instrument_id, since, MAX_SIGNALS)]
    prompt, version = research_prompt(row, today, previous=previous_payload(prev) if prev else None,
                                      market_=market_payload(conn, instrument_id, today),
                                      position=position_payload(conn, instrument_id, today), signals=signals, pages=pages)
    answer, ai_run_id = aicall.call_logged(conn, "research" if kind == "deep" else "deep_dive", prompt, RESEARCH_SCHEMA,
                                           model=model, prompt_version=version, instrument_id=instrument_id,
                                           run_id=run_id, timeout=RESEARCH_TIMEOUT, thinking=True)
    summary = text(answer.get("summary"), 3000)
    if summary is None:
        raise RuntimeError("AI vastus on vigane (kokkuvõte puudub)")
    cited = [c.get("url") for c in answer.get("citations") or [] if isinstance(c, dict)]
    allowed = [p["url"] for p in pages] + [s["url"] for s in signals if s["url"]] + stored_urls(conn, cited)
    citations, bull, bear, dropped_urls, dropped_claims = cross_check_citations(answer, allowed)
    if dropped_urls:
        log.warning("research %s: dropped %d citation(s) neither fetched nor stored, and %d claim(s): %s",
                    row["symbol"], len(dropped_urls), dropped_claims, dropped_urls)
    return conn.execute(
        "INSERT INTO analyses (instrument_id, ai_run_id, kind, as_of, summary, what_changed, bull_json, bear_json, "
        "catalysts_json, risks_json, change_my_mind_json, citations_json, prompt_version, model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (instrument_id, ai_run_id, kind, today.isoformat(), summary, text(answer.get("changes_since_last"), 2000),
         *(json.dumps(v, ensure_ascii=False) for v in (
             bull, bear, catalysts(answer.get("catalysts")), texts(answer.get("risks"), 10, 500),
             texts(answer.get("what_would_change_my_mind"), 10, 500), citations)),
         version, model)).lastrowid


def is_deep_day(conn, today):
    """`today` is the night's run date: a Monday run is Sunday night."""
    return config.WEEKDAYS[today.weekday()] in config.settings(conn)["deep_days"]


def deep_night(conn, run_id, today, now, deadline):
    """Night step "research", on the deep-research days only: every followed stock (not ETFs),
    holdings first. A stock with a deep note for `today` is not researched again."""
    if not is_deep_day(conn, today):
        return {"done": [], "skipped": [], "failed": [], "stopped": None}
    held = held_ids(conn)
    rows = sorted((r for r in night_instruments(conn, held) if r["kind"] == "stock"),
                  key=lambda r: (r["id"] not in held, r["symbol"]))

    def done(row):
        return conn.execute("SELECT 1 FROM analyses WHERE instrument_id = ? AND as_of = ? AND kind = 'deep'",
                            (row["id"], today.isoformat())).fetchone() is not None

    return run_night(rows, deadline, now, lambda row: research(conn, row["id"], today, run_id=run_id), done_already=done)
