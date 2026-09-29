"""Stock alerts and the morning digest through ntfy (docs/AKTSIAD-PLAN.md, "Notifications"),
the rules ported from stock-oracle's export/alerts.py and digest.py.

Every alert has a dedupe key and is recorded in stock_notices before it is sent, so a rule can be
evaluated as often as we like (after the night, after each quote and filings poll) and each event
still goes out once. Rules look back LOOKBACK only, so the first run on an old database does not
replay history, and so a send that failed is retried for a bounded time: the next time its rule
fires, while it still looks back that far. A pass stops sending at its first failure (ntfy down
should not cost the worker ten timeouts in a row).

Sending uses reminders.publish with the vault's ntfy server and token, but the stocks topic
(Seaded → Aktsiad, default "stock-oracle", so the phone's old subscription keeps working).
Without ntfy set up an alert is recorded, not sent, and nothing fails.

The digest is built at the end of the night and stored as the unsent notice "digest:<date>";
the 07:00 job sends it (building it then if the night never got that far).
"""

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from .. import reminders
from ..util import TALLINN, iso, now_utc, parse_iso
from . import common, config, market, portfolio, sources

log = logging.getLogger("markmik.stocks.alerts")

LOOKBACK = timedelta(hours=36)
EIGHT_K = ("8-K", "8-K/A")
DIGEST_AT = time(7, 0)
EARNINGS_DAYS = 7
NOT_SET = "ntfy pole seadistatud"
STEP_LABEL = {"prices": "hinnad", "ingest": "uudised", "attention": "tähelepanu", "extract": "hindamine",
              "research": "uuringud", "verdict": "hinnangud", "notes": "märkmed", "digest": "kokkuvõte", "night": "öö"}
STATUS_WORD = {"succeeded": "õnnestus", "partial": "osaliselt", "failed": "ebaõnnestus", "skipped": "jäi vahele",
               "running": "käib", "missing": "puudub"}
VERDICT_WORD = {"buy": "Ostaks ▲", "hold": "Hoiaks ◆", "sell": "Müüks ▼"}
WHEN = {"bmo": "enne turu avanemist", "amc": "pärast turu sulgemist"}
NEW_YORK = market.NEW_YORK


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    title: str
    body: str
    link: str | None = None


# --- What is tracked and how it moved -------------------------------------------------------------

def held_ids(conn):
    try:
        return set(portfolio.holdings(conn))
    except Exception:
        # A ledger that does not add up is the Portfell tab's to report; alerts go on without it.
        log.warning("stock alerts: holdings unavailable", exc_info=True)
        return set()


def tracked(conn):
    """(instrument rows by symbol: the watchlist plus anything held, the held ids)"""
    held = held_ids(conn)
    rows = {r["id"]: r for r in common.followed(conn)}
    for iid in held - rows.keys():
        if (row := common.instrument(conn, iid)) is not None:
            rows[iid] = row
    return sorted(rows.values(), key=lambda r: (r["symbol"], r["id"])), held


def day_move(conn, instrument_id):
    """The latest day's move: {day, price, previous_close, change (fraction)} or None. The day of
    a quote is its New York date, of a bar its own."""
    last = market.latest_price(conn, instrument_id)
    if last is None or not last["previous_close"] or last["price"] is None:
        return None
    as_of = last["as_of"]
    day = parse_iso(as_of).astimezone(NEW_YORK).date() if "T" in as_of else date.fromisoformat(as_of)
    return {"day": day, "price": last["price"], "previous_close": last["previous_close"],
            "change": last["price"] / last["previous_close"] - 1}


def verdict_changes(conn, day):
    """Verdicts dated `day` that differ from the stock's previous one: [{instrument_id, symbol, name,
    verdict, conviction, previous}]."""
    rows = conn.execute(
        "SELECT v.instrument_id, v.verdict, v.conviction, i.symbol, i.name, "
        "(SELECT p.verdict FROM verdicts p WHERE p.instrument_id = v.instrument_id AND p.as_of < v.as_of "
        " ORDER BY p.as_of DESC LIMIT 1) AS previous "
        "FROM verdicts v JOIN instruments i ON i.id = v.instrument_id WHERE v.as_of = ? ORDER BY i.symbol",
        (day.isoformat(),)).fetchall()
    return [dict(r) for r in rows if r["previous"] and r["previous"] != r["verdict"]]


# --- Rules ---------------------------------------------------------------------------------------

