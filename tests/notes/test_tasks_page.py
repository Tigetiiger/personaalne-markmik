"""Ülesanded and the daily template (docs/MARKMED-PLAN.md 15-16)."""

from datetime import date

from app import notes


def test_tasks_page_and_api_leave_templates_out(owner, conn):
    assert owner.get("/ulesanded").status_code == 200
    notes.create(conn, "Kool", folder="kool", body="# Kool\n\n- [ ] õpi\n- [ ] tähtajaga @2026-10-09\n")
    notes.create(conn, "Mall", folder="Mallid", body="# Mall\n\n- [ ] malli ülesanne\n")
    notes.create(conn, "Alam", folder="mallid/x", body="# Alam\n\n- [ ] ka mall\n")
    found = owner.get("/api/tasks").get_json()
    assert [(t["text"], t["folder"]) for t in found] == [("tähtajaga", "kool"), ("õpi", "kool")]


def test_anonymous_cannot_see_the_tasks_page(client):
    assert client.get("/ulesanded").status_code in (302, 401)


def test_daily_note_from_the_template(conn):
    plain = notes.daily(conn, date(2026, 10, 5))
    assert plain["body"] == "# Esmaspäev, 5. oktoober 2026\n"
    notes.create(conn, "Päevik", folder="Mallid", body="# Päevik\n\n{{nädalapäev}} {{kuupäev}}\n\n- [ ] trenn\n")
    made = notes.daily(conn, date(2026, 10, 6))
    assert made["body"] == "# Teisipäev, 6. oktoober 2026\n\nteisipäev 2026-10-06\n\n- [ ] trenn\n"
    assert (made["folder"], made["daily_date"], made["slug"]) == ("paevik", "2026-10-06", "2026-10-06")
