"""eKool: homework and tests as all-day calendar events, plus one note per school subject.

Two halves (docs/spikes/ekool.md §7):
- normalisation, tested: an internal item {id, subject, date 'YYYY-MM-DD', title, content_html,
  is_test, is_done} becomes an event (uid ekool:<id>, auto-confirmed) linked to its subject note;
- transport, UNVERIFIED: anything with student_name() and todos(start, end) -> internal items.
  The only one, EkoolWebTransport, has never talked to eKool.
"""

import re
import secrets
from datetime import date, timedelta

import httpx

from .. import connections, events, extract, notes, vault
from ..db import tx
from ..util import local_today
from .base import NeedsUser, NotConnected, SourceError, html_to_text, upsert_source

SUBJECT_FOLDER = "kool"
TITLE_MAX = 200
UNVERIFIED = "Kontrollimata — vajab päris sisselogimist."
TWO_FACTOR = ("eKool küsib kinnituskoodi (kaheastmeline sisselogimine). Automaatne sisselogimine "
              "sellise kontoga ei tööta.")


class EkoolAdapter:
    kind = "ekool"
    structured = True

    def fetch(self, conn, source_row, cursor):
        # eKool has no change feed: every run re-reads Monday of last week to five weeks ahead.
        today = local_today()
        start = today - timedelta(days=today.weekday() + 7)
        todos = _transport(conn).todos(start, start + timedelta(weeks=6, days=-1))
        return [{"external_id": f"todo:{t['id']}", "raw": t} for t in todos], start.isoformat()

    def normalise(self, conn, source_row, item):
        normalise(conn, item)


ADAPTER = EkoolAdapter()


def normalise(conn, item):
    """Upsert the event for one internal item. Never overwrites an event the user edited
    (events.upsert_by_uid), and a removed one stays removed."""
    day = date.fromisoformat(item["date"])
    kind = "kontrolltöö" if item["is_test"] else "kodutöö"
    title = " ".join(f"{item['subject']}: {item['title']}".split())[:TITLE_MAX]
    uid = f"ekool:{item['id']}"
    event_id, created = events.upsert_by_uid(conn, uid, {
        "title": title, "kind": kind, "all_day": 1, "start_date": day.isoformat(),
        "end_date": (day + timedelta(days=1)).isoformat(), "status": "kinnitatud", "source": "ekool",
        "source_ref": str(item["id"]), "description": html_to_text(item["content_html"]),
        "note_id": subject_note(conn, item["subject"]),
    })
    if event_id is None:
        return
    if created:
        # eKool wins over an AI event for the same work that got here first (the notification mail
        # usually arrives before the next eKool sync).
        for dup in extract.duplicates(conn, day, title, kind, "ekool", exclude_uid=uid):
            if dup["source"] == "ai" and not dup["user_edited"] and dup["status"] != "eemaldatud":
                events.remove(conn, dup["id"])
    if item["is_done"]:
        # Only ever set, never cleared: homework ticked done here is unknown to eKool, and the next
        # sync must not untick it.
        conn.execute("INSERT OR IGNORE INTO event_done (event_id, occurrence) VALUES (?, '')", (event_id,))


def subject_note(conn, subject):
    """The note of an eKool subject, created in "kool" the first time the subject appears. The
    link is kept in settings, so a renamed or moved note keeps its subject and a deleted one is not
    brought back."""
    key = f"ekool_subject:{subject}"
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        note = notes.get(conn, int(row["value"]))
        return note["id"] if note else None
    note = notes.create(conn, subject, folder=SUBJECT_FOLDER)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, str(note["id"])))
    return note["id"]


def save_credentials(conn, username, password):
    username, password = (username or "").strip(), password or ""
    if not username or not password:
        raise ValueError("Sisesta eKooli kasutajanimi ja parool.")
    with tx(conn):
        vault.set(conn, "ekool_username", username)
        vault.set(conn, "ekool_password", password)
        vault.delete(conn, "ekool_tokens")  # the old login's session
        upsert_source(conn, "ekool", "eKool")


def test_connection(conn):
    try:
        transport = _transport(conn)
    except NotConnected:
        return {"ok": False, "summary": "", "error": "Pole ühendatud: kasutajanimi ja parool puuduvad."}
    if not transport.VERIFIED:
        return {"ok": False, "summary": "Kasutajanimi ja parool on salvestatud.", "error": UNVERIFIED}
    today = local_today()
    try:
        name = transport.student_name()
        count = len(transport.todos(today, today + timedelta(days=13)))
    except SourceError as exc:
        return {"ok": False, "summary": "", "error": str(exc)}
    except httpx.HTTPError:
        return {"ok": False, "summary": "", "error": "eKool ei vastanud."}
    return {"ok": True, "summary": f"{name} — {count} eelseisvat ülesannet/kontrolltööd", "error": None}


