"""POST /api/arvuta with the kernel running locally (conftest.local_sandbox)."""

import pytest

from app import mouser, notes, shares
from app.views import arvuta as view

NOTE = """# Tellimus

## Komponendid

| Link | Kogus | Ühikuhind (€) | Summa (€) | Hoiatus |
| --- | --- | --- | --- | --- |
| 595-NE555P | 25 |  |  |  |
| https://lcsc.com/x | 2 | 1,00 € |  |  |

Kokku: `= kokku`

```arvuta-peida
t = tabel("Komponendid")
m = mouser(t["Link"], t["Kogus"])
t["Ühikuhind (€)"] = m["hind"].fillna(t["Ühikuhind (€)"])
t["Hoiatus"] = m["hoiatus"]
t["Summa (€)"] = t["Kogus"] * t["Ühikuhind (€)"]
kokku = t["Summa (€)"].sum()
round(kokku, 2)
```
"""
PART = {"nr": "595-NE555P", "tootja_nr": "NE555P", "tootja": "TI", "kirjeldus": "Timer",
        "breaks": [[1, 0.44], [10, 0.342]], "laos": 20, "min": 1, "kordne": 1}


@pytest.fixture
def note(conn):
    return notes.create(conn, "Tellimus", body=NOTE)


@pytest.fixture
def lookups(monkeypatch):
    calls = []

    def parts(conn, values, *, refresh=False, fetch_new=True):
        calls.append((list(values), refresh, fetch_new))
        return {v: PART for v in values} if fetch_new or refresh else {}
    monkeypatch.setattr(mouser, "parts", parts)
    return calls


def post(owner, slug, markdown, **kw):
    return owner.post("/api/arvuta", json={"slug": slug, "markdown": markdown, **kw})


def test_a_run_fills_the_table_output_and_inline_value(owner, note, local_sandbox, lookups):
    r = post(owner, note["slug"], NOTE)
    assert r.status_code == 200, r.json
    out = r.json["markdown"]
    assert "| 595-NE555P | 25 | 0,342 € | 8,55 € | laos ainult 20 |" in out
    assert "| https://lcsc.com/x | 2 | 1,00 € | 2,00 € |  |" in out
    assert "`= kokku ⇒ 10,55`" in out
    assert "```väljund\n10,55\n```" in out
    assert r.json["cells"] == [{"ok": True}]
    # Auto: the part was never looked up, so it was fetched, then the cells ran again.
    assert lookups == [(["595-NE555P"], False, False), (["595-NE555P"], False, True)]
    # The same inputs again: nothing to do.
    again = post(owner, note["slug"], out, run="auto", hash=r.json["hash"])
    assert again.json == {"skipped": "sama"}
    # Unchanged results: no Markdown comes back.
    assert post(owner, note["slug"], out, run="all").json["markdown"] is None


def test_play_refreshes_and_a_cell_index_runs_up_to_it(owner, note, local_sandbox, lookups):
    post(owner, note["slug"], NOTE, run=0)
    assert lookups[0] == (["595-NE555P"], True, False)
    assert post(owner, note["slug"], NOTE, run=3).status_code == 400


def test_guest_edit_link_stops_automatic_runs(owner, conn, note, local_sandbox, lookups):
    shares.create(conn, note["id"], "edit")
    assert post(owner, note["slug"], NOTE, run="auto").json == {"skipped": "kulalised"}
    assert local_sandbox.requests == []
    assert post(owner, note["slug"], NOTE, run="all").json["markdown"]


def test_a_note_without_cells_needs_no_sandbox(owner, conn, local_sandbox):
    n = notes.create(conn, "Tavaline", body="# Tavaline\n\n```python\nprint(1)\n```\n")
    r = post(owner, n["slug"], n["body"])
    assert r.json["markdown"] is None and local_sandbox.requests == []


def test_sandbox_errors_and_bad_requests(owner, note, monkeypatch, lookups):
    monkeypatch.setattr(view.client, "send", lambda req, s: {"error": "Arvutusmootor pole kättesaadav (konteiner arvutus)."})
    r = post(owner, note["slug"], NOTE)
    assert r.json["error"].startswith("Arvutusmootor") and r.json["markdown"] is None
    assert post(owner, note["slug"], NOTE, run="kõik").status_code == 400
    assert post(owner, "pole", NOTE).status_code == 404
    assert post(owner, note["slug"], "x" * (view.MARKDOWN_CAP + 1)).status_code == 413


def test_one_run_per_note(owner, note, local_sandbox, lookups):
    view._running.add(note["id"])
    try:
        assert post(owner, note["slug"], NOTE).status_code == 409
    finally:
        view._running.discard(note["id"])


def test_only_the_owner(client, note):
    r = client.post("/api/arvuta", json={"slug": note["slug"], "markdown": NOTE})
    assert r.status_code in (302, 401, 403)
