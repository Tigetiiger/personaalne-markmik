"""Gmail (app/sources/gmail.py): client JSON, PKCE consent link, paste flow, refresh, history sync,
404 resync, MIME decoding and the prefilter. Every request goes to the GoogleFake in conftest."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import connections, vault
from app.sources import base, gmail

ACCOUNT = "opilane.naidis@gmail.com"
CLIENT_JSON = json.dumps({"installed": {
    "client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]}})
TOKEN = {"access_token": "ya29.first", "expires_in": 3599, "refresh_token": "1//refresh-first",
         "scope": gmail.SCOPE, "token_type": "Bearer"}


def form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# --- client and consent ---------------------------------------------------------------------

def test_save_client(conn):
    for bad in ("not json", "[]", json.dumps({"web": {"client_id": "x"}}), json.dumps({"installed": {"client_id": "x"}})):
        with pytest.raises(ValueError):
            gmail.save_client(conn, bad)
    gmail.save_client(conn, CLIENT_JSON)
    assert vault.get_json(conn, "google_client")["client_id"] == "123-abc.apps.googleusercontent.com"


def test_auth_url_needs_client(conn):
    with pytest.raises(ValueError, match="OAuth-kliendi"):
        gmail.auth_url(conn)


def test_auth_url_parameters(conn):
    gmail.save_client(conn, CLIENT_JSON)
    url = urlsplit(gmail.auth_url(conn))
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    pending = vault.get_json(conn, gmail.PENDING)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert params["client_id"] == "123-abc.apps.googleusercontent.com"
    assert params["response_type"] == "code"
    assert params["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert (params["access_type"], params["prompt"]) == ("offline", "consent")
    assert params["state"] == pending["state"]
    redirect = urlsplit(params["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == ("http", "127.0.0.1", "/")
    assert 49152 <= redirect.port <= 65535 and params["redirect_uri"] == pending["redirect_uri"]
    verifier = pending["verifier"]
    assert 43 <= len(verifier) <= 128
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert gmail.auth_url(conn) != gmail.auth_url(conn)  # fresh state every time


def pasted(conn, **params):
    pending = vault.get_json(conn, gmail.PENDING)
    query = {"state": pending["state"], "code": "4/0Acode", "scope": gmail.SCOPE, **params}
    return pending["redirect_uri"] + "?" + "&".join(f"{k}={v}" for k, v in query.items() if v is not None)


def test_finish_auth(conn, google):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    pending = vault.get_json(conn, gmail.PENDING)
    google.token_replies = [(200, TOKEN)]
    # Pasted without the scheme, as a browser's address bar may copy it.
    assert gmail.finish_auth(conn, "  " + pasted(conn).removeprefix("http://") + "\n") == ACCOUNT
    sent = form(google.requests[0])
    assert sent == {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test-secret",
                    "code": "4/0Acode", "code_verifier": pending["verifier"], "grant_type": "authorization_code",
                    "redirect_uri": pending["redirect_uri"]}
    assert google.requests[1].headers["Authorization"] == "Bearer ya29.first"
    stored = vault.get_json(conn, f"google_account:{ACCOUNT}")
    assert (stored["refresh_token"], stored["access_token"]) == ("1//refresh-first", "ya29.first")
    assert stored["expires_at"] > time.time() + 3000
    assert vault.get(conn, gmail.PENDING) is None
    assert gmail.accounts(conn) == [{"email": ACCOUNT, "ok": True, "last_error": None, "last_ok_at": None}]


@pytest.mark.parametrize("state", ["wrong", None])
def test_finish_auth_rejects_bad_state(conn, google, state):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    with pytest.raises(ValueError, match="ei kuulu praegusele"):
        gmail.finish_auth(conn, pasted(conn, state=state))
    assert google.requests == []  # no code exchange


def test_finish_auth_rejects_expired_attempt(conn, google):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    pending = vault.get_json(conn, gmail.PENDING)
    vault.set_json(conn, gmail.PENDING, {**pending, "created": time.time() - 16 * 60})
    with pytest.raises(ValueError):
        gmail.finish_auth(conn, pasted(conn))


def test_finish_auth_denied_and_missing_scope(conn, google):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    with pytest.raises(ValueError, match="Ligipääs keelati"):
        gmail.finish_auth(conn, "http://127.0.0.1:50000/?error=access_denied&state=x")
    gmail.auth_url(conn)
    google.token_replies = [(200, {**TOKEN, "scope": "openid"})]
    with pytest.raises(ValueError, match="lugemise luba"):
        gmail.finish_auth(conn, pasted(conn))
    gmail.auth_url(conn)
    google.token_replies = [(400, {"error": "invalid_grant"})]
    with pytest.raises(ValueError, match="invalid_grant"):
        gmail.finish_auth(conn, pasted(conn))
    assert vault.names(conn, "google_account:") == []


@pytest.mark.parametrize("token_reply, profile_ok", [
    ("not json", True),
    ({"scope": gmail.SCOPE, "refresh_token": "1//r"}, True),  # no access_token
    (TOKEN, False),                                           # profile without emailAddress
])
def test_finish_auth_rejects_odd_replies(conn, monkeypatch, token_reply, profile_ok):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)

    def google_says(request):
        if request.url.path == "/token":
            return httpx.Response(200, **({"text": token_reply} if isinstance(token_reply, str) else {"json": token_reply}))
        return httpx.Response(200, json={"emailAddress": ACCOUNT} if profile_ok else {"messagesTotal": 1})
    monkeypatch.setattr(gmail, "transport", httpx.MockTransport(google_says))
    with pytest.raises(ValueError, match="ootamatu"):
        gmail.finish_auth(conn, pasted(conn))
    assert vault.names(conn, "google_account:") == []


def test_finish_auth_bad_paste_and_expiry(conn, google):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    with pytest.raises(ValueError, match="See pole aadress"):
        gmail.finish_auth(conn, "http://[")
    google.token_replies = [(200, {**TOKEN, "expires_in": "soon"})]
    with pytest.raises(ValueError, match="ootamatu"):
        gmail.finish_auth(conn, pasted(conn))


def test_finish_auth_non_ascii_state(conn, google):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)
    with pytest.raises(ValueError, match="ei kuulu praegusele"):
        gmail.finish_auth(conn, pasted(conn, state="%C3%B5%C3%A4"))


def test_finish_auth_network_error(conn, monkeypatch):
    gmail.save_client(conn, CLIENT_JSON)
    gmail.auth_url(conn)

    def down(request):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(gmail, "transport", httpx.MockTransport(down))
    with pytest.raises(ValueError, match="ei saanud ühendust"):
        gmail.finish_auth(conn, pasted(conn))


def test_reconnect_keeps_one_account(conn, google, gmail_source):
    conn.execute("UPDATE sources SET enabled = 0, last_error = 'x' WHERE id = ?", (gmail_source,))
    gmail.auth_url(conn)
    google.token_replies = [(200, {**TOKEN, "refresh_token": "1//second"})]
    gmail.finish_auth(conn, pasted(conn))
    assert [a["ok"] for a in gmail.accounts(conn)] == [True]
    assert vault.get_json(conn, f"google_account:{ACCOUNT}")["refresh_token"] == "1//second"


# --- tokens ---------------------------------------------------------------------------------

def test_expired_token_is_refreshed(conn, google, gmail_source):
    token = vault.get_json(conn, f"google_account:{ACCOUNT}")
    vault.set_json(conn, f"google_account:{ACCOUNT}", {**token, "expires_at": time.time() + 30})
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    assert base.run_source(conn, gmail_source)["ok"]
    refresh = form(google.requests[0])
    assert refresh == {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test-secret",
                       "grant_type": "refresh_token", "refresh_token": "1//refresh"}
    stored = vault.get_json(conn, f"google_account:{ACCOUNT}")
    assert (stored["access_token"], stored["refresh_token"]) == ("ya29.fresh", "1//refresh")
    assert sum(r.url.path == "/token" for r in google.requests) == 1  # reused afterwards


def test_401_refreshes_once(conn, google, gmail_source):
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    google.unauthorized_once = True
    assert base.run_source(conn, gmail_source)["ok"]
    assert [r.url.path for r in google.requests[:3]] == [
        "/gmail/v1/users/me/history", "/token", "/gmail/v1/users/me/history"]


def test_revoked_token_needs_reconnect(conn, google, gmail_source):
    token = vault.get_json(conn, f"google_account:{ACCOUNT}")
    vault.set_json(conn, f"google_account:{ACCOUNT}", {**token, "expires_at": 0})
    google.token_replies = [(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})]
    result = base.run_source(conn, gmail_source)
    assert result["error"] == "Google'i luba ei kehti enam (invalid_grant). Ühenda konto uuesti."
    assert gmail.accounts(conn)[0]["ok"] is False
    assert conn.execute("SELECT enabled FROM sources WHERE id = ?", (gmail_source,)).fetchone()[0] == 0


def test_removed_token_is_not_connected(conn, google, gmail_source):
    vault.delete(conn, f"google_account:{ACCOUNT}")
    assert base.run_source(conn, gmail_source)["error"] == "Pole ühendatud."
    assert google.requests == []


# --- sync -----------------------------------------------------------------------------------

def stored(conn):
    return {r["external_id"]: json.loads(r["raw_json"]) for r in conn.execute(
        "SELECT external_id, raw_json FROM source_items WHERE status = 'new' ORDER BY id")}


def test_first_run_takes_cursor_and_prefiltered_recent_mail(conn, google, gmail_source):
    google.listed = ["1a1153c6ce09d619"]
    assert base.run_source(conn, gmail_source) == {"ok": True, "items": 1, "error": None}
    assert google.paths()[:2] == ["/profile", "/messages"]
    listing = google.requests[1].url.params
    assert (listing["q"], listing["maxResults"]) == ("from:(ekool.eu OR gag.ee) newer_than:14d", "100")
    assert conn.execute("SELECT cursor FROM sources").fetchone()[0] == "5820417"


def test_resync_pages_up_to_cap(conn, google, gmail_source):
    google.listed = [f"id{i}" for i in range(650)]
    assert base.run_source(conn, gmail_source)["ok"]
    listings = [r.url.params for r in google.requests if r.url.path.endswith("/messages")]
    assert [p.get("pageToken") for p in listings] == [None, "100", "200", "300", "400"]
    assert len(google.fetched("metadata")) == 500


def test_resync_label_mode(conn, google, gmail_source):
    conn.execute("INSERT INTO settings (key, value) VALUES ('gmail_senders', '[]')")
    conn.execute("INSERT INTO settings (key, value) VALUES ('gmail_label', ?)", (json.dumps("Label_4829135"),))
    google.listed = ["1a1153c6ce09d619"]
    assert base.run_source(conn, gmail_source)["items"] == 1
    [listing] = [r.url.params for r in google.requests if r.url.path.endswith("/messages")]
    assert (listing["q"], listing["labelIds"]) == ("newer_than:14d", "Label_4829135")


def test_history_sync(conn, google, gmail_source):
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    assert base.run_source(conn, gmail_source)["items"] == 3
    params = google.requests[0].url.params
    assert (params["startHistoryId"], params["historyTypes"]) == ("5820417", "messageAdded")
    assert list(stored(conn)) == ["1a1153c6ce09d619", "1a115bcfb081f13d", "1a1168f1c0046bde"]
    assert conn.execute("SELECT cursor FROM sources").fetchone()[0] == "5821004"
    assert base.run_source(conn, gmail_source)["items"] == 0  # the same ids again are ignored
    assert conn.execute("SELECT COUNT(*) FROM source_items").fetchone()[0] == 3


def test_history_paging(conn, google, gmail_source):
    first, second = google.history[0]["history"][:2], google.history[0]["history"][2:]
    google.history = [{"history": first, "nextPageToken": "1", "historyId": "5820800"},
                      {"history": second + first[:1], "historyId": "5821004"}]
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    assert base.run_source(conn, gmail_source)["items"] == 3
    assert google.requests[1].url.params["pageToken"] == "1"
    assert conn.execute("SELECT cursor FROM sources").fetchone()[0] == "5821004"


def test_expired_history_resyncs(conn, google, gmail_source):
    conn.execute("UPDATE sources SET cursor = '12' WHERE id = ?", (gmail_source,))
    google.history_status = 404
    google.listed = ["1a1168f1c0046bde", "gone"]  # "gone" was deleted after listing: skipped
    assert base.run_source(conn, gmail_source) == {"ok": True, "items": 1, "error": None}
    assert google.paths()[:3] == ["/history", "/profile", "/messages"]
    assert conn.execute("SELECT cursor, last_error FROM sources").fetchone()[:] == ("5820417", None)


def test_server_error_keeps_cursor(conn, gmail_source, monkeypatch):
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    monkeypatch.setattr(gmail, "transport", httpx.MockTransport(lambda request: httpx.Response(503)))
    result = base.run_source(conn, gmail_source)
    assert result["error"] == "Gmail vastas veaga (HTTP 503), proovin hiljem uuesti."
    assert conn.execute("SELECT cursor, enabled FROM sources").fetchone()[:] == ("5820417", 1)


# --- messages -------------------------------------------------------------------------------

def test_decode_multipart_mixed_with_attachment(load):
    raw = gmail.parse_message(load("gmail/message_1_multipart_mixed_attachment.json"))
    assert raw["text"].startswith("Head 11. klasside õpilased!\n\nGeograafia õppekäik Lõuna-Eestisse")
    assert "21.–22. oktoobril" in raw["text"] and "\r" not in raw["text"] and "<" not in raw["text"]
    assert (raw["from"], raw["subject"], raw["date_header"]) == (
        "Tiit Kallas <tiit.kallas@gag.ee>", "Geograafia õppekäik Lõuna-Eestisse", "Wed, 07 Oct 2026 10:20:44 +0300")
    assert raw["message_id"] == "<CAH7mQpX0v3kZrT8e1LwQn5yB2sJd4fG6hK9aPq3uVx1cR0tNzA@mail.gmail.com>"
    assert (raw["id"], raw["thread_id"], raw["internal_date"]) == ("1a1153c6ce09d619", "1a1153c6ce09d619", "1791357646317")
    assert raw["labels"] == ["UNREAD", "CATEGORY_PERSONAL", "INBOX", "Label_4829135"]
    assert raw["authenticated"] is True


def test_decode_html_only(load):
    raw = gmail.parse_message(load("gmail/message_2_ekool_html_only.json"))
    assert raw["text"] == (
        "Tere, Mari Näidis!\nÕpetaja Riina Kuusk lisas uue ülesande.\nAine: Keemia\n"
        "Ülesanne: Loe õpikust lk 45–48 ja vasta küsimustele 1–5\nTähtaeg: 12.10.2026\n\n"
        "Vaata ülesannet eKoolis\nSee on automaatne teavitus, palun ära vasta sellele kirjale.")
    assert raw["authenticated"] is True


def test_decode_multipart_alternative_prefers_plain(load):
    raw = gmail.parse_message(load("gmail/message_3_multipart_alternative.json"))
    assert raw["text"].startswith("Tere, 11.b!\n\nKahjuks jääb homme, neljapäeval 8. oktoobril")
    assert raw["text"].endswith("Merike Oja\n")


def test_decode_unpadded_base64_and_encoded_words(load):
    msg = load("gmail/message_3_multipart_alternative.json")
    part = msg["payload"]["parts"][0]
    part["body"]["data"] = part["body"]["data"].rstrip("=")
    for h in msg["payload"]["headers"]:
        if h["name"] == "Subject":
            h["value"] = "=?utf-8?q?Homne_teatrik=C3=BClastus?="
    raw = gmail.parse_message(msg)
    assert raw["text"].startswith("Tere, 11.b!") and raw["subject"] == "Homne teatrikülastus"


def test_spoofed_sender_is_not_authenticated(load):
    msg = load("gmail/message_1_multipart_mixed_attachment.json")
    headers = msg["payload"]["headers"]
    for h in headers:
        if h["name"] == "Authentication-Results":
            h["value"] = h["value"].replace("dmarc=pass", "dmarc=fail")
    # A forged header further down does not count: only Gmail's topmost one does.
    headers.append({"name": "Authentication-Results", "value": "evil; dmarc=pass header.from=gag.ee"})
    assert gmail.parse_message(msg)["authenticated"] is False


# --- prefilter ------------------------------------------------------------------------------

def mail(sender="Õpetaja <kadri.kask@gag.ee>", labels=("INBOX",)):
    return {"from": sender, "labels": list(labels)}


@pytest.mark.parametrize("sender, labels, kept", [
    ("Õpetaja <kadri.kask@gag.ee>", ["INBOX"], True),
    ("eKool <noreply@ekool.eu>", ["INBOX", "CATEGORY_UPDATES"], True),
    ("KADRI <Kadri.Kask@GAG.EE>", ["INBOX"], True),
    ("IT-tugi <it-tugi@gag-ee.example>", ["INBOX"], False),       # look-alike domain
    ("x <a@mail.gag.ee>", ["INBOX"], False),                      # subdomains only when listed
    ("Pood <uudiskiri@lugemisroom.example>", ["INBOX"], False),
    ("Õpetaja <kadri.kask@gag.ee>", ["SPAM"], False),
    ("Õpetaja <kadri.kask@gag.ee>", ["INBOX", "CATEGORY_PROMOTIONS"], False),
    ("Mina <opilane.naidis@gmail.com>", ["SENT"], False),
])
def test_prefilter_defaults(conn, sender, labels, kept):
    assert gmail.keep(conn, mail(sender, labels)) is kept


def test_prefilter_settings(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('gmail_senders', ?)",
                 (json.dumps(["*@moodle.example", "sober@gmail.com"]),))
    conn.execute("INSERT INTO settings (key, value) VALUES ('gmail_label', ?)", (json.dumps("Label_4829135"),))
    assert gmail.keep(conn, mail("M <noreply@moodle.example>"))
    assert gmail.keep(conn, mail("S <sober@gmail.com>"))
    assert not gmail.keep(conn, mail("T <teine@gmail.com>"))
    assert not gmail.keep(conn, mail("Õpetaja <kadri.kask@gag.ee>"))  # the list replaces the default
    assert gmail.keep(conn, mail("T <teine@gmail.com>", ["INBOX", "Label_4829135"]))
    assert not gmail.keep(conn, mail("T <teine@gmail.com>", ["SPAM", "Label_4829135"]))


def test_prefilter_reads_headers_before_body(conn, google, gmail_source):
    google.messages["1a115bcfb081f13d"]["labelIds"].append("CATEGORY_PROMOTIONS")
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    assert base.run_source(conn, gmail_source)["items"] == 2
    assert "1a115bcfb081f13d" not in stored(conn)
    assert google.fetched("metadata") == ["1a1153c6ce09d619", "1a115bcfb081f13d", "1a1168f1c0046bde"]
    assert google.fetched("full") == ["1a1153c6ce09d619", "1a1168f1c0046bde"]  # no body of the dropped mail
    skipped = conn.execute("SELECT raw_json FROM source_items WHERE status = 'skipped'").fetchall()
    assert [json.loads(r[0]) for r in skipped] == [{"id": "1a115bcfb081f13d"}]  # the id, nothing of the mail
    # The same range again: no message is looked at a second time, dropped ones included.
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    google.requests.clear()
    base.run_source(conn, gmail_source)
    assert google.fetched("metadata") == [] and google.fetched("full") == []


def retries(conn, source_id):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"gmail_retry:{source_id}",)).fetchone()
    return json.loads(row[0]) if row else {}


def test_failed_message_is_retried_and_the_cursor_moves_on(conn, google, gmail_source):
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    google.failing = {"1a115bcfb081f13d"}
    result = base.run_source(conn, gmail_source)
    assert result == {"ok": False, "items": 2, "error": "Gmail vastas veaga (HTTP 503), proovin hiljem uuesti."}
    assert conn.execute("SELECT cursor, enabled, last_error IS NOT NULL FROM sources").fetchone()[:] == ("5821004", 1, 1)
    assert retries(conn, gmail_source) == {"1a115bcfb081f13d": 1}
    google.failing, google.history = set(), [{"historyId": "5821100"}]  # nothing new since
    google.requests.clear()
    assert base.run_source(conn, gmail_source) == {"ok": True, "items": 1, "error": None}
    assert google.fetched("full") == ["1a115bcfb081f13d"]  # the retry, nothing else
    assert retries(conn, gmail_source) == {}
    assert conn.execute("SELECT cursor, last_error FROM sources").fetchone()[:] == ("5821100", None)


def test_unreadable_message_is_given_up(conn, google, gmail_source):
    conn.execute("UPDATE sources SET cursor = '5820417' WHERE id = ?", (gmail_source,))
    google.failing = {"1a115bcfb081f13d"}
    base.run_source(conn, gmail_source)
    google.history = [{"historyId": "5821100"}]
    for _ in range(2):  # the second and third attempt
        base.run_source(conn, gmail_source)
    assert retries(conn, gmail_source) == {}
    row = conn.execute("SELECT status, raw_json FROM source_items WHERE external_id = '1a115bcfb081f13d'").fetchone()
    assert (row["status"], json.loads(row["raw_json"])) == ("skipped", {"id": "1a115bcfb081f13d",
                                                                        "unreadable": "3 katset ebaõnnestus"})
    google.requests.clear()
    assert base.run_source(conn, gmail_source)["ok"] and google.fetched("metadata") == []


def test_gmail_down_counts_against_no_message(conn, google, gmail_source):
    google.listed = [f"id{i}" for i in range(10)]
    google.failing = set(google.listed)
    result = base.run_source(conn, gmail_source)
    assert not result["ok"] and len(google.fetched("metadata")) == 3
    assert conn.execute("SELECT cursor FROM sources").fetchone()[0] == "5820417"
    assert retries(conn, gmail_source) == {f"id{i}": 0 for i in range(10)}  # all wait, none charged


# --- card -----------------------------------------------------------------------------------

def test_accounts_and_remove(conn, google, gmail_source):
    assert gmail.test_connection(conn) == {"ok": True, "summary": ACCOUNT, "error": None}
    conn.execute("UPDATE sources SET last_error = 'Gmail vastas veaga (HTTP 500), proovin hiljem uuesti.'")
    assert gmail.test_connection(conn)["error"] == f"{ACCOUNT}: Gmail vastas veaga (HTTP 500), proovin hiljem uuesti."
    conn.execute("INSERT INTO settings (key, value) VALUES (?, '{\"x\": 1}')", (f"gmail_retry:{gmail_source}",))
    gmail.remove_account(conn, ACCOUNT)
    assert retries(conn, gmail_source) == {}
    revoke = google.requests[-1]
    assert (revoke.url.path, form(revoke)) == ("/revoke", {"token": "1//refresh"})
    assert gmail.accounts(conn) == [] and vault.get(conn, f"google_account:{ACCOUNT}") is None
    assert gmail.test_connection(conn)["error"] == "Ühtegi Google'i kontot pole lisatud."
    assert connections.get("google").order == 30