def big_moves(conn, now):
    rows, held = tracked(conn)
    limit = Decimal(str(config.settings(conn)["alert_move_pct"])) / 100
    since = now.astimezone(TALLINN).date() - timedelta(days=4)
    out = []
    for row in rows:
        if row["id"] not in held:
            continue
        move = day_move(conn, row["id"])
        if move is None or move["day"] < since or abs(move["change"]) < limit:
            continue
        out.append(Alert(
            f"move:{row['symbol']}:{move['day'].isoformat()}", "move", f"{row['symbol']} {_pct(move['change'])}",
            f"{row['name']} liikus {_day(move['day'])} {_pct(move['change'])}, hind {_num(move['price'])} {row['currency']}.",
            _link(row["symbol"])))
    return out


def new_filings(conn, now):
    """New 8-Ks, and Form 4s with an insider purchase (transaction code P)."""
    rows, _ = tracked(conn)
    by_id = {r["id"]: r for r in rows}
    if not by_id:
        return []
    marks = ",".join("?" * len(by_id))
    since = iso(now - LOOKBACK)
    out = []
    for d in conn.execute(
            f"SELECT d.id, d.url, d.title, d.meta_json, l.instrument_id FROM market_docs d "
            f"JOIN market_doc_instruments l ON l.document_id = d.id WHERE d.source = 'edgar' AND d.fetched_at >= ? "
            f"AND l.instrument_id IN ({marks}) ORDER BY d.fetched_at, d.id", (since, *by_id)):
        meta = json.loads(d["meta_json"] or "{}")
        if meta.get("form") not in EIGHT_K:
            continue
        symbol = by_id[d["instrument_id"]]["symbol"]
        items = ", ".join(meta.get("items") or [])
        out.append(Alert(f"8k:{meta.get('accession') or d['id']}", "8k", f"{symbol}: uus {meta['form']}",
                         (d["title"] or "8-K aruanne") + (f" (punktid {items})" if items else ""),
                         _safe_url(d["url"]) or _link(symbol)))
    buys = defaultdict(list)
    for t in conn.execute(
            f"SELECT t.*, d.url, d.fetched_at FROM insider_trades t JOIN market_docs d ON d.id = t.document_id "
            f"WHERE t.code = 'P' AND d.fetched_at >= ? AND t.instrument_id IN ({marks}) ORDER BY d.fetched_at, t.id",
            (since, *by_id)):
        buys[t["document_id"]].append(t)
    for doc_id, trades in buys.items():
        first = trades[0]
        symbol = by_id[first["instrument_id"]]["symbol"]
        qty = sum(common.dec(t["qty"]) for t in trades)
        value = sum(common.dec(t["qty"]) * common.dec(t["price"]) for t in trades if t["price"])
        who = first["insider_name"] + (f" ({first['insider_role']})" if first["insider_role"] else "")
        body = f"{who} ostis {_num(qty, 0)} aktsiat" + (f" umbes {_num(value, 0)} $ eest" if value else "")
        out.append(Alert(f"insider:{doc_id}", "insider", f"{symbol}: siseringi ost",
                         f"{body} ({_day(date.fromisoformat(first['trade_date']))}).", _safe_url(first["url"]) or _link(symbol)))
    return out


def earnings_tomorrow(conn, now):
    rows, _ = tracked(conn)
    tomorrow = now.astimezone(TALLINN).date() + timedelta(days=1)
    out = []
    for row in rows:
        e = conn.execute("SELECT MAX(time_of_day) AS t FROM earnings WHERE instrument_id = ? AND date = ? HAVING COUNT(*) > 0",
                         (row["id"], tomorrow.isoformat())).fetchone()
        if e is None:
            continue
        when = f", {WHEN[e['t']]}" if e["t"] in WHEN else ""
        out.append(Alert(f"earnings:{row['symbol']}:{tomorrow.isoformat()}", "earnings", f"{row['symbol']}: tulemused homme",
                         f"{row['name']} avaldab tulemused {_day(tomorrow)}{when}.", _link(row["symbol"])))
    return out


def attention_spikes(conn, now):
    rows, _ = tracked(conn)
    limit = config.settings(conn)["alert_attention_z"]
    today = now.astimezone(TALLINN).date()
    out = []
    for row in rows:
        for day in (today - timedelta(days=1), today):
            z = sources.attention_z(conn, row["id"], day)
            if z is None or z < limit:
                continue
            out.append(Alert(f"attention:{row['symbol']}:{day.isoformat()}", "attention", f"{row['symbol']}: tähelepanu hüpe",
                             f"Mainimisi tavalisest palju rohkem: z = {_num(Decimal(str(round(z, 1))), 1)} ({_day(day)}).",
                             _link(row["symbol"])))
    return out


