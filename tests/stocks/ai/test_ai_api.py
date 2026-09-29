"""The stocks-ai API: the on-demand streams (progress, then done or error; one at a time; the claim
released however the run ends) and the reading endpoints."""

import json

import pytest
from ai_helpers import LONG, add_stock, html, research_answer

from app.ai import runner
from app.stocks import common

Q2 = "https://investor.example.com/q2"


def events(response):
    """[event dicts] of a text/event-stream body; each frame's event name matches its type."""
    assert response.status_code == 200 and response.mimetype == "text/event-stream"
    out = []
    for frame in response.get_data(as_text=True).split("\n\n"):
        if not frame or frame.startswith(":"):
            continue
        head, data = frame.split("\n")
        ev = json.loads(data.removeprefix("data: "))
        assert head == f"event: {ev['type']}"
        out.append(ev)
    return out


@pytest.fixture
def nvda(conn):
    return add_stock(conn, "NVDA", "NVIDIA Corporation")


def test_owner_only(client):
    assert client.get("/api/stocks/research/NVDA").status_code == 401
    assert client.get("/api/stocks/verdicts/NVDA").status_code == 401
    assert client.get("/api/stocks/explore").status_code == 401
    assert client.post("/api/stocks/research/NVDA").status_code in (401, 403)
    assert client.post("/api/stocks/explore", json={}).status_code in (401, 403)


def test_research_now_streams_its_steps_and_stores_an_on_demand_note(owner, conn, model, pages, nvda):
    model.answers["discover"] = {"candidates": [{"url": Q2, "title": "Q2", "why": "Tulemused."}]}
    model.answers["research"] = research_answer()
    pages.pages[Q2] = html("Q2", LONG)

    evs = events(owner.post("/api/stocks/research/NVDA"))

    assert [e["text"] for e in evs if e["type"] == "progress"] == ["Otsin allikaid…", "Loen 1/1 lehte…", "Kirjutan…"]
    assert evs[-1]["type"] == "done"
    row = conn.execute("SELECT * FROM analyses WHERE id = ?", (evs[-1]["id"],)).fetchone()
    assert row["kind"] == "on_demand" and row["instrument_id"] == nvda
    assert not common.claimed(conn, "web-ai")

    got = owner.get("/api/stocks/research/NVDA").get_json()
    assert got["running"] is False and got["can_run"] is True
    (a,) = got["analyses"]
    assert a["summary"] == "Andmekeskuste nõudlus kasvab." and a["bull"] == [{"text": "Juhtkond tõstis prognoosi.", "citation": 0}]
    assert [c["url"] for c in a["citations"]] == [Q2]  # the other page was never fetched
    assert a["bear"] == []


def test_a_second_run_waits_its_turn(owner, conn, nvda):
    assert common.claim(conn, "web-ai")
    r = owner.post("/api/stocks/research/NVDA")
    assert r.status_code == 409 and "juba käib" in r.get_json()["error"]
    r = owner.post("/api/stocks/explore", json={"focus": "", "count": 6})
    assert r.status_code == 409
    assert owner.get("/api/stocks/research/NVDA").get_json()["running"] is True


@pytest.mark.parametrize("failure", [runner.AIUnavailable("Claude'i kasutuspiir on praegu täis, proovi hiljem uuesti"),
                                     RuntimeError("AI päring ebaõnnestus (error_max_turns)")])
def test_a_failed_run_says_why_and_releases_the_claim(owner, conn, model, pages, nvda, failure):
    model.answers["discover"] = {"candidates": []}
    model.answers["research"] = failure
    evs = events(owner.post("/api/stocks/research/NVDA"))
    assert evs[-1] == {"type": "error", "message": str(failure)}
    assert not common.claimed(conn, "web-ai")
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0


def test_an_unexpected_error_is_worded_and_releases_the_claim(owner, conn, model, pages, nvda, monkeypatch):
    from app.stocks import research

    def broken(*a, **kw):
        raise KeyError("x")
    monkeypatch.setattr(research, "research", broken)
    evs = events(owner.post("/api/stocks/research/NVDA"))
    assert evs == [{"type": "error", "message": "Uuring katkes ootamatu vea tõttu."}]
    assert not common.claimed(conn, "web-ai")


