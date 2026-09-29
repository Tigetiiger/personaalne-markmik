"""Seaded → Aktsiad (setting `stocks:settings`) and the Ühendused card "Turuandmed" (vault
`finnhub_key`, `alphavantage_key`, `sec_user_agent`). settings(conn) always holds every key:
what the owner saved over the defaults below."""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from .. import connections, vault
from .common import get_json, set_json

KEY = "stocks:settings"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODELS = ("sonnet", "opus")
TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-^=]{0,19}")

DEFAULTS = {
    # Weekdays of the night's run date that get deep research: mon, thu = Sunday and Wednesday nights.
    "deep_days": ["mon", "thu"],
    # Research, deep dives and Avasta. The Opus weekly limit is shared with the owner's own Claude Code.
    "research_model": "sonnet",
    "alert_move_pct": 5,
    "alert_attention_z": 3,
    # stock-oracle's topic, so the phone's subscription keeps working.
    "ntfy_topic": "stock-oracle",
    # Investment account income tax per year. Check against EMTA before relying on them.
    "tax_rates": {"2024": "0.20", "2025": "0.22", "2026": "0.22"},
    # Contribution balance carried in from history not entered here, EUR.
    "opening_balance": "0",
    # Company investor-relations RSS feeds: {"NVDA": ["https://..."]}.
    "ir_feeds": {},
    # Model calls one night may make; the day's AI_DAILY_CAP still applies on top.
    "night_cap": 60,
}

SECRETS = ("finnhub_key", "alphavantage_key", "sec_user_agent")


def settings(conn):
    saved = get_json(conn, KEY, {}) or {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


def tax_rates(conn):
    """{year: Decimal rate}"""
    return {int(y): Decimal(r) for y, r in settings(conn)["tax_rates"].items()}


def save_settings(conn, data):
    """Validate and store the fields present in `data`; ValueError with an Estonian message."""
    if not isinstance(data, dict):
        raise ValueError("Vigane päring.")
    new = settings(conn)
    for key, value in data.items():
        if key not in DEFAULTS:
            raise ValueError(f"Tundmatu seade: {key}.")
        new[key] = CHECKS[key](value)
    set_json(conn, KEY, new)
    return new


def _deep_days(v):
    if not isinstance(v, list) or any(d not in WEEKDAYS for d in v):
        raise ValueError("Süvauuringu päevad on vigased.")
    return [d for d in WEEKDAYS if d in v]


def _model(v):
    if v not in MODELS:
        raise ValueError("Tundmatu mudel.")
    return v


def _number(lo, hi, message, *, integer=False):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (integer and v != int(v)):
            raise ValueError(message)
        return int(v) if integer else v
    return check


def _topic(v):
    if not isinstance(v, str) or not TOPIC.fullmatch(v.strip()):
        raise ValueError("Teema nimes võivad olla ainult tähed a–z, numbrid, - ja _ (kuni 64 märki).")
    return v.strip()


def _decimal_text(v, lo, hi, message):
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(message) from None
    if not d.is_finite() or not lo <= d <= hi:
        raise ValueError(message)
    return str(d)


def _tax_rates(v):
    if not isinstance(v, dict) or len(v) > 50:
        raise ValueError("Maksumäärad on vigased.")
    out = {}
    for year, rate in v.items():
        if not re.fullmatch(r"20\d\d", str(year)):
            raise ValueError(f"Vigane aasta: {year}.")
        out[str(year)] = _decimal_text(rate, 0, 1, f"Aasta {year} määr peab olema 0 ja 1 vahel (nt 0.22).")
    return dict(sorted(out.items()))


def _opening_balance(v):
    return _decimal_text(v, 0, Decimal("1e12"), "Algsaldo peab olema null või positiivne summa.")


def _ir_feeds(v):
    if not isinstance(v, dict) or len(v) > 100:
        raise ValueError("Investorisuhete vood on vigased.")
    out = {}
    for symbol, urls in v.items():
        symbol = str(symbol).strip().upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f"Vigane sümbol: {symbol}.")
        if not isinstance(urls, list) or len(urls) > 5:
            raise ValueError(f"{symbol}: kuni 5 voogu.")
        for url in urls:
            parts = urlsplit(url) if isinstance(url, str) else None
            if parts is None or parts.scheme != "https" or not parts.hostname or parts.username or len(url) > 500:
                raise ValueError(f"{symbol}: voo aadress peab algama https://.")
        if urls:
            out[symbol] = list(urls)
    return out


CHECKS = {
    "deep_days": _deep_days,
    "research_model": _model,
    "alert_move_pct": _number(0.5, 50, "Liikumise piir peab olema 0,5–50 %."),
    "alert_attention_z": _number(1, 10, "Tähelepanu piir peab olema 1–10."),
    "ntfy_topic": _topic,
    "tax_rates": _tax_rates,
    "opening_balance": _opening_balance,
    "ir_feeds": _ir_feeds,
    "night_cap": _number(0, 300, "Öine piir peab olema 0–300 päringut.", integer=True),
}


# --- Ühendused: Turuandmed -----------------------------------------------------------------------

def secret(conn, name):
    """A market-data credential, or None (vault errors count as not set)."""
    try:
        return vault.get(conn, name) or None
    except vault.SecretsError:
        return None


def test_connection(conn):
    """One Yahoo chart call (no key), one Finnhub quote and one SEC call when those are set. Alpha
    Vantage is not called: its free tier has 25 calls a day."""
    parts, errors = [], []
    with httpx.Client(timeout=15, headers={"User-Agent": "Mozilla/5.0 (Markmik)"}) as c:
        r = c.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL", params={"range": "5d", "interval": "1d"})
        (parts if r.status_code == 200 else errors).append("Yahoo" if r.status_code == 200 else f"Yahoo vastas {r.status_code}")
        if key := secret(conn, "finnhub_key"):
            r = c.get("https://finnhub.io/api/v1/quote", params={"symbol": "AAPL"}, headers={"X-Finnhub-Token": key})
            ok = r.status_code == 200 and (r.json() or {}).get("c")
            (parts if ok else errors).append("Finnhub" if ok else f"Finnhub vastas {r.status_code}")
        if ua := secret(conn, "sec_user_agent"):
            r = c.get("https://data.sec.gov/submissions/CIK0000320193.json", headers={"User-Agent": ua})
            (parts if r.status_code == 200 else errors).append("SEC" if r.status_code == 200 else f"SEC vastas {r.status_code}")
    if secret(conn, "alphavantage_key"):
        parts.append("Alpha Vantage (salvestatud, ei testita)")
    return {"ok": not errors, "summary": ", ".join(parts) + (" töötavad" if parts else ""),
            "error": "; ".join(errors) or None}


connections.register("stocks", "Turuandmed", order=55, test=test_connection, secret_names=SECRETS)
