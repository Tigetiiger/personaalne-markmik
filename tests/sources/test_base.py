"""Source runner (app/sources/base.py): idempotent storage, cursor, health, not-connected, run_now."""

import json

import httpx
import pytest

from app import jobs
from app.sources import base


class FakeAdapter:
    def __init__(self, kind="ics", structured=True):
        self.kind, self.structured = kind, structured
        self.items, self.cursor, self.error = [], "c1", None
        self.normalised = []

    def fetch(self, conn, source_row, cursor):
        if self.error:
            raise self.error
        return list(self.items), self.cursor

    def normalise(self, conn, source_row, raw):
        if raw.get("boom"):
            raise RuntimeError("normaliser broke")
        self.normalised.append(raw)


@pytest.fixture
def fake(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(base, "adapter_for", lambda kind: adapter)
    return adapter


def source(conn, kind="ics", name="Kool"):
    return base.upsert_source(conn, kind, name)


def row(conn, source_id):
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def items(conn):
    return conn.execute("SELECT external_id, status, raw_json FROM source_items ORDER BY id").fetchall()


def test_stores_items_once_and_moves_cursor(conn, fake):
    sid = source(conn)
    fake.items = [{"external_id": "a", "raw": {"n": 1}}, {"external_id": 7, "raw": {"n": 2}}]
    assert base.run_source(conn, sid) == {"ok": True, "items": 2, "error": None}
    fake.cursor = "c2"
    assert base.run_source(conn, sid) == {"ok": True, "items": 0, "error": None}
    assert [(r["external_id"], r["status"]) for r in items(conn)] == [("a", "done"), ("7", "done")]
    assert len(fake.normalised) == 2  # unchanged items are not normalised again
    src = row(conn, sid)
    assert (src["cursor"], src["last_error"], src["enabled"]) == ("c2", None, 1)
    assert src["last_run_at"] and src["last_ok_at"]


def test_changed_structured_item_is_normalised_again(conn, fake):
    sid = source(conn)
    fake.items = [{"external_id": "a", "raw": {"title": "vana"}}]
    base.run_source(conn, sid)
    fake.items = [{"external_id": "a", "raw": {"title": "uus"}}]
    assert base.run_source(conn, sid)["items"] == 1
    assert [r["title"] for r in fake.normalised] == ["vana", "uus"]
    assert len(items(conn)) == 1


def test_unstructured_items_wait_for_extract(conn, fake):
    fake.structured = False
    sid = source(conn, "gmail", "a@b.ee")
    fake.items = [{"external_id": "m1", "raw": {"text": "x"}}]
    base.run_source(conn, sid)
    fake.items = [{"external_id": "m1", "raw": {"text": "x", "labels": ["changed"]}}]
    base.run_source(conn, sid)
    rows = items(conn)
    assert [(r["external_id"], r["status"]) for r in rows] == [("m1", "new")]
    assert json.loads(rows[0]["raw_json"]) == {"text": "x"}  # a message is stored once
    assert fake.normalised == []


def test_normaliser_error_marks_item_only(conn, fake):
    sid = source(conn)
    fake.items = [{"external_id": "a", "raw": {"boom": True}}, {"external_id": "b", "raw": {"n": 1}}]
    assert base.run_source(conn, sid)["ok"]
    rows = conn.execute("SELECT external_id, status, error FROM source_items ORDER BY id").fetchall()
    assert [(r["external_id"], r["status"]) for r in rows] == [("a", "error"), ("b", "done")]
    assert rows[0]["error"] == "Ootamatu viga (RuntimeError), vaata logi."


def test_failure_keeps_cursor_and_hides_details(conn, fake):
    sid = source(conn)
    base.run_source(conn, sid)
    fake.error = RuntimeError("password=hunter2 in some URL")
    result = base.run_source(conn, sid)
    src = row(conn, sid)
    assert result == {"ok": False, "items": 0, "error": "Ootamatu viga (RuntimeError), vaata logi."}
    assert (src["cursor"], src["enabled"]) == ("c1", 1)
    assert "hunter2" not in src["last_error"]
    fake.error = httpx.ConnectError("https://x/?token=secret")
    assert "secret" not in base.run_source(conn, sid)["error"]
    fake.error = base.SourceError("Gmail vastas veaga (HTTP 500), proovin hiljem uuesti.")
    base.run_source(conn, sid)
    assert row(conn, sid)["last_error"] == "Gmail vastas veaga (HTTP 500), proovin hiljem uuesti."
    fake.error = None
    base.run_source(conn, sid)
    assert row(conn, sid)["last_error"] is None


def test_not_connected_is_quiet_and_stops_runs(conn, fake):
    sid = source(conn)
    fake.error = base.NotConnected()
    assert base.run_now(conn, "ics") == {"ics": {"ok": False, "items": 0, "error": "Kool: Pole ühendatud."}}
    src = row(conn, sid)
    assert (src["enabled"], src["last_error"]) == (0, None)
    fake.error = AssertionError("a disabled source is not run")
    assert base.run_now(conn, "ics") == {"ics": {"ok": False, "items": 0, "error": "Pole ühendatud."}}
    source(conn)  # credentials saved again
    assert row(conn, sid)["enabled"] == 1


def test_needs_user_disables_with_message(conn, fake):
    sid = source(conn)
    fake.error = base.NeedsUser("eKool ei võtnud kasutajanime või parooli vastu.")
    base.run_source(conn, sid)
    src = row(conn, sid)
    assert (src["enabled"], src["last_error"]) == (0, "eKool ei võtnud kasutajanime või parooli vastu.")


def test_run_now_totals_per_kind(conn, fake):
    source(conn, "ics", "Üks")
    source(conn, "ics", "Kaks")
    fake.items = [{"external_id": "a", "raw": {}}]
    assert base.run_now(conn) == {"ics": {"ok": True, "items": 2, "error": None}}  # one item per feed
    assert base.run_now(conn, "ekool") == {"ekool": {"ok": False, "items": 0, "error": "Pole ühendatud."}}


def test_jobs_registered(app):  # create_app imports every job module
    assert jobs.REGISTRY["gmail-sync"].every == 300
    assert jobs.REGISTRY["ekool-sync"].at == ("07:00", "13:00", "17:00", "21:00")
    assert jobs.REGISTRY["ics-sync"].every == 3600
    assert jobs.REGISTRY["extract"].every == 120


def test_html_to_text_is_safe():
    html = ('<p>Kontrolltöö &amp; kordamine</p><script>alert(1)</script><style>p{}</style>'
            '<!-- peidus --><table><tr><td>Aine:</td><td>Keemia</td></tr></table><a href="x">link</a><br>lõpp')
    assert base.html_to_text(html) == "Kontrolltöö & kordamine\nAine: Keemia\n\nlink\nlõpp"