def test_research_now_is_for_known_stocks_only(owner, conn):
    add_stock(conn, "VWCE", "Vanguard FTSE All-World", kind="etf")
    assert owner.post("/api/stocks/research/NOPE").status_code == 404
    r = owner.post("/api/stocks/research/VWCE")
    assert r.status_code == 400 and "ETF" in r.get_json()["error"]
    assert owner.get("/api/stocks/research/VWCE").get_json()["can_run"] is False
    assert not common.claimed(conn, "web-ai")


def test_explore_streams_and_lists(owner, conn, model, pages, nvda):
    model.answers["explore_discover"] = {"candidates": [{"url": Q2, "title": "ASML", "symbol": "ASML", "why": "x"}]}
    model.answers["explore"] = {"market_context": "Rahulik.", "ideas": [
        {"symbol": "ASML", "name": "ASML Holding", "exchange": "NASDAQ", "kind": "stock", "what_it_is": "Litograafia.",
         "why_interesting": "Tellimused.", "catalysts": [], "risks": [], "horizon": "years", "conviction": "high",
         "links": [{"url": Q2, "title": "Q2", "why": "Tellimused."}]}]}
    pages.pages[Q2] = html("Q2", LONG)

    evs = events(owner.post("/api/stocks/explore", json={"focus": "pooljuhid", "count": 3}))

    assert [e["type"] for e in evs] == ["progress", "progress", "progress", "done"]
    got = owner.get("/api/stocks/explore").get_json()
    assert got["running"] is False
    (e,) = got["explorations"]
    assert e["id"] == evs[-1]["id"] and e["focus"] == "pooljuhid" and e["requested_ideas"] == 3
    assert e["ideas"][0]["links"][0]["url"] == Q2
    assert not common.claimed(conn, "web-ai")


@pytest.mark.parametrize(("body", "message"), [
    ({"count": 2}, "3–10"), ({"count": 11}, "3–10"), ({"count": "6"}, "3–10"), ({"count": True}, "3–10"),
    ({"focus": "x" * 201}, "200"), ({"focus": 5}, "200"),
])
def test_explore_checks_its_fields(owner, conn, body, message):
    r = owner.post("/api/stocks/explore", json=body)
    assert r.status_code == 400 and message in r.get_json()["error"]
    assert not common.claimed(conn, "web-ai")


def test_reading_endpoints_normalise_old_rows_and_keep_only_web_links(owner, conn, nvda):
    conn.execute(
        "INSERT INTO analyses (instrument_id, kind, as_of, summary, bull_json, citations_json, prompt_version, model) "
        "VALUES (?, 'deep', '2026-09-20', 'Imporditud.', ?, ?, 'research@x', 'claude-opus-5')",
        (nvda, json.dumps([{"claim": "Vana kuju", "citation": 0}]),
         json.dumps([{"url": "javascript:alert(1)", "quote": "q"}, {"url": "https://ok.example/a", "quote": "q"}])))
    conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, reasons_for_json, prompt_version, model) "
                 "VALUES (?, '2026-09-27', 'sell', 'high', 'Müüks.', '[\"Põhjus\"]', 'v', 'sonnet')", (nvda,))
    conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                 "VALUES (?, '2026-09-28', 'hold', 'low', 'Hoiaks.', 'v', 'sonnet')", (nvda,))
    conn.execute("INSERT INTO explorations (focus, requested_ideas, market_context, ideas_json, prompt_version, model) "
                 "VALUES (NULL, 6, 'Taust.', ?, 'e', 'm')",
                 (json.dumps([{"symbol": "X", "links": [{"url": "javascript:x", "title": "t"}]}]),))

    (a,) = owner.get("/api/stocks/research/NVDA").get_json()["analyses"]
    assert a["bull"] == [{"text": "Vana kuju", "citation": 0}]
    assert [c["url"] for c in a["citations"]] == [None, "https://ok.example/a"]
    got = owner.get("/api/stocks/verdicts/NVDA").get_json()
    assert [v["verdict"] for v in got["verdicts"]] == ["hold", "sell"] and got["kind"] == "stock"
    assert got["verdicts"][1]["reasons_for"] == ["Põhjus"]
    (e,) = owner.get("/api/stocks/explore").get_json()["explorations"]
    assert e["ideas"][0]["links"][0]["url"] is None
    assert owner.get("/api/stocks/verdicts/NOPE").status_code == 404
