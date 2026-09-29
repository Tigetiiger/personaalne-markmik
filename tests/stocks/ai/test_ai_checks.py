"""The pure checks: anonymising and batching for extraction, the citation cross-check, Avasta's
idea checks, and taking the owner's position out of text bound for a search (ported from
stock-oracle's unit tests where they exist)."""

from decimal import Decimal

import pytest

from app.stocks import explore, research
from app.stocks.research import anonymise, cross_check_citations, make_batches, name_variants, scrub


@pytest.mark.parametrize(("name", "expected"), [
    ("NVIDIA Corporation", ["NVIDIA Corporation", "NVIDIA"]),
    ("Apple Inc.", ["Apple Inc.", "Apple"]),
    ("Alphabet Inc. Class A", ["Alphabet Inc. Class A"]),
    ("Berkshire Hathaway Holdings, Inc.", ["Berkshire Hathaway Holdings, Inc.", "Berkshire Hathaway"]),
    ("The Coca-Cola Company", ["The Coca-Cola Company", "Coca-Cola"]),
    ("HP Inc.", ["HP Inc."]),  # "HP" alone is too short to replace safely
    ("ASML Holding N.V.", ["ASML Holding N.V.", "ASML"]),
])
def test_name_variants(name, expected):
    assert name_variants(name) == expected


@pytest.mark.parametrize(("text", "name", "symbol", "expected"), [
    ("Apple Inc. and apple pie", "Apple Inc.", "AAPL", "Company X and Company X pie"),
    ("AAPL, $AAPL and $aapl", "Apple Inc.", "AAPL", "XXXX, $XXXX and $XXXX"),
    ("AAPLE is not a ticker", "Apple Inc.", "AAPL", "AAPLE is not a ticker"),
    ("Turn ON the $ON chips", "onsemi", "ON", "Turn ON the $XXXX chips"),
    ("BRK.B and $BRK.B", "Berkshire Hathaway Inc.", "BRK.B", "XXXX and $XXXX"),
    ("Pineapple Inc", "Apple Inc.", "AAPL", "Pineapple Inc"),
    ("", "Apple Inc.", "AAPL", ""),
])
def test_anonymise(text, name, symbol, expected):
    assert anonymise(text, name, symbol) == expected


def doc(i, size):
    return {"document_id": i, "source": "rss", "published_at": None, "title": "", "text": "x" * size}


def test_batches_split_greedily_in_order():
    batches = make_batches([doc(1, 300), doc(2, 300), doc(3, 300), doc(4, 2000), doc(5, 10)], max_chars=1000)
    assert [[d["document_id"] for d in b] for b in batches] == [[1, 2], [3], [4], [5]]


def test_no_documents_no_batches():
    assert make_batches([], max_chars=1000) == []


def test_batches_also_cap_the_document_count():
    batches = make_batches([doc(i, 10) for i in range(1, 8)], max_chars=100_000, max_docs=3)
    assert [[d["document_id"] for d in b] for b in batches] == [[1, 2, 3], [4, 5, 6], [7]]


def test_many_headlines_are_split_by_count_by_default():
    batches = make_batches([doc(i, 100) for i in range(180)])
    assert max(len(b) for b in batches) == research.BATCH_DOCS == 25


def test_a_batch_holds_at_most_60k_characters():
    batches = make_batches([doc(i, 8000) for i in range(20)])
    assert all(sum(len(d["text"]) for d in b) <= research.BATCH_CHARS for b in batches)


def answer(citations, bull, bear):
    return {"summary": "s", "bull": [{"text": f"bull{i}", "citation": i} for i in bull],
            "bear": [{"text": f"bear{i}", "citation": i} for i in bear],
            "citations": [{"url": url, "title": "t", "quote": "q", "published_at": None} for url in citations]}


def test_citations_are_filtered_and_claims_reindexed():
    kept, bull, bear, dropped_urls, dropped_claims = cross_check_citations(
        answer(["https://a.example", "https://b.example", "https://c.example"], bull=[0, 1, 2, 9], bear=[2, 1]),
        ["https://a.example", "https://c.example/"])
    assert [c["url"] for c in kept] == ["https://a.example", "https://c.example"]
    assert bull == [{"text": "bull0", "citation": 0}, {"text": "bull2", "citation": 1}]
    assert bear == [{"text": "bear2", "citation": 1}]
    assert dropped_urls == ["https://b.example"]
    assert dropped_claims == 3  # bull1, bull9 (no such citation), bear1


def test_nothing_allowed_drops_every_cited_claim():
    kept, bull, bear, _, dropped_claims = cross_check_citations(answer(["https://a.example"], [0], [0]), [])
    assert kept == bull == bear == [] and dropped_claims == 2