def _transport(conn):
    username, password = vault.get(conn, "ekool_username"), vault.get(conn, "ekool_password")
    if not username or not password:
        raise NotConnected
    return EkoolWebTransport(conn, username, password)


class EkoolWebTransport:
    """UNVERIFIED. Written from docs/spikes/ekool.md §4 and §8 (eKoolCpp 2023, QoLC May 2026)
    without an eKool account: not one request here has been sent to eKool. Never called in tests.

    Route: GET login.ekool.eu, POST /login (form) -> {"success", "url"}, GET url sets the
    EKOOLSESSION cookie (domain ekool.eu), then DWR plain calls on family.ekool.eu. The cookie is
    kept in the vault (ekool_tokens) and reused, because every fresh login may e-mail the user or
    ask for a two-factor code.

    The live spike S3 must confirm: the login reply and what a two-factor account gets back, the
    DWR reply format, how an expired session shows, and the todoManager call for homework (its
    method, parameters and reply are in no code found). Until then todos() refuses before any
    request, so scheduled runs never log in, and VERIFIED stays False.
    """

    VERIFIED = False
    LOGIN = "https://login.ekool.eu"
    FAMILY = "https://family.ekool.eu"

    def __init__(self, conn, username, password):
        self.conn, self.username, self.password = conn, username, password
        self.http = httpx.Client(timeout=30, follow_redirects=True)
        self.script_session = None

    def student_name(self):
        reply = self._call("userAccountManager", "getSessData")
        parts = [m.group(1) for key in ("name1", "name2") if (m := re.search(rf'\.{key}="([^"]*)"', reply))]
        return " ".join(parts) or "eKooli kasutaja"

    def todos(self, start, end):
        # Goes here once S3 has recorded it: self._call("todoManager", <method>, <params>) and the
        # reply parsed into internal items.
        raise NeedsUser(f"eKooli kodutööde päring: {UNVERIFIED[0].lower()}{UNVERIFIED[1:]}")

    def _login(self):
        self.http.get(self.LOGIN + "/")
        r = self.http.post(self.LOGIN + "/login", headers={"X-Requested-With": "XMLHttpRequest"}, data={
            "email": self.username, "password": self.password, "recaptcha": "", "two_factor_code": ""})
        try:
            reply = r.json()
        except ValueError:
            reply = {}
        if not reply.get("success"):
            # What eKool answers a two-factor account is unknown; the form field's name is the guess.
            raise NeedsUser(TWO_FACTOR if "two_factor" in r.text else "eKool ei võtnud kasutajanime või parooli vastu.")
        self.http.get(reply["url"])
        session = self.http.cookies.get("EKOOLSESSION")
        if not session:
            raise SourceError("eKool ei andnud sisselogimisel sessiooni.")
        vault.set_json(self.conn, "ekool_tokens", {"EKOOLSESSION": session})

    def _call(self, script, method, *params):
        if self.script_session is None:
            saved = (vault.get_json(self.conn, "ekool_tokens") or {}).get("EKOOLSESSION")
            if saved:
                self.http.cookies.set("EKOOLSESSION", saved, domain=".ekool.eu")
            else:
                self._login()
            m = re.search(r'handleCallback\("0","0","([^"]+)"\)', self._dwr("__System", "generateId"))
            if m is None and saved:  # the stored session has expired
                self._login()
                m = re.search(r'handleCallback\("0","0","([^"]+)"\)', self._dwr("__System", "generateId"))
            if m is None:
                raise SourceError("eKooli vastus oli ootamatu.")
            self.script_session = f"{m.group(1)}/{secrets.token_hex(4)}"
        return self._dwr(script, method, *params)

    def _dwr(self, script, method, *params):
        lines = ["callCount=1", "windowName=", f"c0-scriptName={script}", f"c0-methodName={method}", "c0-id=0",
                 *(f"c0-param{i}={p}" for i, p in enumerate(params)),
                 "batchId=0", "instanceId=0", "page=%2F", f"scriptSessionId={self.script_session or ''}"]
        r = self.http.post(f"{self.FAMILY}/dwr/call/plaincall/{script}.{method}.dwr",
                           content="\n".join(lines) + "\n", headers={"Content-Type": "text/plain"})
        if r.status_code != 200:
            raise SourceError(f"eKool vastas veaga (HTTP {r.status_code}).")
        return r.text


connections.register("ekool", "eKool", order=20, test=test_connection,
                     secret_names=("ekool_username", "ekool_password", "ekool_tokens"))
