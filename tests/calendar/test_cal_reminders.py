"""app/reminders.py with a fake clock and a fake ntfy server."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import connections, events, jobs, reminders, vault

UTC = timezone.utc
TOKEN = "tk_salajane_ntfy_voti_1234"


class FakeNtfy(list):
    """Stands in for httpx.post; the list holds the pushes it received."""
    status = 200

    def __call__(self, url, json=None, headers=None, **kw):
        self.append({"url": url, "json": json, "headers": headers or {}})
        return httpx.Response(self.status, request=httpx.Request("POST", url))


@pytest.fixture
def ntfy(conn, monkeypatch):
    vault.set_json(conn, "ntfy", {"url": "http://ntfy.test:8090/", "topic": "markmik-test", "token": TOKEN})
    fake = FakeNtfy()
    monkeypatch.setattr(reminders.httpx, "post", fake)
    return fake


def run_at(conn, monkeypatch, when):
    monkeypatch.setattr(reminders, "now_utc", lambda: when)
    reminders.run(conn)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_job_is_registered_every_minute():
    assert jobs.REGISTRY["reminders"].every == 60


def test_test_reminder_the_evening_before(conn, add, ntfy, monkeypatch):
    # Kontrolltöö on Monday 26.10 08:15 (EET). The evening before is Sunday 25.10, already EET:
    # 18:00 local = 16:00 UTC.
    add(title="Matemaatika KT", kind="kontrolltöö", start_at="2026-10-26T06:15:00Z", location="203")
    run_at(conn, monkeypatch, utc(2026, 10, 25, 15, 59))
    assert ntfy == []
    run_at(conn, monkeypatch, utc(2026, 10, 25, 16, 0))
    [push] = ntfy
    assert push["url"] == "http://ntfy.test:8090"
    assert push["json"]["topic"] == "markmik-test"
    assert push["json"]["title"] == "Homme kontrolltöö: Matemaatika KT"
    assert push["json"]["message"] == "esmaspäev 26.10. kell 08:15, 203"
    assert push["headers"]["Authorization"] == f"Bearer {TOKEN}"
    run_at(conn, monkeypatch, utc(2026, 10, 25, 16, 1))
    run_at(conn, monkeypatch, utc(2026, 10, 25, 20, 0))
    assert len(ntfy) == 1  # idempotent


def test_test_reminder_in_summer_time(conn, add, ntfy):
    # All-day test on Sunday 25.10: the evening before is still EEST, 18:00 local = 15:00 UTC.
    add(title="Ajaloo KT", kind="kontrolltöö", all_day=1, start_date="2026-10-25")
    assert reminders.due_reminders(conn, utc(2026, 10, 24, 14, 59)) == []
    [r] = reminders.due_reminders(conn, utc(2026, 10, 24, 15, 0))
    assert (r["rule"], r["fire_at"], r["occurrence"]) == ("test", "2026-10-24T15:00:00Z", "")
    # Once the test day has begun it is too late.
    assert reminders.due_reminders(conn, utc(2026, 10, 24, 22, 0)) == []


def test_homework_and_tasks_due_tomorrow_at_17(conn, add, note, task, ntfy, monkeypatch):
    add(title="Inglise essee", kind="kodutöö", all_day=1, start_date="2026-10-08")
    task(note("fuusika"), "tee ülesanded 1-5", "2026-10-08", due_time="08:00")
    task(note("keemia"), "tehtud juba", "2026-10-08", done=True)
    assert reminders.due_reminders(conn, utc(2026, 10, 7, 13, 59)) == []  # 16:59 local
    run_at(conn, monkeypatch, utc(2026, 10, 7, 14, 0))
    assert sorted(p["json"]["title"] for p in ntfy) == ["Kodutöö homme: Inglise essee", "Ülesanne homme: tee ülesanded 1-5"]
    assert "Tähtaeg neljapäev 8.10. kell 08:00" in [p["json"]["message"] for p in ntfy]
    run_at(conn, monkeypatch, utc(2026, 10, 7, 14, 1))
    assert len(ntfy) == 2


def test_timed_event_thirty_minutes_before(conn, add, ntfy, monkeypatch):
    add(title="Hambaarst", kind="isiklik", start_at="2026-10-07T12:00:00Z", location="Kesklinn")
    assert reminders.due_reminders(conn, utc(2026, 10, 7, 11, 29)) == []
    run_at(conn, monkeypatch, utc(2026, 10, 7, 11, 30))
    [push] = ntfy
    assert push["json"]["title"] == "Kell 15:00: Hambaarst"
    assert push["json"]["message"] == "Algab 30 minuti pärast, Kesklinn."


def test_repeated_hour_fires_once(conn, add, ntfy, monkeypatch):
    # 25.10.2026: clocks go from 04:00 EEST back to 03:00 EET, so 03:00-04:00 happens twice.
    # A 04:15 event (02:15 UTC) is reminded at 03:45 of the second pass, 01:45 UTC; a 03:30
    # first-pass event (00:30 UTC) at 03:00 of the first pass, 00:00 UTC.
    add(title="Öine rong", start_at="2026-10-25T02:15:00Z")
    add(title="Varajane lend", start_at="2026-10-25T00:30:00Z")
    fired = []
    t = utc(2026, 10, 24, 23, 0)
    while t <= utc(2026, 10, 25, 3, 0):
        before = len(ntfy)
        run_at(conn, monkeypatch, t)
        fired += [(p["json"]["title"], t) for p in ntfy[before:]]
        t += timedelta(minutes=1)
    assert fired == [("Kell 03:30: Varajane lend", utc(2026, 10, 25, 0, 0)),
                     ("Kell 04:15: Öine rong", utc(2026, 10, 25, 1, 45))]


def test_recurring_occurrences_are_reminded_separately(conn, add, ntfy, monkeypatch):
    add(title="Trenn", kind="isiklik", start_at="2026-10-19T15:00:00Z", rrule="FREQ=WEEKLY")
    run_at(conn, monkeypatch, utc(2026, 10, 19, 14, 30))
    run_at(conn, monkeypatch, utc(2026, 10, 26, 15, 30))  # EET now: 17:30 local = 15:30 UTC
    assert len(ntfy) == 2
    sent = [r["occurrence"] for r in conn.execute("SELECT occurrence FROM reminders_sent ORDER BY occurrence")]
    assert sent == ["2026-10-19T15:00:00Z", "2026-10-26T16:00:00Z"]


def test_no_reminder_for_lessons_all_day_events_done_or_unconfirmed(conn, add, ntfy):
    add(title="Mate", kind="tund", start_at="2026-10-07T12:00:00Z")
    add(title="Pidu", kind="sündmus", all_day=1, start_date="2026-10-08")
    add(title="Pakkumine", status="pakutud", start_at="2026-10-07T12:00:00Z")
    done = add(title="Tehtud", kind="kodutöö", all_day=1, start_date="2026-10-08")
    events.set_done(conn, done, "", True)
    assert reminders.due_reminders(conn, utc(2026, 10, 7, 11, 45)) == []
    assert reminders.due_reminders(conn, utc(2026, 10, 7, 14, 30)) == []


def test_failed_push_is_retried_next_tick(conn, add, ntfy, monkeypatch):
    add(title="Hambaarst", start_at="2026-10-07T12:00:00Z")
    ntfy.status = 500
    with pytest.raises(RuntimeError, match="ntfy vastas 500"):
        run_at(conn, monkeypatch, utc(2026, 10, 7, 11, 30))
    assert conn.execute("SELECT COUNT(*) FROM reminders_sent").fetchone()[0] == 0
    ntfy.status = 200
    run_at(conn, monkeypatch, utc(2026, 10, 7, 11, 31))
    assert len(ntfy) == 2 and conn.execute("SELECT COUNT(*) FROM reminders_sent").fetchone()[0] == 1


def test_rules_from_settings(conn, add, ntfy):
    add(title="Hambaarst", start_at="2026-10-07T12:00:00Z")
    add(title="KT", kind="kontrolltöö", all_day=1, start_date="2026-10-08")
    reminders.save_rules(conn, {"before": {"minutes": 60}, "test": {"on": False}})
    assert reminders.rules(conn) == {"test": {"on": False, "time": "18:00"}, "homework": {"on": True, "time": "17:00"},
                                     "before": {"on": True, "minutes": 60}}
    assert [r["title"] for r in reminders.due_reminders(conn, utc(2026, 10, 7, 11, 0))] == ["Kell 15:00: Hambaarst"]
    assert reminders.due_reminders(conn, utc(2026, 10, 7, 16, 0)) == []  # the test rule is off


@pytest.mark.parametrize("data, message", [
    ({"test": {"time": "25:00"}}, "Kellaaeg peab olema kujul HH:MM."),
    ({"homework": {"time": "5pm"}}, "Kellaaeg peab olema kujul HH:MM."),
    ({"before": {"minutes": -5}}, "Minuteid peab olema 0 kuni 1440."),
    ({"before": {"minutes": "30"}}, "Minuteid peab olema 0 kuni 1440."),
    ({"before": "30"}, "Vigased reeglid."),
    ([], "Vigased reeglid."),
])
def test_invalid_rules(conn, data, message):
    with pytest.raises(ValueError, match=message):
        reminders.save_rules(conn, data)


def test_nothing_happens_without_ntfy(conn, add, monkeypatch):
    add(title="Hambaarst", start_at="2026-10-07T12:00:00Z")
    monkeypatch.setattr(reminders.httpx, "post", lambda *a, **k: pytest.fail("no push without a topic"))
    run_at(conn, monkeypatch, utc(2026, 10, 7, 11, 30))
    assert conn.execute("SELECT COUNT(*) FROM reminders_sent").fetchone()[0] == 0
    result = connections.run_test(conn, "ntfy")
    assert (result["ok"], result["error"]) == (False, "Teema on määramata.")


def test_ntfy_connection_sends_a_test_and_hides_the_token(conn, ntfy):
    result = connections.run_test(conn, "ntfy")
    assert result["ok"] and result["summary"] == "Testteade saadetud (ntfy.test:8090)."
    assert TOKEN not in str(result) and "markmik-test" not in str(result)
    [push] = ntfy
    assert push["json"]["title"] == "Märkmik"
    assert connections.get("ntfy").order == 50 and connections.get("ntfy").secret_names == ("ntfy",)


def test_default_ntfy_url(conn):
    vault.set_json(conn, "ntfy", {"topic": "t"})
    assert reminders.ntfy_config(conn)["url"] == "http://100.122.97.7:8090"
