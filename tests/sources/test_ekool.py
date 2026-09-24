"""eKool (app/sources/ekool.py): normalisation from the fixture, subject notes, re-sync rules,
credentials and the honest connection test. The web transport is never called here."""

from datetime import date

import pytest

from app import connections, events, notes, vault
from app.sources import base, ekool


class FakeTransport:
    VERIFIED = True

    def __init__(self, todos=(), error=None):
        self._todos, self.error, self.windows = list(todos), error, []

    def student_name(self):
        if self.error:
            raise self.error
        return "Mari Näidis"

    def todos(self, start, end):
        if self.error:
            raise self.error
        self.windows.append((start, end))
        return list(self._todos)


@pytest.fixture
def transport(monkeypatch, ekool_items):
    fake = FakeTransport(ekool_items)
    monkeypatch.setattr(ekool, "_transport", lambda conn: fake)
    return fake


@pytest.fixture
def sid(conn):
    ekool.save_credentials(conn, "mari@example.ee", "salasõna")
    return conn.execute("SELECT id FROM sources WHERE kind = 'ekool'").fetchone()["id"]


def event(conn, eid):
    return conn.execute("SELECT * FROM events WHERE uid = ?", (f"ekool:{eid}",)).fetchone()


def test_sync_creates_events(conn, sid, transport):
    assert base.run_source(conn, sid) == {"ok": True, "items": 8, "error": None}
    rows = conn.execute("SELECT * FROM events ORDER BY start_date").fetchall()
    assert len(rows) == 8
    assert {(r["source"], r["status"], r["all_day"]) for r in rows} == {("ekool", "kinnitatud", 1)}
    math = event(conn, 15907712301)
    assert (math["title"], math["kind"], math["start_date"], math["end_date"], math["source_ref"]) == (
        "Matemaatika: Kontrolltöö: trigonomeetrilised funktsioonid", "kontrolltöö", "2026-10-01", "2026-10-02",
        "15907712301")
    assert math["description"] == "Kontrolltöö hõlmab õpiku peatükke 3.1–3.4. Kaasa võtta taskuarvuti."
    homework = event(conn, 15907712475)
    assert (homework["kind"], homework["title"]) == ("kodutöö", "Füüsika: Töövihik lk 23, ülesanded 4–7")
    assert event(conn, 15907712420)["description"] == "https://example.com/unit3-words"  # the link text only
    assert event(conn, 15907712388)["description"] == ""
    start, end = transport.windows[0]
    assert start.weekday() == 0 and (end - start).days == 41


