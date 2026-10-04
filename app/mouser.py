"""Mouser part details for notes' order tables (docs/ARVUTUS-PLAN.md): the Search API with the key
from the Ühendused card "Mouser", answers cached in `mouser_parts`.

Mouser's product pages refuse scripts (Akamai "Access denied"), so a link is only read for its
part number; nothing is ever fetched from the link itself. The key travels as a query parameter,
so it is masked in every error that could be stored or shown.
"""

import collections
import json
import re
import threading
import time
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import connections, vault
from .util import local_today, now_utc, parse_iso, iso

API = "https://api.mouser.com/api/v1/search/partnumber"
KEY = "mouser_key"
FRESH = timedelta(hours=1)  # ▶ refreshes prices and stock older than this
PER_MINUTE = 25  # Mouser allows 30 calls a minute and 1,000 a day
PER_DAY = 900
# www.mouser.ee/ProductDetail/…, and with a language in the path: www.mouser.ee/en/ProductDetail/….
MOUSER_LINK = re.compile(r"^https?://(?:[a-z]{2,3}\.)?mouser\.[a-z.]{2,12}/(?:[a-z]{2}(?:-[a-z]{2,4})?/)?ProductDetail/", re.I)
MOUSER_NR = re.compile(r"^\d{2,4}-[A-Za-z0-9][A-Za-z0-9.\-/+#]*$")  # same as sandbox/kernel.py

_minute = collections.deque()
_lock = threading.Lock()


def is_mouser(value):
    value = str(value or "").strip()
    return bool(MOUSER_LINK.match(value) or MOUSER_NR.match(value))


def search_term(value):
    """The part number in a Mouser link ('/ProductDetail/<maker>/<part>?qs=…' or
    '/ProductDetail/<Mouser nr>'), or the value itself when it is a Mouser number."""
    value = value.strip()
    if MOUSER_NR.match(value):
        return value
    path = urlparse(value).path.rstrip("/")
    return unquote(path.rsplit("/", 1)[-1])


def key(conn):
    try:
        return vault.get(conn, KEY) or None
    except vault.SecretsError:
        return None


def parts(conn, values, *, refresh=False, fetch_new=True):
    """{value: part | {"error"}} for the Mouser values. Cached answers are used; a value never looked
    up is fetched when fetch_new (else left out: the sandbox shows it as "otsimata"); with refresh,
    cached ones older than FRESH are fetched again (the old answer stays if that fails)."""
    out, todo = {}, []
    now = now_utc()
    for value in values:
        row = conn.execute("SELECT data_json, fetched_at FROM mouser_parts WHERE key = ?", (value,)).fetchone()
        if row:
            out[value] = json.loads(row["data_json"])
            if refresh and now - parse_iso(row["fetched_at"]) > FRESH:
                todo.append(value)
        elif fetch_new:
            todo.append(value)
    if not todo:
        return out
    api_key = key(conn)
    if not api_key:
        for value in todo:
            out.setdefault(value, {"error": "Mouseri võti puudub (Ühendused → Mouser)"})
        return out
    with httpx.Client(timeout=15) as client:
        for value in todo:
            try:
                part = lookup(client, api_key, value, conn)
            except LimitReached as exc:
                out.setdefault(value, {"error": str(exc)})
                continue
            except MouserError as exc:
                out.setdefault(value, {"error": str(exc)})
                continue
            out[value] = part
            conn.execute(
                "INSERT INTO mouser_parts (key, data_json, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET data_json = excluded.data_json, fetched_at = excluded.fetched_at",
                (value, json.dumps(part, ensure_ascii=False), iso(now_utc())))
    return out


class MouserError(RuntimeError):
    pass


class LimitReached(MouserError):
    pass


def _count_call(conn):
    """Keep under Mouser's limits: PER_MINUTE in this process, PER_DAY in the database."""
    with _lock:
        now = time.monotonic()
        while _minute and now - _minute[0] > 60:
            _minute.popleft()
        if len(_minute) >= PER_MINUTE:
            raise LimitReached("Mouseri päringute piir: proovi minuti pärast uuesti.")
        day_key = f"mouser:calls:{local_today().isoformat()}"
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (day_key,)).fetchone()
        used = int(row["value"]) if row else 0
        if used >= PER_DAY:
            raise LimitReached("Mouseri tänane päringute piir on täis.")
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (day_key, str(used + 1)))
        _minute.append(now)


