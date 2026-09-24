"""Seaded → Ühendused (one card per outside account), the first-run wizard /tere and its
onboarding state.

Cards that only hold a secret (claude, brain, ntfy) are saved here through the vault. The others
call their owner's helpers (docs/CONTRACTS.md, "Connection helpers"), imported when needed: while
the owning package is not merged its card says "Pole veel saadaval".

The browser never receives a secret: only masked forms, and for ICS feeds only the host (a
private calendar URL is itself a credential).

Owner: WP-E2 (Postkast + Ühendused)."""

import importlib
import json
import os
import re
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, redirect, render_template, request

from .. import connections, vault
from ..db import get_db, tx
from ..security import rate_limit

bp = Blueprint("connections", __name__)

TEST_PAGES = os.environ.get("MARKMIK_TEST_PAGES") == "1"

# Every card of overview §15, in wizard order. The registry's title and order win once the owning
# package has registered its connection.
CARDS = {
    "claude": ("Claude", 10),
    "ekool": ("eKool", 20),
    "google": ("Google'i kontod", 30),
    "brain": ("Brain repo", 40),
    "ntfy": ("ntfy teavitused", 50),
    "ics": ("Kalendrivood (ICS)", 60),
    "phone": ("Telefoni kalender", 70),
}
STEPS = tuple(CARDS)

# Vault names each card owns (Katkesta deletes them). Google accounts are 'google_account:<email>'.
SECRETS = {
    "claude": ("claude_token",),
    "ekool": ("ekool_username", "ekool_password", "ekool_tokens"),
    "google": ("google_client", "google_auth_pending"),
    "brain": ("brain_token",),
    "ntfy": ("ntfy",),
}
GOOGLE_ACCOUNT = "google_account:"

# Card -> (owner module, helpers the card calls).
HELPERS = {
    "ekool": ("app.sources.ekool", ("save_credentials",)),
    "google": ("app.sources.gmail", ("save_client", "auth_url", "finish_auth", "accounts", "remove_account")),
    "ics": ("app.sources.icsfeed", ("feeds", "add_feed", "remove_feed")),
    "phone": ("app.ics", ("feed_url",)),
}

# Card -> the sources kind its Värskenda runs through sources.base.run_now.
REFRESH = {"ekool": "ekool", "google": "gmail", "ics": "ics"}

DEFAULT_REMOTE = "https://github.com/Tigetiiger/brain.git"
DEFAULT_NTFY = "http://100.122.97.7:8090"
TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")
SENDER = re.compile(r"(?:[^@\s]+@)?[a-z0-9-]+(?:\.[a-z0-9-]+)+")
MAX_CLIENT_JSON = 64 * 1024
MASK = "••••"


def _bad(message, status=400):
    return jsonify(error=message), status


def _unavailable():
    return _bad("Pole veel saadaval.", 503)