def new_analyses(conn, now):
    return [Alert(f"analysis:{r['id']}", "analysis", f"{r['symbol']}: uus süvaanalüüs", _first_sentence(r["summary"]),
                  _link(r["symbol"]))
            for r in conn.execute(
                "SELECT a.id, a.summary, i.symbol FROM analyses a JOIN instruments i ON i.id = a.instrument_id "
                "WHERE a.kind = 'deep' AND a.created_at >= ? ORDER BY i.symbol, a.id", (iso(now - LOOKBACK),))]


def event_alerts(conn, now):
    return [*new_filings(conn, now), *big_moves(conn, now), *earnings_tomorrow(conn, now),
            *attention_spikes(conn, now), *new_analyses(conn, now)]


def night_alert(conn, run_date, status, steps, now):
    """A night that failed, was partial, was skipped or never ran ("missing"): one alert per night,
    whichever check notices first. True when sent now."""
    lines = []
    if status == "missing":
        lines.append("Ööd pole kirjas: töötaja ei käinud.")
    for name, step in (steps or {}).items():
        if not isinstance(step, dict):
            continue
        detail = step.get("detail") or {}
        label = STEP_LABEL.get(name, name)
        if step.get("status") == "failed":
            lines.append(f"Ebaõnnestus: {label}.")
        elif name == "night":
            lines.append(detail.get("reason") or "")
        elif stopped := detail.get("stopped"):
            if f"Peatus: {stopped}." not in lines:
                lines.append(f"Peatus: {stopped}.")
        elif step.get("status") == "partial":
            lines.append(f"Osaliselt: {label}.")
    alert = Alert(f"night:{run_date.isoformat()}", "night", f"Aktsiate öö {_day(run_date)}: {STATUS_WORD.get(status, status)}",
                  "\n".join(line for line in lines if line) or "Vaata Allikate lehte.", _link(None, "allikad"))
    return _send_all(conn, [alert], now) == 1


# --- Sending -------------------------------------------------------------------------------------

def check(conn, now):
    """Evaluate every event rule and send what is new; returns how many were sent now."""
    sent = _send_all(conn, event_alerts(conn, now), now)
    return sent + _retry_digest(conn, now)


def ntfy(conn):
    """The vault's ntfy server and token with the stocks topic, or None when ntfy is not set up."""
    cfg = reminders.ntfy_config(conn)
    return None if cfg is None else {**cfg, "topic": config.settings(conn)["ntfy_topic"]}


def _send_all(conn, due, now):
    cfg = ntfy(conn)
    sent = 0
    for alert in due:
        result = _deliver(conn, cfg, alert, now)
        if result is None:
            break
        sent += result
    return sent


def _deliver(conn, cfg, alert, now):
    """Record the alert and send it unless it went out before. True when sent now, False when not
    (already sent, ntfy not set up), None when the send failed (recorded in `error`)."""
    conn.execute("INSERT OR IGNORE INTO stock_notices (dedupe_key, kind, title, body) VALUES (?, ?, ?, ?)",
                 (alert.key, alert.kind, alert.title, alert.body))
    if cfg is None:
        conn.execute("UPDATE stock_notices SET error = ? WHERE dedupe_key = ? AND sent_at IS NULL", (NOT_SET, alert.key))
        return False
    # Claimed by marking it sent first: the night's thread and a worker job may run the same rule at
    # once, and only the one whose UPDATE wins sends. An unsent row gets the fresh text.
    claimed = conn.execute("UPDATE stock_notices SET title = ?, body = ?, sent_at = ?, error = NULL "
                           "WHERE dedupe_key = ? AND sent_at IS NULL", (alert.title, alert.body, iso(now), alert.key)).rowcount
    if not claimed:
        return False
    try:
        reminders.publish(cfg, alert.title, alert.body + (f"\n{alert.link}" if alert.link else ""))
    except Exception as exc:
        log.warning("stock alert %s not sent: %s", alert.key, exc)
        conn.execute("UPDATE stock_notices SET sent_at = NULL, error = ? WHERE dedupe_key = ?", (str(exc)[:500], alert.key))
        return None
    return True


def _link(symbol, tab=None):
    """A link into the app for the notification. Only with PUBLIC_URL: the worker has no request to
    take a host from."""
    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not base:
        return None
    return f"{base}/aktsiad/{quote(symbol, safe='.-^=')}" if symbol else f"{base}/aktsiad" + (f"/{tab}" if tab else "")


def _safe_url(url):
    return url if isinstance(url, str) and url.startswith("https://") else None