def lookup(client, api_key, value, conn):
    """One part: search by its number, pick the result the link names."""
    _count_call(conn)
    term = search_term(value)
    try:
        r = client.post(API, params={"apiKey": api_key},
                        json={"SearchByPartRequest": {"mouserPartNumber": term, "partSearchOptions": "None"}})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MouserError(f"Mouser ei vastanud: {mask(str(exc), api_key)}") from None
    errors = data.get("Errors") or []
    if r.status_code != 200 or errors:
        message = errors[0].get("Message") if errors and isinstance(errors[0], dict) else f"HTTP {r.status_code}"
        raise MouserError(f"Mouser: {mask(str(message), api_key)}")
    found = (data.get("SearchResults") or {}).get("Parts") or []
    part = choose(found, value, term)
    if part is None:
        return {"error": "ei leitud" if not found else "mitu vastet: kasuta Mouseri numbrit"}
    return shape(part)


def choose(found, value, term):
    """The Mouser number itself, else the result whose page has the link's qs, else the only one
    with that manufacturer number."""
    want = term.casefold()
    for p in found:
        if str(p.get("MouserPartNumber", "")).casefold() == want:
            return p
    qs = parse_qs(urlparse(value).query).get("qs")
    if qs:
        for p in found:
            if parse_qs(urlparse(str(p.get("ProductDetailUrl", ""))).query).get("qs") == qs:
                return p
    same = [p for p in found if str(p.get("ManufacturerPartNumber", "")).casefold() == want]
    if len(same) == 1:
        return same[0]
    return found[0] if len(found) == 1 else None


def shape(p):
    """The part as the sandbox's mouser() reads it (sandbox/kernel.py)."""
    breaks = []
    currency = None
    for b in p.get("PriceBreaks") or []:
        price = price_value(b.get("Price"))
        if price is not None and b.get("Quantity") is not None:
            breaks.append([int(b["Quantity"]), price])
            currency = currency or b.get("Currency")
    return {"nr": p.get("MouserPartNumber"), "tootja_nr": p.get("ManufacturerPartNumber"),
            "tootja": p.get("Manufacturer"), "kirjeldus": p.get("Description"), "breaks": breaks,
            "laos": _int(p.get("AvailabilityInStock")) if p.get("AvailabilityInStock") not in (None, "") else _stock(p.get("Availability")),
            "min": _int(p.get("Min")) or 1, "kordne": _int(p.get("Mult")) or 1, "valuuta": currency,
            "url": p.get("ProductDetailUrl")}


def price_value(text):
    """'0,44 €', '€0.44', '$1,234.50', '1.234,50 €' -> float, else None."""
    digits = re.sub(r"[^\d.,]", "", str(text or ""))
    if not digits:
        return None
    if "," in digits and "." in digits:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        digits = digits.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in digits:
        digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def _int(value):
    try:
        return int(str(value).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _stock(text):
    m = re.match(r"\s*([\d\s.,]+)", str(text or ""))
    return _int(re.sub(r"[\s.,]", "", m.group(1))) if m else 0


def mask(text, api_key):
    return text.replace(api_key, "•••") if api_key else text


def test_connection(conn):
    api_key = key(conn)
    if not api_key:
        return {"ok": False, "summary": "", "error": "Võti puudub."}
    try:
        with httpx.Client(timeout=15) as client:
            part = lookup(client, api_key, "595-NE555P", conn)
    except MouserError as exc:
        return {"ok": False, "summary": "", "error": str(exc)}
    if part.get("error"):
        return {"ok": False, "summary": "", "error": f"Proovipäring: {part['error']}"}
    price = part["breaks"][0][1] if part["breaks"] else None
    return {"ok": True, "summary": f"Mouser vastas: {part['nr']}, {price} {part['valuuta'] or ''}".strip(), "error": None}


connections.register("mouser", "Mouser", order=57, test=test_connection, secret_names=(KEY,))