def test_subject_notes_created_once(conn, sid, transport):
    base.run_source(conn, sid)
    subject_notes = conn.execute("SELECT id, title, folder FROM notes WHERE deleted_at IS NULL ORDER BY title").fetchall()
    assert [n["title"] for n in subject_notes] == [
        "A-võõrkeel (inglise keel)", "Ajalugu", "Bioloogia", "Eesti keel", "Füüsika", "Keemia", "Kirjandus", "Matemaatika"]
    assert {n["folder"] for n in subject_notes} == {"kool"}
    math_note = notes.by_slug(conn, "matemaatika")
    assert event(conn, 15907712301)["note_id"] == math_note["id"]
    # Renamed by the user: the next item for the subject still goes to it, no second note.
    notes.write_body(conn, math_note["id"], "# Matemaatika 11.b\n")
    transport._todos.append({**transport._todos[0], "id": 1, "title": "Uus töö"})
    base.run_source(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 8
    assert event(conn, 1)["note_id"] == math_note["id"]


def test_resync_keeps_user_edits(conn, sid, transport):
    base.run_source(conn, sid)
    math = event(conn, 15907712301)
    conn.execute("UPDATE events SET title = 'Minu pealkiri', user_edited = 1 WHERE id = ?", (math["id"],))
    transport._todos[0] = {**transport._todos[0], "title": "Muudetud eKoolis"}
    assert base.run_source(conn, sid)["items"] == 1
    assert event(conn, 15907712301)["title"] == "Minu pealkiri"


def test_changed_item_updates_event(conn, sid, transport):
    base.run_source(conn, sid)
    transport._todos[0] = {**transport._todos[0], "date": "2026-10-02"}
    base.run_source(conn, sid)
    math = event(conn, 15907712301)
    assert (math["start_date"], math["end_date"]) == ("2026-10-02", "2026-10-03")


def test_removed_event_stays_gone(conn, sid, transport):
    base.run_source(conn, sid)
    events.remove(conn, event(conn, 15907712301)["id"])
    transport._todos[0] = {**transport._todos[0], "title": "Muudetud"}  # changed, so normalised again
    base.run_source(conn, sid)
    assert event(conn, 15907712301)["status"] == "eemaldatud"
    assert conn.execute("SELECT COUNT(*) FROM events WHERE uid = 'ekool:15907712301'").fetchone()[0] == 1


def test_done_flag_set_but_never_cleared(conn, sid, transport):
    base.run_source(conn, sid)
    done = {r[0] for r in conn.execute("SELECT e.uid FROM event_done d JOIN events e ON e.id = d.event_id")}
    assert done == {"ekool:15907712388"}
    ticked_here = event(conn, 15907712301)["id"]
    conn.execute("INSERT INTO event_done (event_id, occurrence) VALUES (?, '')", (ticked_here,))
    transport._todos[0] = {**transport._todos[0], "title": "Muudetud"}
    base.run_source(conn, sid)
    assert conn.execute("SELECT 1 FROM event_done WHERE event_id = ?", (ticked_here,)).fetchone()


def test_ekool_event_retires_ai_duplicate(conn, sid, transport):
    def ai(uid, title, kind):
        events.create(conn, {"uid": uid, "title": title, "kind": kind, "all_day": 1, "start_date": "2026-10-05",
                             "status": "kinnitatud", "source": "ai", "description": "Kirjast: x"})
    ai("ai:same", "Töövihik lk 23, ülesanded 4–7", "kodutöö")   # the same work, worded alike
    ai("ai:subject", "Füüsika kodutöö", "kodutöö")              # only the subject agrees
    ai("ai:other", "Füüsika olümpiaad", "sündmus")
    base.run_source(conn, sid)
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM events WHERE source = 'ai'")}
    assert {uid: r["status"] for uid, r in rows.items()} == {
        "ai:same": "eemaldatud", "ai:subject": "pakutud", "ai:other": "kinnitatud"}
    assert rows["ai:subject"]["description"] == "eKoolis on samal päeval: Füüsika: Töövihik lk 23, ülesanded 4–7\n\nKirjast: x"
    assert [r[0] for r in conn.execute("SELECT uid FROM rejected_uids")] == ["ai:same"]


def test_homework_gone_from_ekool(conn, sid, transport, monkeypatch):
    monkeypatch.setattr(ekool, "local_today", lambda: date(2026, 9, 30))  # window 21.09 to 01.11
    far = {**transport._todos[0], "id": 2, "date": "2026-12-01"}
    transport._todos.append(far)
    base.run_source(conn, sid)
    everything = list(transport._todos)
    transport._todos[:] = everything[1:-1]  # the maths test and the far item are not in this reply
    assert base.run_source(conn, sid)["ok"]
    assert event(conn, 15907712301)["status"] == "eemaldatud"
    assert event(conn, 2)["status"] == "kinnitatud"  # outside the window that was read: untouched
    assert conn.execute("SELECT COUNT(*) FROM rejected_uids").fetchone()[0] == 0
    transport._todos[:] = everything
    base.run_source(conn, sid)
    assert event(conn, 15907712301)["status"] == "kinnitatud"


def test_save_credentials(conn):
    with pytest.raises(ValueError):
        ekool.save_credentials(conn, " ", "x")
    vault.set_json(conn, "ekool_tokens", {"EKOOLSESSION": "old"})
    ekool.save_credentials(conn, " mari@example.ee ", "salasõna")
    assert vault.get(conn, "ekool_username") == "mari@example.ee"
    assert vault.get(conn, "ekool_password") == "salasõna"
    assert vault.get(conn, "ekool_tokens") is None
    row = conn.execute("SELECT name, enabled FROM sources WHERE kind = 'ekool'").fetchone()
    assert (row["name"], row["enabled"]) == ("eKool", 1)


def test_missing_credentials_is_not_connected(conn, sid):
    vault.delete(conn, "ekool_password")
    assert base.run_now(conn, "ekool")["ekool"]["error"] == "eKool: Pole ühendatud."
    assert conn.execute("SELECT enabled, last_error FROM sources WHERE id = ?", (sid,)).fetchone()[:] == (0, None)


def test_unverified_transport_refuses_before_any_request(conn, sid, monkeypatch):
    # The real EkoolWebTransport, with its network paths booby-trapped: scheduled runs must not
    # log in to eKool while the homework call is unknown.
    def trap(*a, **kw):
        raise AssertionError("no request to eKool")
    monkeypatch.setattr(ekool.EkoolWebTransport, "_login", trap)
    monkeypatch.setattr(ekool.EkoolWebTransport, "_dwr", trap)
    result = base.run_source(conn, sid)
    assert result["error"] == "eKooli kodutööde päring: kontrollimata — vajab päris sisselogimist."
    assert conn.execute("SELECT enabled FROM sources WHERE id = ?", (sid,)).fetchone()[0] == 0


def test_connection_test_is_honest(conn):
    assert ekool.test_connection(conn)["error"] == "Pole ühendatud: kasutajanimi ja parool puuduvad."
    ekool.save_credentials(conn, "mari@example.ee", "salasõna")
    assert ekool.test_connection(conn) == {
        "ok": False, "summary": "Kasutajanimi ja parool on salvestatud.", "error": "Kontrollimata — vajab päris sisselogimist."}
    assert connections.get("ekool").order == 20


def test_connection_test_after_verification(conn, monkeypatch, ekool_items):
    ekool.save_credentials(conn, "mari@example.ee", "salasõna")
    monkeypatch.setattr(ekool, "_transport", lambda conn: FakeTransport(ekool_items[:3]))
    monkeypatch.setattr(ekool, "local_today", lambda: date(2026, 9, 28))
    assert ekool.test_connection(conn) == {
        "ok": True, "summary": "Mari Näidis — 3 eelseisvat ülesannet/kontrolltööd", "error": None}
    monkeypatch.setattr(ekool, "_transport", lambda conn: FakeTransport(error=base.NeedsUser(ekool.TWO_FACTOR)))
    result = ekool.test_connection(conn)
    assert not result["ok"] and "kinnituskoodi" in result["error"]