# --- The digest ----------------------------------------------------------------------------------

def digest_key(day):
    return f"digest:{day.isoformat()}"


def night_window(day):
    """The digest covers from the previous 07:00 to this one, so the daytime polls land in the
    next morning's digest."""
    end = datetime.combine(day, DIGEST_AT, tzinfo=TALLINN).astimezone(timezone.utc)
    start = datetime.combine(day - timedelta(days=1), DIGEST_AT, tzinfo=TALLINN).astimezone(timezone.utc)
    return start, end


def build_digest(conn, day, *, status=None):
    """(title, body) of the morning digest for the night of `day`, from stored data only.
    `status`: the night's, when the run row does not have its final one yet."""
    run = conn.execute("SELECT * FROM stock_runs WHERE run_date = ? ORDER BY id DESC LIMIT 1", (day.isoformat(),)).fetchone()
    steps = json.loads(run["steps_json"]) if run else {}
    rows, held = tracked(conn)
    start, end = night_window(day)
    sections = [
        ("Öö", _night_lines(day, run, steps, status, conn)),
        ("Portfell", _holding_lines(conn, rows, held, day)),
        ("Uued analüüsid", [f"{r['symbol']}: {_clip(r['summary'])}" for r in conn.execute(
            "SELECT a.summary, i.symbol FROM analyses a JOIN instruments i ON i.id = a.instrument_id "
            "WHERE a.kind = 'deep' AND a.as_of = ? ORDER BY i.symbol, a.id", (day.isoformat(),))]),
        ("Hinnang muutus", [f"{v['symbol']}: {VERDICT_WORD[v['previous']]} → {VERDICT_WORD[v['verdict']]}"
                            for v in verdict_changes(conn, day)]),
        ("Tulemused", _earnings_lines(conn, rows, day)),
        ("Vahele jäi või ebaõnnestus", _problem_lines(conn, steps, start, end)),
        ("AI kasutus", _usage_lines(conn, start, end)),
    ]
    body = "\n\n".join(f"{heading}\n" + "\n".join(f"• {line}" for line in lines) for heading, lines in sections if lines)
    return f"Aktsiad {_day(day)}", body


def store_digest(conn, day, *, status=None):
    """Keep the digest as an unsent notice for the 07:00 job; one that already went out is left
    alone. -> {"sections": n}"""
    title, body = build_digest(conn, day, status=status)
    key = digest_key(day)
    conn.execute("INSERT INTO stock_notices (dedupe_key, kind, title, body) VALUES (?, 'digest', ?, ?) "
                 "ON CONFLICT (dedupe_key) DO UPDATE SET title = excluded.title, body = excluded.body "
                 "WHERE stock_notices.sent_at IS NULL", (key, title, body))
    return {"sections": body.count("\n\n") + 1 if body else 0}


def send_digest(conn, day):
    """The 07:00 job: the night's problems (a night that never ran included), then the digest,
    once. True when the digest was sent now."""
    now = now_utc()
    rows, _ = tracked(conn)
    stored = conn.execute("SELECT * FROM stock_notices WHERE dedupe_key = ?", (digest_key(day),)).fetchone()
    if not rows and stored is None:
        return False  # Aktsiad not in use: nothing to tell
    run = conn.execute("SELECT * FROM stock_runs WHERE run_date = ? ORDER BY id DESC LIMIT 1", (day.isoformat(),)).fetchone()
    if run is None:
        night_alert(conn, day, "missing", {}, now)
    elif run["status"] in ("partial", "failed", "skipped"):
        night_alert(conn, day, run["status"], json.loads(run["steps_json"] or "{}"), now)
    if stored is None:
        store_digest(conn, day)
        stored = conn.execute("SELECT * FROM stock_notices WHERE dedupe_key = ?", (digest_key(day),)).fetchone()
    return _send_all(conn, [Alert(stored["dedupe_key"], "digest", stored["title"], stored["body"], _link(None))], now) == 1


def _retry_digest(conn, now):
    """Today's digest whose 07:00 send failed goes again with the next check, the same day only."""
    local = now.astimezone(TALLINN)
    if local.time() < DIGEST_AT:
        return 0
    stored = conn.execute("SELECT * FROM stock_notices WHERE dedupe_key = ? AND sent_at IS NULL AND error IS NOT NULL",
                          (digest_key(local.date()),)).fetchone()
    if stored is None:
        return 0
    return _send_all(conn, [Alert(stored["dedupe_key"], "digest", stored["title"], stored["body"], _link(None))], now)