def _import(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None


def _owner(key):
    """The module behind a card, or None while it (or a helper the card needs) is not built."""
    name, needs = HELPERS[key]
    module = _import(name)
    return module if module and all(hasattr(module, f) for f in needs) else None


def _run_now():
    return getattr(_import("app.sources.base"), "run_now", None)


def _health(conn, kind):
    """Each source's last run as the worker recorded it."""
    return [dict(r) for r in conn.execute(
        "SELECT name, enabled, last_run_at, last_ok_at, last_error FROM sources WHERE kind = ? ORDER BY name", (kind,))]


def _mask(value):
    """Like vault.masked(): the last four characters, and only of a long value."""
    if not value:
        return None
    return MASK + (value[-4:] if len(value) >= 12 else "")


def _setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _forget_result(conn, key):
    """After new credentials the last Testi result describes the old ones."""
    conn.execute("DELETE FROM settings WHERE key = ?", (f"connection:{key}",))


# --- Cards -----------------------------------------------------------------------------------

def _fill_claude(conn, card):
    card["masked"]["claude_token"] = vault.masked(conn, "claude_token")
    card["configured"] = card["masked"]["claude_token"] is not None
    card["data"]["from_env"] = card["configured"] and "claude_token" not in vault.names(conn, "claude_token")


def _fill_ekool(conn, card):
    card["available"] = _owner("ekool") is not None
    card["masked"]["ekool_username"] = vault.masked(conn, "ekool_username")
    # A password shows no characters at all.
    card["masked"]["ekool_password"] = MASK if vault.get(conn, "ekool_password") else None
    card["configured"] = card["masked"]["ekool_username"] is not None
    card["data"]["sources"] = _health(conn, "ekool")


def _fill_google(conn, card):
    gmail = _owner("google")
    card["available"] = gmail is not None
    has_client = vault.get(conn, "google_client") is not None
    card["masked"]["google_client"] = MASK if has_client else None
    # Only these keys: whatever else the helper returns stays on the server.
    accounts = [{k: a.get(k) for k in ("email", "ok", "last_error", "last_ok_at")}
                for a in (gmail.accounts(conn) if gmail else [])]
    senders = _setting(conn, "gmail_senders")
    card["data"] = {"has_client": has_client, "accounts": accounts, "senders": json.loads(senders) if senders else None}
    card["configured"] = bool(accounts)


def _fill_brain(conn, card):
    card["masked"]["brain_token"] = vault.masked(conn, "brain_token")
    card["configured"] = card["masked"]["brain_token"] is not None
    card["data"]["remote"] = _setting(conn, "brain_remote") or DEFAULT_REMOTE


def _fill_ntfy(conn, card):
    cfg = vault.get_json(conn, "ntfy") or {}
    card["masked"]["ntfy"] = MASK if cfg else None
    card["configured"] = bool(cfg.get("topic"))
    card["data"] = {"url": cfg.get("url") or DEFAULT_NTFY, "topic": _mask(cfg.get("topic")), "token": _mask(cfg.get("token"))}


def _fill_ics(conn, card):
    icsfeed = _owner("ics")
    card["available"] = icsfeed is not None
    card["data"]["feeds"] = [
        {"id": f.get("id"), "name": f.get("name"), "host": urlsplit(f.get("url") or "").hostname,
         "last_error": f.get("last_error"), "last_ok_at": f.get("last_ok_at")}
        for f in (icsfeed.feeds(conn) if icsfeed else [])
    ]
    card["configured"] = bool(card["data"]["feeds"])


def _fill_phone(conn, card):
    ics = _owner("phone")
    card["available"] = card["configured"] = ics is not None
    # Shown in full on purpose: the owner copies it into the phone's calendar app.
    card["data"]["url"] = request.host_url.rstrip("/") + ics.feed_url(conn) if ics else None


FILL = {"claude": _fill_claude, "ekool": _fill_ekool, "google": _fill_google, "brain": _fill_brain,
        "ntfy": _fill_ntfy, "ics": _fill_ics, "phone": _fill_phone}


def card_title(key):
    reg = connections.get(key)
    return reg.title if reg else CARDS.get(key, (key,))[0]


def card(conn, key):
    """{key, title, order, ok, summary, error, at, testable, refreshable, available, configured,
    masked, data}"""
    reg = connections.get(key)
    title, order = (reg.title, reg.order) if reg else CARDS.get(key, (key, 100))
    last = connections.last_result(conn, key) or {}
    c = {"key": key, "title": title, "order": order, "ok": last.get("ok"), "summary": last.get("summary"),
         "error": last.get("error"), "at": last.get("at"), "testable": reg is not None,
         "refreshable": key in REFRESH and _run_now() is not None,
         "available": True, "configured": False, "masked": {}, "data": {}}
    if key in FILL:
        FILL[key](conn, c)
    return c


def all_cards(conn):
    keys = dict.fromkeys([*CARDS, *(c.key for c in connections.all_connections())])
    return sorted((card(conn, k) for k in keys), key=lambda c: c["order"])


# --- Saving a card ---------------------------------------------------------------------------

def _text(body, name, max_len=500):
    value = body.get(name)
    value = value.strip() if isinstance(value, str) else ""
    if len(value) > max_len:
        raise ValueError("Sisestatud väärtus on liiga pikk.")
    return value


def _token(value, missing):
    if not value:
        raise ValueError(missing)
    if re.search(r"\s", value) or len(value) < 8:
        raise ValueError("See ei näe välja nagu token; kopeeri see uuesti tervikuna.")
    return value


def _url(value, schemes, message, *, query=False):
    """A URL with one of `schemes`, a host and no user:password@ part (tokens have own fields).
    Without `query` also no ?query or #fragment: ntfy takes ?auth=<token>, and such a URL is not
    masked when shown."""
    try:
        parts = urlsplit(value)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        raise ValueError(message) from None
    if parts.scheme not in schemes or not parts.hostname or parts.username or parts.password:
        raise ValueError(message)
    if not query and (parts.query or parts.fragment or value.rstrip().endswith(("?", "#"))):
        raise ValueError(message)
    return value


def _save_claude(conn, body):
    vault.set(conn, "claude_token", _token(_text(body, "token"), "Kleebi token."))


def _save_ekool(conn, body):
    username = _text(body, "username", 200)
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    if not username or not password:
        raise ValueError("Sisesta eKooli kasutajanimi ja parool.")
    if len(password) > 500:
        raise ValueError("Sisestatud väärtus on liiga pikk.")
    _owner("ekool").save_credentials(conn, username, password)


def _save_brain(conn, body):
    remote = _url(_text(body, "remote") or DEFAULT_REMOTE, ("https",),
                  "Repo aadress peab olema kujul https://github.com/omanik/repo.git (ilma tokenita).")
    token = _text(body, "token")
    if token or vault.get(conn, "brain_token") is None:
        vault.set(conn, "brain_token", _token(token, "Kleebi GitHubi token."))
    conn.execute("INSERT INTO settings (key, value) VALUES ('brain_remote', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (remote,))


def _save_ntfy(conn, body):
    old = vault.get_json(conn, "ntfy") or {}
    url = _url(_text(body, "url") or DEFAULT_NTFY, ("http", "https"),
               "Serveri aadress peab algama http:// või https:// (ilma ?-osa ja tokenita).")
    # Empty topic or token fields keep what is stored: the browser never had them to prefill.
    topic = _text(body, "topic") or old.get("topic")
    if not topic:
        raise ValueError("Teema on puudu.")
    if not TOPIC.fullmatch(topic):
        raise ValueError("Teema nimes võivad olla ainult tähed a–z, numbrid, - ja _ (kuni 64 märki).")
    token = _text(body, "token")
    token = _token(token, "") if token else old.get("token")
    vault.set_json(conn, "ntfy", {"url": url.rstrip("/"), "topic": topic, "token": token or None})


def _save_ics(conn, body):
    url = _text(body, "url", 2000)
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    _url(url, ("https",), "Kalendri aadress peab algama https:// või webcal://.", query=True)
    name = _text(body, "name", 80) or urlsplit(url).hostname
    _owner("ics").add_feed(conn, url, name)


def _save_google(conn, body):
    """The Gmail prefilter (setting gmail_senders): sender domains or addresses, one per line.
    Empty removes the setting, so the owner's default list applies again."""
    raw = body.get("senders")
    senders = [s.strip().lower() for s in raw.splitlines() if s.strip()] if isinstance(raw, str) else []
    if len(senders) > 50 or not all(len(s) <= 200 and SENDER.fullmatch(s) for s in senders):
        raise ValueError("Kirjuta iga saatja eraldi reale: domeen (gag.ee) või aadress (nimi@gag.ee).")
    if senders:
        conn.execute("INSERT INTO settings (key, value) VALUES ('gmail_senders', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(senders),))
    else:
        conn.execute("DELETE FROM settings WHERE key = 'gmail_senders'")


SAVE = {"claude": _save_claude, "ekool": _save_ekool, "google": _save_google, "brain": _save_brain,
        "ntfy": _save_ntfy, "ics": _save_ics}


# --- Pages and API ---------------------------------------------------------------------------

@bp.get("/seaded/uhendused")
def page():
    return render_template("connections.html")


@bp.get("/api/connections")
def list_cards():
    return jsonify(all_cards(get_db()))


@bp.post("/api/connections/<key>")
def save(key):
    if key not in SAVE:
        return _bad("Seda ühendust ei salvestata nii.", 404)
    if key in HELPERS and _owner(key) is None:
        return _unavailable()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _bad("Vigane päring.")
    conn = get_db()
    # No transaction around the owners' helpers: they keep their own, and add_feed resolves DNS,
    # which must not happen while holding the database's write lock.
    try:
        SAVE[key](conn, body)
    except ValueError as exc:
        return _bad(str(exc))
    _forget_result(conn, key)
    return jsonify(card(conn, key))


@bp.post("/api/connections/<key>/test")
@rate_limit("connection-test", 10, 60)
def test(key):
    if connections.get(key) is None:
        return _unavailable()
    return jsonify(connections.run_test(get_db(), key))


@bp.post("/api/connections/<key>/refresh")
@rate_limit("sources-refresh", 5, 300)
def refresh(key):
    """Värskenda: run that card's sources now. -> {"ok", "items", "error"}"""
    if key not in REFRESH:
        return _bad("Seda ühendust ei saa värskendada.", 404)
    run_now = _run_now()
    if run_now is None:
        return _unavailable()
    kind = REFRESH[key]
    return jsonify(run_now(get_db(), kind).get(kind) or {"ok": False, "items": 0, "error": "Pole ühendatud."})


@bp.delete("/api/connections/<key>")
def disconnect(key):
    reg = connections.get(key)
    if key not in CARDS and reg is None:
        return _bad("Tundmatu ühendus.", 404)
    conn = get_db()
    if key == "google" and (gmail := _owner("google")):
        # Through the owner, so the tokens are revoked at Google and its sync state goes too.
        for account in gmail.accounts(conn):
            gmail.remove_account(conn, account["email"])
    names = {*SECRETS.get(key, ()), *(reg.secret_names if reg else ())}
    if key == "google":
        names.update(vault.names(conn, GOOGLE_ACCOUNT))
    with tx(conn):
        for name in names:
            vault.delete(conn, name)
        _forget_result(conn, key)
    return "", 204


@bp.post("/api/connections/google/client")
def google_client():
    gmail = _owner("google")
    if gmail is None:
        return _unavailable()
    upload = request.files.get("file")
    if upload is not None:
        raw = upload.read(MAX_CLIENT_JSON + 1)
    else:
        body = request.get_json(silent=True)
        text = body.get("client_json") if isinstance(body, dict) else None
        raw = text.encode() if isinstance(text, str) else b""
    if not raw.strip():
        return _bad("Vali JSON-fail või kleebi selle sisu.")
    if len(raw) > MAX_CLIENT_JSON:
        return _bad("Fail on liiga suur; see pole Google'i kliendi JSON.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _bad("Fail pole loetav tekst; vali allalaaditud JSON-fail.")
    conn = get_db()
    try:
        gmail.save_client(conn, text)
    except ValueError as exc:
        return _bad(str(exc))
    _forget_result(conn, "google")
    return jsonify(card(conn, "google"))


@bp.post("/api/connections/google/start")
def google_start():
    gmail = _owner("google")
    if gmail is None:
        return _unavailable()
    try:
        return jsonify(url=gmail.auth_url(get_db()))
    except ValueError as exc:
        return _bad(str(exc))


@bp.post("/api/connections/google/finish")
def google_finish():
    gmail = _owner("google")
    if gmail is None:
        return _unavailable()
    body = request.get_json(silent=True)
    pasted = body.get("pasted_url") if isinstance(body, dict) else None
    pasted = pasted.strip() if isinstance(pasted, str) else ""
    if not pasted:
        return _bad("Kleebi aadress, mis algab http://127.0.0.1.")
    if len(pasted) > 4000:
        return _bad("See aadress on liiga pikk.")
    conn = get_db()
    try:
        email = gmail.finish_auth(conn, pasted)
    except ValueError as exc:
        return _bad(str(exc))
    _forget_result(conn, "google")
    return jsonify(email=email)


@bp.delete("/api/connections/google/<email>")
def google_remove(email):
    gmail = _owner("google")
    if gmail is None:
        return _unavailable()
    conn = get_db()
    gmail.remove_account(conn, email)
    _forget_result(conn, "google")
    return "", 204


@bp.delete("/api/connections/ics/<int:source_id>")
def ics_remove(source_id):
    icsfeed = _owner("ics")
    if icsfeed is None:
        return _unavailable()
    conn = get_db()
    icsfeed.remove_feed(conn, source_id)
    _forget_result(conn, "ics")
    return "", 204


# --- First-run wizard ------------------------------------------------------------------------

def _onboarding(conn):
    status = {r["step"]: r["status"] for r in conn.execute("SELECT step, status FROM onboarding")}
    steps = [{"key": k, "title": card_title(k), "status": status.get(k)} for k in STEPS]
    return {"steps": steps, "remaining": sum(s["status"] is None for s in steps)}


@bp.get("/tere")
def wizard():
    return render_template("wizard.html")


@bp.get("/api/onboarding")
def onboarding():
    return jsonify(_onboarding(get_db()))


@bp.post("/api/onboarding/<step>")
def onboarding_step(step):
    if step not in STEPS:
        return _bad("Tundmatu samm.", 404)
    body = request.get_json(silent=True)
    status = body.get("status") if isinstance(body, dict) else None
    if status not in ("done", "skipped"):
        return _bad("Olek peab olema 'done' või 'skipped'.")
    conn = get_db()
    conn.execute("INSERT INTO onboarding (step, status) VALUES (?, ?) "
                 "ON CONFLICT (step) DO UPDATE SET status = excluded.status", (step, status))
    return jsonify(_onboarding(conn))


@bp.before_app_request
def first_run():
    """Until the owner has answered any wizard step, the start pages lead to the wizard. Only
    browser navigations to / and /tana: never the API, static files, login or /tere itself."""
    if request.method != "GET" or request.path not in ("/", "/tana") or not g.get("owner"):
        return None
    if not request.accept_mimetypes.accept_html:
        return None
    # Every package's browser tests start at / on a fresh database; only the wizard's own opt in.
    if TEST_PAGES and request.cookies.get("vt_wizard") != "1":
        return None
    if get_db().execute("SELECT 1 FROM onboarding LIMIT 1").fetchone() is None:
        return redirect("/tere")
    return None


if TEST_PAGES:
    import sys
    import types

    _real = {}  # what the fakes replaced: ("module" | "conn", name) -> original or None

    def _fake_modules():
        def save_client(conn, text):
            if '"installed"' not in text:
                raise ValueError("See pole Google'i „Desktop app“ kliendi JSON.")
            vault.set(conn, "google_client", text)

        def finish_auth(conn, pasted_url):
            if "code=" not in pasted_url or "state=vt" not in pasted_url:
                raise ValueError("Aadressist ei leitud Google'i koodi.")
            vault.set_json(conn, GOOGLE_ACCOUNT + "opilane@example.com", {"refresh_token": "vt"})
            return "opilane@example.com"

        def accounts(conn):
            return [{"email": n[len(GOOGLE_ACCOUNT):], "ok": True, "last_error": None, "last_ok_at": None}
                    for n in vault.names(conn, GOOGLE_ACCOUNT)]

        def add_feed(conn, url, name):
            return conn.execute("INSERT INTO sources (kind, name, config_json) VALUES ('ics', ?, json_object('url', ?))",
                                (name, url)).lastrowid

        def run_now(conn, kind=None):
            conn.execute("UPDATE sources SET last_run_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
                         "last_ok_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), last_error = NULL WHERE kind = ?", (kind,))
            return {kind: {"ok": True, "items": 2, "error": None}}

        def save_credentials(conn, username, password):
            vault.set(conn, "ekool_username", username)
            vault.set(conn, "ekool_password", password)
            conn.execute("INSERT OR IGNORE INTO sources (kind, name) VALUES ('ekool', 'eKool')")

        def feeds(conn):
            return [{"id": r["id"], "name": r["name"], "url": r["url"], "last_error": None, "last_ok_at": None}
                    for r in conn.execute("SELECT id, name, json_extract(config_json, '$.url') AS url FROM sources WHERE kind = 'ics'")]

        return {
            "app.sources.gmail": types.SimpleNamespace(
                save_client=save_client, auth_url=lambda conn: "/_test/inbox/google?state=vt",
                finish_auth=finish_auth, accounts=accounts,
                remove_account=lambda conn, email: vault.delete(conn, GOOGLE_ACCOUNT + email)),
            "app.sources.ekool": types.SimpleNamespace(save_credentials=save_credentials),
            "app.sources.base": types.SimpleNamespace(run_now=run_now),
            "app.sources.icsfeed": types.SimpleNamespace(
                feeds=feeds, add_feed=add_feed,
                remove_feed=lambda conn, source_id: conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))),
            "app.ics": types.SimpleNamespace(feed_url=lambda conn: "/ics/vt-privaatne-token.ics"),
        }

    FAKE_TESTS = {
        "ekool": {"ok": False, "summary": "", "error": "Vale kasutajanimi või parool (võlts)"},
        "google": {"ok": True, "summary": "opilane@example.com · 4 kirja (võlts)", "error": None},
        "brain": {"ok": True, "summary": "Viimane commit: abc1234 (võlts)", "error": None},
        "ntfy": {"ok": True, "summary": "Proovisõnum saadetud (võlts)", "error": None},
        "ics": {"ok": True, "summary": "12 sündmust (võlts)", "error": None},
        "phone": {"ok": True, "summary": "Aadress on olemas (võlts)", "error": None},
    }

    @bp.post("/_test/inbox/fakes")
    def install_fakes():
        """Browser tests: fake owner helpers and Testi results, and start from a blank state
        (no onboarding answers, no card secrets, no results, no sources)."""
        for name, module in _fake_modules().items():
            _real.setdefault(("module", name), sys.modules.get(name))
            sys.modules[name] = module
        for key, result in FAKE_TESTS.items():
            _real.setdefault(("conn", key), connections.get(key))
            connections.register(key, CARDS[key][0], CARDS[key][1], test=lambda conn, r=result: dict(r))
        conn = get_db()
        with tx(conn):
            conn.execute("DELETE FROM onboarding")
            conn.execute("DELETE FROM settings WHERE key LIKE 'connection:%' OR key IN ('brain_remote', 'gmail_senders')")
            conn.execute("DELETE FROM sources")
            for name in [n for names in SECRETS.values() for n in names] + vault.names(conn, GOOGLE_ACCOUNT):
                vault.delete(conn, name)
        return "", 204

    @bp.delete("/_test/inbox/fakes")
    def remove_fakes():
        for (kind, name), original in _real.items():
            if kind == "module":
                if original is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original
            elif original is None:
                connections._registry.pop(name, None)
            else:
                connections._registry[name] = original
        _real.clear()
        return "", 204

    @bp.get("/_test/inbox/google")
    def fake_consent():
        """Stands in for Google's consent page, which would redirect to http://127.0.0.1…"""
        return "<!DOCTYPE html><title>Google (võlts)</title><p>Google'i nõusolekuleht (võlts)</p>"
