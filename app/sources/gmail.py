"""Gmail: the OAuth paste flow and account helpers for the Ühendused card, and the sync adapter.

Built from docs/spikes/gmail.md, which comes from Google's documentation only: the endpoints and
their replies are UNVERIFIED against a real account until spike S4 runs. Tests mock every request
with the synthetic fixtures in tests/fixtures/gmail/.

Flow: a "Desktop app" OAuth client, authorization code with PKCE (S256) and a loopback redirect
http://127.0.0.1:<port>/ that nothing listens on. The user pastes the address the browser ends up
on, and finish_auth() swaps its code for tokens.

Settings (JSON values in the settings table):
  gmail_senders  allowed sender domains or addresses, default ["gag.ee", "ekool.eu"]
  gmail_label    a Gmail label id ("Label_…") whose mail is kept whoever sent it
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from email.header import decode_header, make_header
from email.utils import parseaddr
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .. import connections, vault
from ..db import tx
from .base import NeedsUser, NotConnected, PartialFetch, SourceError, html_to_text, safe_message, upsert_source

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
PENDING = "google_auth_pending"  # vault: {state, verifier, redirect_uri, created} of the open attempt
PENDING_TTL = 15 * 60
DEFAULT_SENDERS = ["gag.ee", "ekool.eu"]
SKIP_LABELS = {"SPAM", "TRASH", "DRAFT", "SENT", "CHATS", "CATEGORY_PROMOTIONS"}
TEXT_MAX = 20000
RESYNC_MAX = 500  # messages read on the first run or after the history expired
MAX_ATTEMPTS = 3  # runs in which a message may fail to load before it is given up

# Tests put an httpx.MockTransport here; None means the real network.
transport = None


class _NotFound(Exception):
    pass


def _http():
    return httpx.Client(timeout=30, transport=transport)


# --- Ühendused card -------------------------------------------------------------------------

def save_client(conn, client_json):
    """Store the "Desktop app" client JSON downloaded from Google Cloud. ValueError (Estonian)."""
    try:
        data = json.loads(client_json)
    except (TypeError, ValueError):
        raise ValueError("See pole JSON. Lae üles Google Cloudist alla laaditud kliendi JSON-fail.") from None
    if isinstance(data, dict) and "web" in data:
        raise ValueError("See on veebirakenduse klient. Loo Google Cloudis klient tüübiga „Desktop app“.")
    client = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("Kliendi JSON-is puudub „installed“ osa koos client_id ja client_secret'iga. "
                         "Loo klient tüübiga „Desktop app“.")
    vault.set_json(conn, "google_client", client)


def auth_url(conn):
    """The Google consent link for adding an account. The PKCE verifier and state stay in the vault."""
    client = _client(conn)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    # Only ever text: nothing listens there, the browser shows an error page the user copies.
    redirect_uri = f"http://127.0.0.1:{49152 + secrets.randbelow(16384)}/"
    vault.set_json(conn, PENDING, {"state": state, "verifier": verifier, "redirect_uri": redirect_uri,
                                   "created": time.time()})
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URI + "?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent",
    })


def finish_auth(conn, pasted_url):
    """Exchange the code in the pasted 127.0.0.1 address; store the tokens and enable the account.
    Returns the e-mail address. ValueError (Estonian) on anything wrong."""
    text = (pasted_url or "").strip()
    try:
        query = parse_qs(urlsplit(text if "://" in text else "http://" + text).query)
    except ValueError:  # "http://[" and the like
        raise ValueError("See pole aadress. Kopeeri kogu aadress brauseri aadressiribalt.") from None

    def param(name):
        return (query.get(name) or [None])[0]

    if param("error"):
        raise ValueError("Ligipääs keelati." if param("error") == "access_denied" else f"Google'i viga: {param('error')}.")
    pending, state = vault.get_json(conn, PENDING), param("state")
    if (not pending or not state or not hmac.compare_digest(state.encode(), pending["state"].encode())
            or time.time() - pending["created"] > PENDING_TTL):
        raise ValueError("See aadress ei kuulu praegusele ühendamisele. Alusta „Lisa Google'i konto“ uuesti.")
    vault.delete(conn, PENDING)  # one attempt, one use
    if not param("code"):
        raise ValueError("Aadressis pole koodi. Kopeeri kogu aadress brauseri aadressiribalt.")
    try:
        token, email = _exchange(_client(conn), pending, param("code"))
    except httpx.HTTPError:
        raise ValueError("Google'iga ei saanud ühendust. Alusta uuesti.") from None
    except (KeyError, TypeError, AttributeError):  # a 200 whose body is not what Google documents
        raise ValueError("Google'i vastus oli ootamatu. Alusta uuesti.") from None
    with tx(conn):
        vault.set_json(conn, f"google_account:{email}", {
            "email": email, "refresh_token": token["refresh_token"], "access_token": token["access_token"],
            "expires_at": time.time() + token["expires_in"]})
        upsert_source(conn, "gmail", email)
    return email


def _exchange(client, pending, code):
    with _http() as http:
        r = http.post(TOKEN_URI, data={
            "client_id": client["client_id"], "client_secret": client["client_secret"], "code": code,
            "code_verifier": pending["verifier"], "grant_type": "authorization_code",
            "redirect_uri": pending["redirect_uri"]})
        if r.status_code != 200:
            raise ValueError(f"Google ei andnud ligipääsu ({_oauth_error(r)}). Alusta uuesti.")
        token = _json(r)
        if not isinstance(token.get("access_token"), str):
            raise KeyError("access_token")
        try:
            token["expires_in"] = int(token.get("expires_in", 3600))
        except (TypeError, ValueError):
            raise KeyError("expires_in") from None
        if "gmail.readonly" not in str(token.get("scope", "")):
            raise ValueError("Kirjade lugemise luba jäi andmata. Alusta uuesti ja jäta see linnuke alles.")
        if not token.get("refresh_token"):
            raise ValueError("Google ei andnud pikaajalist luba. Alusta uuesti.")
        r = http.get(API + "/profile", headers={"Authorization": f"Bearer {token['access_token']}"})
        if r.status_code != 200:
            raise ValueError(f"Gmaili profiili ei saanud lugeda (HTTP {r.status_code}).")
        email = _json(r)["emailAddress"]
        if not isinstance(email, str) or "@" not in email:
            raise KeyError("emailAddress")
        return token, email


def accounts(conn):
    rows = conn.execute("SELECT name, enabled, last_error, last_ok_at FROM sources WHERE kind = 'gmail' ORDER BY name")
    return [{"email": r["name"], "last_error": r["last_error"], "last_ok_at": r["last_ok_at"],
             "ok": bool(r["enabled"]) and not r["last_error"] and vault.get(conn, f"google_account:{r['name']}") is not None}
            for r in rows]


def remove_account(conn, email):
    token = vault.get_json(conn, f"google_account:{email}")
    if token:
        try:
            with _http() as http:
                http.post(REVOKE_URI, data={"token": token["refresh_token"]})
        except httpx.HTTPError:
            pass  # the stored tokens go anyway; access can also be revoked in the Google account
    with tx(conn):
        vault.delete(conn, f"google_account:{email}")
        conn.execute("DELETE FROM settings WHERE key = 'gmail_retry:' || "
                     "(SELECT id FROM sources WHERE kind = 'gmail' AND name = ?)", (email,))
        conn.execute("DELETE FROM sources WHERE kind = 'gmail' AND name = ?", (email,))


def test_connection(conn):
    """Health of the accounts as the last syncs left it; no request to Google."""
    if vault.get(conn, "google_client") is None:
        return {"ok": False, "summary": "", "error": "OAuth-kliendi JSON on üles laadimata."}
    accts = accounts(conn)
    if not accts:
        return {"ok": False, "summary": "", "error": "Ühtegi Google'i kontot pole lisatud."}
    bad = [f"{a['email']}: {a['last_error'] or 'pole ühendatud'}" for a in accts if not a["ok"]]
    return {"ok": not bad, "summary": ", ".join(a["email"] for a in accts), "error": "; ".join(bad) or None}


# --- Sync -----------------------------------------------------------------------------------

class GmailAdapter:
    kind = "gmail"
    structured = False

    def fetch(self, conn, source_row, cursor):
        """New messages since the history cursor that pass the prefilter. The first run, and a
        cursor Gmail no longer keeps (404), start from the current historyId plus the last 14
        days of prefiltered mail instead of a full backfill.

        Only headers are read first; the body only of mail the prefilter keeps, and mail it drops
        is stored as 'skipped' (its id only), so no message is looked at twice. The cursor always
        moves on: a message that could not be read is kept in settings gmail_retry:<source id>
        and tried again in the next runs, up to MAX_ATTEMPTS, then stored as 'skipped' too."""
        email, retry_key = source_row["name"], f"gmail_retry:{source_row['id']}"
        retry = _setting(conn, retry_key, {})  # message id -> failed attempts
        with _http() as http:
            def get(path, **params):
                return _get(conn, email, http, path, params)

            try:
                ids, new_cursor = _history(get, cursor) if cursor else _recent(conn, get)
            except _NotFound:
                ids, new_cursor = _recent(conn, get)
            items, failed, pending, failure, streak = [], [], {}, None, 0
            for message_id in dict.fromkeys([*ids, *retry]):
                if conn.execute("SELECT 1 FROM source_items WHERE source_id = ? AND external_id = ?",
                                (source_row["id"], message_id)).fetchone():
                    continue
                if streak >= 3:  # Gmail is struggling: the rest waits for the next run, uncounted
                    pending[message_id] = retry.get(message_id, 0)
                    continue
                try:
                    if not keep(conn, parse_message(get(f"/messages/{message_id}", format="metadata",
                                                        metadataHeaders="From"))):
                        items.append({"external_id": message_id, "raw": {"id": message_id}, "status": "skipped"})
                        continue
                    raw = parse_message(get(f"/messages/{message_id}", format="full"))
                except _NotFound:
                    continue  # deleted since it was listed
                except NeedsUser:
                    raise
                except (SourceError, httpx.HTTPError) as exc:
                    failure, streak = failure or exc, streak + 1
                    failed.append(message_id)
                    continue
                streak = 0
                items.append({"external_id": message_id, "raw": raw})
        for message_id in failed:
            # A run that ended in a row of failures says more about Gmail than about the messages.
            attempts = retry.get(message_id, 0) + (0 if streak >= 3 else 1)
            if attempts >= MAX_ATTEMPTS:
                items.append({"external_id": message_id, "status": "skipped",
                              "raw": {"id": message_id, "unreadable": f"{attempts} katset ebaõnnestus"}})
            else:
                pending[message_id] = attempts
        _set_setting(conn, retry_key, pending)
        if failure is not None:
            raise PartialFetch(safe_message(failure), items, new_cursor)
        return items, new_cursor


ADAPTER = GmailAdapter()


def _history(get, cursor):
    ids, page = [], None
    while True:
        params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "maxResults": 500}
        if page:
            params["pageToken"] = page
        reply = get("/history", **params)
        ids += [added["message"]["id"] for record in reply.get("history", [])
                for added in record.get("messagesAdded", [])]
        page = reply.get("nextPageToken")
        if not page:
            return list(dict.fromkeys(ids)), reply["historyId"]


def _recent(conn, get):
    cursor = get("/profile")["historyId"]  # first: mail arriving meanwhile is in the next history
    # The prefilter as Gmail searches: allowed senders, and the chosen label.
    queries = []
    if senders := sorted(_senders(conn)):
        queries.append({"q": f"from:({' OR '.join(senders)}) newer_than:14d"})
    if (label := _setting(conn, "gmail_label", None)) is not None:
        queries.append({"q": "newer_than:14d", "labelIds": label})
    ids = []
    for query in queries:
        page = None
        while len(ids) < RESYNC_MAX:
            reply = get("/messages", **query, maxResults=100, **({"pageToken": page} if page else {}))
            ids += [m["id"] for m in reply.get("messages", [])]
            if not (page := reply.get("nextPageToken")):
                break
    return list(dict.fromkeys(ids))[:RESYNC_MAX], cursor


def _get(conn, email, http, path, params):
    for force in (False, True):  # a 401 gets one fresh access token
        r = http.get(API + path, params=params,
                     headers={"Authorization": f"Bearer {_access_token(conn, email, http, force)}"})
        if r.status_code != 401:
            break
    if r.status_code == 404:
        raise _NotFound
    if r.status_code != 200:
        raise SourceError(f"Gmail vastas veaga (HTTP {r.status_code}), proovin hiljem uuesti.")
    return r.json()


def _access_token(conn, email, http, force=False):
    token = vault.get_json(conn, f"google_account:{email}")
    client = vault.get_json(conn, "google_client")
    if token is None or client is None:
        raise NotConnected
    if not force and token.get("expires_at", 0) - 60 > time.time():
        return token["access_token"]
    r = http.post(TOKEN_URI, data={"client_id": client["client_id"], "client_secret": client["client_secret"],
                                   "grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
    if r.status_code in (400, 401):
        # invalid_grant: revoked, password changed, unused for 6 months, or a Testing-mode client.
        raise NeedsUser(f"Google'i luba ei kehti enam ({_oauth_error(r)}). Ühenda konto uuesti.")
    if r.status_code != 200:
        raise SourceError(f"Google'i loa uuendamine ebaõnnestus (HTTP {r.status_code}).")
    fresh = r.json()
    token.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)))
    token["refresh_token"] = fresh.get("refresh_token") or token["refresh_token"]
    vault.set_json(conn, f"google_account:{email}", token)
    return token["access_token"]


def _client(conn):
    client = vault.get_json(conn, "google_client")
    if client is None:
        raise ValueError("Lae enne üles Google'i OAuth-kliendi JSON.")
    return client


def _json(r):
    try:
        body = r.json()
    except ValueError:
        raise KeyError("json") from None
    if not isinstance(body, dict):
        raise KeyError("json")
    return body


def _oauth_error(r):
    try:
        return str(r.json().get("error") or f"HTTP {r.status_code}")
    except ValueError:
        return f"HTTP {r.status_code}"


# --- Messages -------------------------------------------------------------------------------

def parse_message(msg):
    """A messages.get?format=full reply -> the raw item app.extract reads."""
    headers = msg["payload"].get("headers", [])
    sender = _words(_header(headers, "From"))
    return {
        "id": msg["id"], "thread_id": msg.get("threadId"),
        "message_id": _header(headers, "Message-ID") or msg["id"],
        "date_header": _header(headers, "Date"), "internal_date": msg.get("internalDate"),
        "from": sender, "subject": _words(_header(headers, "Subject")),
        "text": message_text(msg["payload"])[:TEXT_MAX], "labels": msg.get("labelIds", []),
        "authenticated": _dmarc_pass(headers, _domain(sender)),
    }


def message_text(payload):
    """The first text/plain part, else the first text/html part as text. Attachments are skipped."""
    plain = html = None
    for part in _leaves(payload):
        if part["mimeType"] == "text/plain" and plain is None:
            plain = _decode(part)
        elif part["mimeType"] == "text/html" and html is None:
            html = _decode(part)
    text = plain if plain is not None else html_to_text(html or "")
    return text.replace("\r\n", "\n")


def keep(conn, raw):
    """Prefilter before any AI call: never spam, sent, drafts or promotions; otherwise an
    allow-listed sender or the chosen label."""
    labels = set(raw["labels"])
    if labels & SKIP_LABELS:
        return False
    label = _setting(conn, "gmail_label", None)
    return sender_allowed(conn, raw["from"]) or (label is not None and label in labels)


def sender_allowed(conn, sender):
    address = parseaddr(sender)[1].lower()
    allowed = _senders(conn)
    return bool(address) and (address in allowed or _domain(address) in allowed)


def _senders(conn):
    return {e for e in (entry.strip().lower().lstrip("*").lstrip("@") for entry in
                        _setting(conn, "gmail_senders", DEFAULT_SENDERS)) if e}


def _leaves(part):
    if part.get("parts"):
        for child in part["parts"]:
            yield from _leaves(child)
    elif not part.get("filename") and part.get("body", {}).get("data"):
        yield part


def _decode(part):
    data = part["body"]["data"]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    m = re.search(r'charset="?([\w.-]+)', _header(part.get("headers", []), "Content-Type") or "", re.I)
    try:
        return raw.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _dmarc_pass(headers, domain):
    # Only the topmost Authentication-Results counts: Gmail adds it; lower ones came with the mail.
    value = _header(headers, "Authentication-Results") or ""
    m = re.search(r"\bdmarc=pass\b[^;]*?\bheader\.from=([\w.-]+)", value, re.I)
    return bool(domain) and m is not None and m.group(1).lower() == domain


def _header(headers, name):
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), None)


def _words(value):
    """Decode RFC 2047 encoded words left in a header, as a precaution."""
    try:
        return str(make_header(decode_header(value or "")))
    except (ValueError, LookupError):
        return value or ""


def _domain(sender):
    return parseaddr(sender)[1].lower().rpartition("@")[2]


def _setting(conn, key, default):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def _set_setting(conn, key, value):
    if value:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


connections.register("google", "Google'i kontod", order=30, test=test_connection, secret_names=("google_client",))
