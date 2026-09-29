"""SEC EDGAR filings (ported from stock-oracle's test_edgar.py)."""

from datetime import UTC, datetime
from itertools import pairwise

import httpx
import pytest
from news_helpers import NOW, SINCE, fixture_bytes

from app.stocks.sources.base import RateLimiter, SourceError
from app.stocks.sources.edgar import POLL_FORMS, EdgarSource, filing_time, form4_headline, recent_filings
from app.stocks.sources.form4 import parse_form4
from app.stocks.sources.models import InstrumentRef

UA = "Markmik test@example.com"
NVDA = InstrumentRef(ticker="NVDA", name="NVIDIA")
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1045810"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0001045810.json"


def mock_edgar(http):
    http.bytes(TICKERS, "edgar/company_tickers.json")
    http.bytes(SUBMISSIONS, "edgar/submissions_nvda.json")
    http.bytes(f"{ARCHIVE}/000104581026000078/nvda-20260903.htm", "edgar/8k.htm")
    http.on(f"{ARCHIVE}/000104581026000070/nvda-20260726.htm",
            httpx.Response(200, text="<html><body><p>Quarterly report</p></body></html>"))
    http.on(f"{ARCHIVE}/000104581026000060/nvda-20260820.htm", httpx.Response(500))
    http.bytes(f"{ARCHIVE}/000192109426000970/wk-form4_1757543405.xml", "edgar/form4_sale.xml")
    http.on(f"{ARCHIVE}/000192109426000971/wk-form4a_1757543460.xml", httpx.Response(200, text="<html>not xml"))


def source(client, clock, **kw):
    return EdgarSource(client, user_agent=UA, limiter=RateLimiter.per_second(10, clock=clock, sleep=clock.sleep),
                       sleep=clock.sleep, **kw)


def test_user_agent_with_contact_is_required(hclient):
    with pytest.raises(ValueError, match="User-Agent"):
        EdgarSource(hclient, user_agent="")
    with pytest.raises(ValueError, match="e-mail"):
        EdgarSource(hclient, user_agent="Markmik")


def test_fetch_filings(http, hclient, clock):
    mock_edgar(http)
    docs = source(hclient, clock).fetch(NVDA, SINCE, NOW)

    by_form = {d.meta["form"]: d for d in docs}
    # The 8-K after `now` and the 10-K before `since` are out; the Form 144 is not a wanted form.
    assert sorted(d.external_id for d in docs) == ["0001045810-26-000060", "0001045810-26-000070",
                                                   "0001045810-26-000078", "0001921094-26-000970",
                                                   "0001921094-26-000971"]
    assert "144" not in by_form

    eight_k = next(d for d in docs if d.external_id == "0001045810-26-000078")
    assert eight_k.meta["items"] == ["2.02", "9.01"]
    assert "Item 2.02 Results of Operations" in eight_k.body
    assert "second quarter & fiscal 2027" in eight_k.body
    assert "var x" not in eight_k.body
    assert eight_k.url == f"{ARCHIVE}/000104581026000078/nvda-20260903.htm"
    # acceptanceDateTime 16:05:12 is New York time (EDT, UTC-4).
    assert eight_k.published_at == datetime(2026, 9, 3, 20, 5, 12, tzinfo=UTC)
    assert eight_k.instruments == ("NVDA",)

    form4 = by_form["4"]
    assert form4.title == "NVIDIA 4: HUANG JEN HSUN sold 76,500 shares"
    assert len(form4.insider_trades) == 2
    assert "code S (D) 75000 Common Stock at 181.2345" in form4.body

    # A body that fails to parse or download still yields the filing.
    broken = by_form["4/A"]
    assert "parse_error" in broken.meta and broken.insider_trades == ()
    no_body = next(d for d in docs if d.external_id == "0001045810-26-000060")
    assert no_body.body is None and "body_error" in no_body.meta
    # No acceptanceDateTime: midnight New York on the filing date.
    assert no_body.published_at == datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


def test_poll_forms_and_known_filings_are_skipped(http, hclient, clock):
    mock_edgar(http)
    docs = source(hclient, clock, forms=POLL_FORMS).fetch(NVDA, SINCE, NOW, known=lambda a: a == "0001921094-26-000970")
    assert sorted(d.external_id for d in docs) == ["0001045810-26-000060", "0001045810-26-000078", "0001921094-26-000971"]
    # The known filing's body was not downloaded again.
    assert not http.called(f"{ARCHIVE}/000192109426000970/wk-form4_1757543405.xml")


def test_every_request_carries_the_user_agent(http, hclient, clock):
    mock_edgar(http)
    source(hclient, clock).fetch(NVDA, SINCE, NOW)
    assert http.calls and all(r.headers["User-Agent"] == UA for r in http.calls)


def test_requests_are_paced_at_ten_per_second(http, hclient, clock):
    mock_edgar(http)
    source(hclient, clock).fetch(NVDA, SINCE, NOW)
    # tickers + submissions + 5 bodies, one of them retried after a 500.
    assert len(http.stamps) >= 7
    assert min(b - a for a, b in pairwise(http.stamps)) >= 0.1 - 1e-9


def test_known_cik_skips_ticker_lookup(http, hclient, clock):
    http.json(SUBMISSIONS, {"filings": {"recent": {"accessionNumber": [], "form": []}}})
    assert source(hclient, clock).fetch(InstrumentRef(ticker="NVDA", name="NVIDIA", cik="1045810"), SINCE, NOW) == []
    assert not http.called(TICKERS)


def test_cik_lookup_handles_share_class_dots_and_unknowns(http, hclient, clock):
    http.bytes(TICKERS, "edgar/company_tickers.json")
    edgar = source(hclient, clock)
    assert edgar.cik_for("brk.b") == "0001067983"
    assert edgar.cik_for("AAPL") == "0000320193"
    assert edgar.fetch(InstrumentRef(ticker="VWCE", name="Vanguard FTSE All-World"), SINCE, NOW) == []
    assert len(http.called(TICKERS)) == 1  # the map is downloaded once


def test_malformed_submissions_raise(http, hclient, clock):
    http.json(SUBMISSIONS, {"x": 1})
    with pytest.raises(SourceError, match="submissions"):
        source(hclient, clock).fetch(InstrumentRef(ticker="NVDA", name="NVIDIA", cik="1045810"), SINCE, NOW)


def test_recent_filings_tolerates_ragged_columns():
    rows = recent_filings({"filings": {"recent": {"accessionNumber": ["a", "b", "c"], "form": ["8-K", "4"],
                                                  "note": "not a column"}}})
    assert [r["accessionNumber"] for r in rows] == ["a", "b"]


def test_filing_time_fallbacks():
    # EST in winter.
    assert filing_time({"acceptanceDateTime": "2026-01-15T16:30:00.000Z"}) == datetime(2026, 1, 15, 21, 30, tzinfo=UTC)
    assert filing_time({"acceptanceDateTime": "garbage", "filingDate": "bad"}) is None
    assert filing_time({}) is None


def test_form4_headline_variants():
    assert form4_headline(parse_form4(fixture_bytes("edgar/form4a_derivative.xml"))) == "Amended: Kress Colette bought 500 shares"
    empty = parse_form4("<ownershipDocument><documentType>4</documentType><reportingOwner><reportingOwnerId>"
                        "<rptOwnerName>X</rptOwnerName></reportingOwnerId></reportingOwner></ownershipDocument>")
    assert form4_headline(empty) == "X reported other transactions"