def _night_lines(day, run, steps, status, conn):
    if run is None:
        return ["Ööd pole kirjas."]
    status = status or run["status"]
    line = f"{STATUS_WORD.get(status, status).capitalize()}"
    if (steps.get("research") or {}).get("status") not in (None, "skipped"):
        line += " (süvauuringu öö)"
    stops = {s["detail"]["stopped"] for s in steps.values() if isinstance(s, dict) and isinstance(s.get("detail"), dict)
             and s["detail"].get("stopped")}
    if stops:
        line += f", peatus: {'; '.join(sorted(stops))}"
    out = [line]
    if failed := [STEP_LABEL.get(n, n) for n, s in steps.items() if isinstance(s, dict) and s.get("status") == "failed"]:
        out.append(f"Ebaõnnestunud sammud: {', '.join(failed)}")
    if (steps.get("night") or {}).get("status") == "skipped":
        out.append(steps["night"]["detail"]["reason"])
    return out


def _holding_lines(conn, rows, held, day):
    moves = []
    for row in rows:
        if row["id"] in held and (move := day_move(conn, row["id"])) and move["day"] >= day - timedelta(days=5):
            moves.append((-abs(move["change"]), row["symbol"], move, row["currency"]))
    return [f"{symbol} {_pct(m['change'])}, {_num(m['price'])} {ccy} ({_day(m['day'])})" for _, symbol, m, ccy in sorted(moves)]


def _earnings_lines(conn, rows, day):
    ids = {r["id"]: r["symbol"] for r in rows}
    if not ids:
        return []
    found = conn.execute(
        f"SELECT instrument_id, date, MAX(time_of_day) AS t FROM earnings WHERE instrument_id IN ({','.join('?' * len(ids))}) "
        f"AND date >= ? AND date <= ? GROUP BY instrument_id, date",
        (*ids, day.isoformat(), (day + timedelta(days=EARNINGS_DAYS)).isoformat())).fetchall()
    found = sorted(found, key=lambda r: (r["date"], ids[r["instrument_id"]]))
    return [f"{_day(date.fromisoformat(r['date']))} {ids[r['instrument_id']]}" + (f" ({WHEN[r['t']]})" if r["t"] in WHEN else "")
            for r in found]


def _problem_lines(conn, steps, start, end):
    out = []
    for name in ("extract", "research", "verdict", "notes"):
        detail = (steps.get(name) or {}).get("detail")
        if not isinstance(detail, dict):
            continue
        failed = detail.get("failed") or []
        # The AI package gives [{symbol, error}]; the notes step {symbol: error}.
        items = failed.items() if isinstance(failed, dict) else [(f.get("symbol"), f.get("error")) for f in failed]
        for symbol, error in sorted(items, key=lambda i: str(i[0])):
            out.append(f"{STEP_LABEL[name]}, {symbol}: {_clip(str(error or ''), 120)}")
    for r in conn.execute(
            "SELECT source, COUNT(*) AS n, MAX(error) AS error FROM stock_source_runs WHERE status = 'failed' "
            "AND started_at >= ? AND started_at < ? GROUP BY source ORDER BY source", (iso(start), iso(end))):
        times = f" ×{r['n']}" if r["n"] > 1 else ""
        out.append(f"allikas {r['source']} ebaõnnestus{times}" + (f": {_clip(r['error'], 120)}" if r["error"] else ""))
    return out


def _usage_lines(conn, start, end):
    return [f"{r['purpose']}: {r['n']} päringut" + (f", {r['bad']} ebaõnnestus" if r["bad"] else "")
            + f", {_num(Decimal(r['ms'] or 0) / 60000, 1)} min"
            for r in conn.execute(
                "SELECT purpose, COUNT(*) AS n, SUM(status != 'success') AS bad, SUM(duration_ms) AS ms FROM stock_ai_runs "
                "WHERE started_at >= ? AND started_at < ? GROUP BY purpose ORDER BY purpose", (iso(start), iso(end)))]


# --- Estonian text -------------------------------------------------------------------------------

def _num(value, digits=2):
    """1234.5 -> '1 234,50' (Estonian: space for thousands, comma for decimals)."""
    return f"{Decimal(value):,.{digits}f}".replace(",", " ").replace(".", ",")


def _pct(fraction):
    return f"{Decimal(fraction) * 100:+.1f} %".replace(".", ",")


def _day(day):
    return f"{day.day:02d}.{day.month:02d}"


def _clip(text, limit=200):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_sentence(text, limit=240):
    text = " ".join((text or "").split())
    end = text.find(". ")
    sentence = text[: end + 1] if 0 < end < limit else text
    return _clip(sentence, limit)