def test_citations_match_after_normalising_but_never_as_non_web_links():
    got = cross_check_citations(answer(["http://www.Example.com/a/?utm_source=x#top", "javascript:alert(1)"], [0, 1], []),
                                ["https://example.com/a", "javascript:alert(1)"])
    assert [c["url"] for c in got[0]] == ["http://www.Example.com/a/?utm_source=x#top"]
    assert got[1] == [{"text": "bull0", "citation": 0}]


def test_a_claim_with_a_boolean_or_text_index_is_dropped():
    data = answer(["https://a.example"], [], [])
    data["bull"] = [{"text": "t", "citation": True}, {"text": "u", "citation": "0"}, {"text": "", "citation": 0}]
    assert cross_check_citations(data, ["https://a.example"])[1] == []


def idea(symbol, *urls, **over):
    return {"symbol": symbol, "name": f"{symbol} Corp", "exchange": "NASDAQ", "kind": "stock", "what_it_is": "Teeb asju.",
            "why_interesting": "Tellimused kasvavad.", "catalysts": [], "risks": [], "horizon": "months",
            "conviction": "low", "links": [{"url": u, "title": "t", "why": "w"} for u in urls], **over}


FETCHED = "https://www.example.com/asml-q2?utm_source=x"


def test_ideas_keep_fetched_links_matched_after_normalising():
    ideas, dropped = explore.check_ideas({"ideas": [idea("asml", "http://example.com/asml-q2/")]}, [FETCHED], [])
    assert [i["symbol"] for i in ideas] == ["ASML"] and dropped == []


def test_unfetched_links_and_ideas_left_without_links_are_dropped():
    ideas, dropped = explore.check_ideas(
        {"ideas": [idea("ASML", FETCHED, "https://made-up.example/claim"), idea("NVO", "https://made-up.example/other")]},
        [FETCHED], [])
    assert [i["symbol"] for i in ideas] == ["ASML"]
    assert [link["url"] for link in ideas[0]["links"]] == [FETCHED]
    assert {"kind": "idea", "symbol": "NVO", "reason": "no verified links"} in dropped
    assert sum(1 for d in dropped if d["kind"] == "link") == 2


def test_non_web_links_are_dropped_even_if_they_match():
    ideas, _ = explore.check_ideas({"ideas": [idea("ASML", "javascript:alert(1)")]}, ["javascript:alert(1)"], [])
    assert ideas == []


def test_followed_symbols_on_any_listing_and_duplicates_are_dropped():
    ideas, dropped = explore.check_ideas(
        {"ideas": [idea("SAP", FETCHED), idea("ASML", FETCHED), idea("asml.as", FETCHED)]}, [FETCHED], ["SAP.DE", "AAPL"])
    assert [i["symbol"] for i in ideas] == ["ASML"]
    assert {"kind": "idea", "symbol": "SAP", "reason": "already followed"} in dropped
    assert {"kind": "idea", "symbol": "asml.as", "reason": "duplicate"} in dropped


def test_an_idea_with_a_bad_symbol_or_kind_is_dropped_and_enums_are_checked():
    ideas, dropped = explore.check_ideas({"ideas": [
        idea("<b>X</b>", FETCHED), idea("NVO", FETCHED, kind="bond"), "not an idea",
        idea("ASML", FETCHED, horizon="forever", conviction="certain",
             catalysts=[{"event": "Q3", "date": "2026-13-01"}, {"event": "", "date": None}]),
    ]}, [FETCHED], [])
    assert [d["reason"] for d in dropped] == ["invalid", "invalid", "invalid"]
    assert ideas[0]["horizon"] is None and ideas[0]["conviction"] is None
    assert ideas[0]["catalysts"] == [{"event": "Q3", "date": None}]


def test_idea_count_is_clamped():
    assert [explore.clamp_count(n) for n in (0, 3, 6, 10, 50, "x")] == [3, 3, 6, 10, 10, 6]


FIGURES = [Decimal("37"), Decimal("3741.81"), Decimal("101.13")]


@pytest.mark.parametrize("sentence", [
    "Sul on kontol LHV Investeerimiskonto 37 aktsiat.",
    "Positsiooni väärtus on 3 741,81 USD.",
    "Soetushind oli 1,234.50 ehk kokku 3741.81.",
    "Keskmine hind 101,13.",
    "Omandatud 2025 37 aktsiat.",
    "Väärtus umbes 3742 dollarit.",
])
def test_sentences_with_the_position_are_taken_out(sentence):
    text = f"Nõudlus kasvab. {sentence} Konkurents tiheneb."
    assert scrub(text, ["LHV Investeerimiskonto"], FIGURES) == "Nõudlus kasvab. Konkurents tiheneb."


def test_other_numbers_stay():
    text = "Tulu kasvas 12,5 % ja kvartal lõpeb 2026-10-31. Q3 tulemused tulevad 18.11."
    assert scrub(text, ["LHV"], FIGURES) == text
