"""Avasta: discovery with WebSearch over public facts and tickers, pages fetched by code, a
no-tools write-up, and the idea checks."""

import json

import pytest
from ai_helpers import LONG, TODAY, add_stock, hold, html, rows

from app.ai import runner
from app.stocks import explore

ASML, NOVO = "https://www.asml.com/en/investors/q2", "https://news.example.org/novo"


def idea(symbol, *urls, **over):
    return {"symbol": symbol, "name": f"{symbol} Corp", "exchange": "NASDAQ", "kind": "stock", "what_it_is": "Teeb asju.",
            "why_interesting": "Tellimused kasvavad.", "catalysts": [{"event": "Q3", "date": "2026-10-15"}],
            "risks": ["Tsüklilisus"], "horizon": "years", "conviction": "medium",
            "links": [{"url": u, "title": "Leht", "why": "Näitab tellimusi."} for u in urls], **over}


@pytest.fixture
def followed(conn):
    add_stock(conn, "NVDA", "NVIDIA Corporation")
    add_stock(conn, "SAP.DE", "SAP SE", exchange="GER", currency="EUR")
    held = add_stock(conn, "TSLA", "Tesla Inc", watch=False)
    hold(conn, held)


def script(model, pages):
    model.answers["explore_discover"] = {"candidates": [
        {"url": ASML, "title": "ASML Q2", "symbol": "ASML", "why": "Tellimused."},
        {"url": NOVO, "title": "Novo", "symbol": "NVO", "why": "Ravim."},
        {"url": "https://down.example/x", "title": "Maas", "symbol": "X", "why": "?"}]}
    pages.pages.update({ASML: html("ASML Q2", LONG), NOVO: html("Novo", LONG)})
    model.answers["explore"] = {"market_context": "Turg on rahulik.", "ideas": [
        idea("ASML", ASML, "https://made-up.example/asml"),
        idea("SAP", NOVO),                                 # another listing of a followed stock
        idea("TSLA", ASML),                                # held, though not on the watchlist
        idea("MU", "https://made-up.example/micron"),      # no link that was fetched
        idea("NVO", NOVO, kind="etf", horizon="months"),
    ]}


def test_stores_checked_ideas_and_what_was_dropped(conn, model, pages, followed):
    script(model, pages)
    steps = []

    exploration_id = explore.explore(conn, "  Euroopa   tervishoid ", 6, TODAY, progress=steps.append)

    assert steps == ["Otsin allikaid…", "Loen 1/3 lehte…", "Loen 2/3 lehte…", "Loen 3/3 lehte…", "Kirjutan…"]
    (e,) = rows(conn, "SELECT * FROM explorations")
    assert e["id"] == exploration_id
    assert (e["focus"], e["requested_ideas"], e["market_context"], e["model"]) == ("Euroopa tervishoid", 6, "Turg on rahulik.", "sonnet")
    ideas = json.loads(e["ideas_json"])
    assert [i["symbol"] for i in ideas] == ["ASML", "NVO"]
    assert [link["url"] for link in ideas[0]["links"]] == [ASML]
    assert ideas[1]["kind"] == "etf" and ideas[1]["catalysts"] == [{"event": "Q3", "date": "2026-10-15"}]
    dropped = json.loads(e["dropped_json"])
    assert {"kind": "idea", "symbol": "SAP", "reason": "already followed"} in dropped
    assert {"kind": "idea", "symbol": "TSLA", "reason": "already followed"} in dropped
    assert {"kind": "idea", "symbol": "MU", "reason": "no verified links"} in dropped
    assert {"kind": "link", "symbol": "ASML", "url": "https://made-up.example/asml", "reason": "not fetched"} in dropped
    assert e["prompt_version"] == explore.load("explore").version
    runs = rows(conn, "SELECT id, purpose, instrument_id FROM stock_ai_runs ORDER BY id")
    assert [r["purpose"] for r in runs] == ["discover", "explore"] and e["ai_run_id"] == runs[1]["id"]

    found, wrote = model.calls
    assert found["web_search"] is True and not wrote.get("web_search") and wrote["thinking"] is True
    # Discovery: the focus, the count, the date and the followed tickers; not their names.
    assert "Euroopa tervishoid" in found["prompt"] and "NVDA, SAP.DE, TSLA" in found["prompt"]
    assert "NVIDIA Corporation" not in found["prompt"] and "2026-09-28" in found["prompt"]
    assert "- NVDA (NVIDIA Corporation)" in wrote["prompt"] and "Tellimused kasvavad" not in wrote["prompt"]


def test_nothing_read_means_nothing_written(conn, model, pages, followed):
    model.answers["explore_discover"] = {"candidates": [{"url": "https://down.example/x", "title": "x", "symbol": "X", "why": "?"}]}
    with pytest.raises(RuntimeError, match="Ühtegi lehte ei õnnestunud lugeda"):
        explore.explore(conn, None, 6, TODAY)
    assert rows(conn, "SELECT id FROM explorations") == [] and len(model.calls) == 1


def test_a_failed_write_up_stores_nothing(conn, model, pages, followed):
    script(model, pages)
    model.answers["explore"] = runner.AIUnavailable("Claude'i kasutuspiir on praegu täis, proovi hiljem uuesti")
    with pytest.raises(runner.AIUnavailable):
        explore.explore(conn, None, 6, TODAY)
    assert rows(conn, "SELECT id FROM explorations") == []


def test_the_count_is_clamped_and_an_empty_focus_is_none(conn, model, pages, followed):
    script(model, pages)
    explore.explore(conn, "   ", 50, TODAY)
    (e,) = rows(conn, "SELECT focus, requested_ideas FROM explorations")
    assert e == {"focus": None, "requested_ideas": 10}
    assert "\nnone\n" in model.calls[0]["prompt"]
