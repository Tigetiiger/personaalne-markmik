"""The nightly call: what it is shown, the stored snapshot, the order and the stops."""

import json
from datetime import timedelta

import pytest
from ai_helpers import (DEADLINE, NOW, TODAY, add_bars, add_doc, add_signal, add_stock, hold, night, research_answer, rows,
                        verdict_answer)

from app.ai import runner
from app.stocks import research, verdict
from app.stocks.verdict import verdict_night


def test_inputs_cover_research_news_market_position_and_the_previous_call(conn, model, pages):
    iid = add_stock(conn, "NVDA", "NVIDIA Corporation")
    add_bars(conn, iid)
    hold(conn, iid)
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, bull_json, prompt_version, model) "
                 "VALUES (?, 'deep', '2026-09-24', 'Tees püsib.', '[{\"claim\": \"Vana väide\", \"citation\": 0}]', 'r', 'm')", (iid,))
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) "
                 "VALUES (?, 'on_demand', '2026-09-29', 'Tulevikust.', 'r', 'm')", (iid,))
    conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                 "VALUES (?, '2026-09-27', 'hold', 'low', 'Ootan.', 'v', 'm')", (iid,))
    for i in range(20):
        doc = add_doc(conn, iid, f"Uudis {i}", url=f"https://example.com/{i}")
        add_signal(conn, doc, iid, relevance=f"0.{i:02d}", summary=f"Kokkuvõte {i}")
    old = add_doc(conn, iid, "Vana uudis")
    add_signal(conn, old, iid, relevance="0.99", created=(NOW - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    model.answers["verdict"] = verdict_answer()
    run_id = night(conn)

    result = verdict_night(conn, run_id, TODAY, NOW, DEADLINE)

    assert result == {"done": ["NVDA"], "skipped": [], "failed": [], "stopped": None}
    (call,) = model.calls
    prompt = call["prompt"]
    assert call["model"] == "sonnet" and call["timeout"] == 300 and not call.get("web_search") and not call.get("thinking")
    assert "Tees püsib." in prompt and "Vana väide" in prompt and "Tulevikust." not in prompt
    assert '"verdict": "hold"' in prompt and "Ootan." in prompt
    shown = [int(n) for n in __import__("re").findall(r"Kokkuvõte (\d+)", prompt)]
    assert shown == list(range(19, 4, -1))  # the 15 most relevant of 14 days, most relevant first
    assert "Vana uudis" not in prompt
    assert '"held": true' in prompt and '"qty": "37"' in prompt and "LHV" not in prompt
    assert '"last_close"' in prompt and '"state"' in prompt

    (v,) = rows(conn, "SELECT * FROM verdicts WHERE as_of = ?", TODAY.isoformat())
    assert (v["verdict"], v["conviction"], v["summary"], v["run_id"], v["model"]) == ("buy", "medium", "Kasv jätkub.", run_id, "sonnet")
    assert json.loads(v["reasons_for_json"]) == ["Tugev nõudlus"] and json.loads(v["what_would_change_json"]) == ["Tellimuste langus"]
    assert v["prompt_version"] == verdict.load("verdict").version
    snapshot = json.loads(v["inputs_json"])
    assert snapshot["research_as_of"] == "2026-09-24" and snapshot["signals"] == 15
    assert snapshot["previous"]["verdict"] == "hold" and snapshot["position"]["held"] is True
    assert snapshot["market"]["stats"]["last_close"]
    run = rows(conn, "SELECT purpose, run_id FROM stock_ai_runs WHERE id = ?", v["ai_run_id"])[0]
    assert run == {"purpose": "verdict", "run_id": run_id}
    assert verdict.latest(conn, iid)["id"] == v["id"]


def stocks(conn):
    ids = {s: add_stock(conn, s, f"{s} Inc") for s in ("AAA", "BBB", "CCC", "DDD")}
    add_stock(conn, "VWCE", "Vanguard FTSE All-World", kind="etf")
    hold(conn, ids["DDD"])
    return ids


def test_holdings_first_etfs_left_out_and_a_rerun_makes_no_duplicates(conn, model, pages):
    stocks(conn)
    model.answers["verdict"] = verdict_answer()
    run_id = night(conn)
    assert verdict_night(conn, run_id, TODAY, NOW, DEADLINE)["done"] == ["DDD", "AAA", "BBB", "CCC"]
    again = verdict_night(conn, run_id, TODAY, NOW, DEADLINE)
    assert again["done"] == [] and [s["reason"] for s in again["skipped"]] == [research.ALREADY_DONE] * 4
    assert len(model.calls) == 4 and len(rows(conn, "SELECT id FROM verdicts")) == 4


@pytest.mark.parametrize("bad", [{"verdict": "strong buy"}, {"conviction": "total"}, {"summary": ""}])
def test_an_answer_outside_the_enums_fails_that_stock_only(conn, model, pages, bad):
    stocks(conn)
    model.answers["verdict"] = lambda prompt: verdict_answer(**(bad if "AAA" in prompt else {}))
    result = verdict_night(conn, night(conn), TODAY, NOW, DEADLINE)
    assert result["failed"] == [{"symbol": "AAA", "error": "AI vastus on vigane (hinnang puudub)"}]
    assert result["done"] == ["DDD", "BBB", "CCC"]


def test_the_usage_limit_stops_the_rest(conn, model, pages):
    stocks(conn)

    def limited(prompt):
        if len(model.calls) == 3:
            raise runner.AIUnavailable("Claude'i võti ei kehti või on aegunud (Seaded → Ühendused)")
        return verdict_answer()
    model.answers["verdict"] = limited
    result = verdict_night(conn, night(conn), TODAY, NOW, DEADLINE)
    assert result["done"] == ["DDD", "AAA"] and result["stopped"].startswith("Claude'i võti")
    assert [s["symbol"] for s in result["skipped"]] == ["BBB", "CCC"]
    assert rows(conn, "SELECT status FROM stock_ai_runs ORDER BY id")[-1] == {"status": "unavailable"}


def test_past_the_deadline_no_calls(conn, model, pages):
    stocks(conn)
    result = verdict_night(conn, night(conn), TODAY, DEADLINE + timedelta(minutes=1), DEADLINE)
    assert result["stopped"] == research.DEADLINE_REASON and len(result["skipped"]) == 4 and model.calls == []


def test_the_research_note_the_call_sees_is_the_latest_up_to_today(conn, model, pages):
    iid = add_stock(conn, "NVDA", "NVIDIA Corporation")
    model.answers["discover"] = {"candidates": []}
    model.answers["research"] = research_answer(citations=[], bull=[], bear=[], summary="Värske märge.")
    research.research(conn, iid, TODAY)
    model.answers["verdict"] = verdict_answer()
    verdict_night(conn, night(conn), TODAY, NOW, DEADLINE)
    assert "Värske märge." in model.of("verdict")[0]["prompt"]
