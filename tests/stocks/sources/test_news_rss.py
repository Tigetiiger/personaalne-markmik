"""RSS and Atom on the standard library's XML parser, against stock-oracle's feedparser fixtures
(ported from its test_rss.py)."""

from datetime import UTC, datetime

import httpx
import pytest
from news_helpers import NOW, SINCE, fixture_bytes

from app.stocks.sources.base import RateLimiter, SourceError
from app.stocks.sources.models import InstrumentRef
from app.stocks.sources.rss import google_news_rss, ir_rss, parse_time, yahoo_rss

NVDA = InstrumentRef(ticker="NVDA", name="NVIDIA", aliases=("Nvidia Corp",))
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline"


def paced(clock):
    return {"limiter": RateLimiter(1.0, clock=clock, sleep=clock.sleep), "sleep": clock.sleep}


def test_yahoo_feed(http, hclient, clock):
    http.bytes(YAHOO, "rss/yahoo_nvda.xml")
    docs = yahoo_rss(hclient, **paced(clock)).fetch(NVDA, SINCE, NOW)
    assert http.calls[-1].url.params["s"] == "NVDA"

    by_id = {d.external_id: d for d in docs}
    # The undated entry and the one before `since` are dropped.
    assert set(by_id) == {"nvidia-intel-partner-123", "nvidia-analysts-456", "tzless-789", "nvidia-rekord"}

    partner = by_id["nvidia-intel-partner-123"]
    assert partner.body == "The two companies will co-develop products."
    assert partner.language == "en-US" and partner.source == "yahoo_rss"
    assert partner.url == "https://finance.yahoo.com/news/nvidia-intel-partner-123.html?.tsrc=rss"

    assert by_id["nvidia-analysts-456"].published_at == datetime(2026, 9, 20, 12, 30, tzinfo=UTC)

    tzless = by_id["tzless-789"]
    assert tzless.published_at == datetime(2026, 9, 19, 10, 15, tzinfo=UTC)
    assert tzless.meta["tz_assumed"] == "UTC"

    german = by_id["nvidia-rekord"]
    assert german.title == "Nvidia-Aktie: Rekordhoch in Frankfurt – Anleger jubeln über KI-Nachfrage"
    assert german.body == "Die Aktie stieg um 4 % — getrieben von der Nachfrage nach KI-Chips."
    assert german.published_at == datetime(2026, 9, 19, 5, 0, tzinfo=UTC)


def test_yahoo_uses_the_yahoo_symbol_and_skips_uncovered(http, hclient, clock):
    http.on(YAHOO, httpx.Response(200, content=b'<rss version="2.0"><channel><title>q</title></channel></rss>'))
    source = yahoo_rss(hclient, **paced(clock))
    source.fetch(InstrumentRef(ticker="VWCE", name="Vanguard", symbols={"yahoo": "VWCE.DE"}), SINCE, NOW)
    assert http.calls[-1].url.params["s"] == "VWCE.DE"
    assert not source.covers(InstrumentRef(ticker="X", name="X", symbols={"yahoo": None}))


def test_google_news_strips_publisher_and_keeps_non_latin_titles(http, hclient, clock):
    http.bytes("https://news.google.com/rss/search", "rss/google_news.xml")
    reuters, nikkei = google_news_rss(hclient, **paced(clock)).fetch(NVDA, SINCE, NOW)
    assert http.calls[-1].url.params["q"] == '"NVIDIA" OR "Nvidia Corp"'
    assert reuters.title == "Nvidia unveils new Rubin chips"
    assert reuters.meta["publisher"] == "Reuters" and reuters.body is None
    assert nikkei.title == "エヌビディア株が最高値を更新" and nikkei.meta["publisher"] == "日本経済新聞"


def test_ir_atom_feed_goes_through_the_guarded_download(hclient, clock):
    asked = []

    def download(url):
        asked.append(url)
        return fixture_bytes("rss/ir_atom.xml")

    source = ir_rss(hclient, {"NVDA": ["https://ir.example.com/feed"]}, download=download, **paced(clock))
    results, updated = source.fetch(NVDA, SINCE, NOW)
    assert asked == ["https://ir.example.com/feed"]
    assert results.title == "NVIDIA Announces Financial Results"
    assert results.body == "Record quarterly revenue of $52.1 billion.\n\nData Center up 60%"
    assert results.author == "NVIDIA Newsroom"
    assert results.url == "https://nvidianews.nvidia.com/news/results-q2-fy27"
    assert results.published_at == datetime(2026, 8, 27, 20, 20, tzinfo=UTC)
    assert results.language == "en"
    assert updated.published_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    # Tickers without configured feeds are not covered and make no requests.
    aapl = InstrumentRef(ticker="AAPL", name="Apple")
    assert not source.covers(aapl) and source.fetch(aapl, SINCE, NOW) == [] and len(asked) == 1


def test_ir_download_refusal_is_a_source_error(hclient, clock):
    def download(url):
        raise ValueError("Aadress ei ole avalik.")

    source = ir_rss(hclient, {"NVDA": ["https://10.0.0.1/feed"]}, download=download, **paced(clock))
    with pytest.raises(SourceError, match="Aadress ei ole avalik") as info:
        source.fetch(NVDA, SINCE, NOW)
    assert "10.0.0.1" not in str(info.value)


def test_partially_malformed_feed_yields_what_parsed(hclient, clock):
    docs = yahoo_rss(hclient, **paced(clock)).parse(fixture_bytes("rss/malformed_partial.xml"), NVDA)
    assert [d.url for d in docs] == ["https://example.com/ok"]
    assert docs[0].title == "Item before the break & more"


@pytest.mark.parametrize("content", [b"", b"<<<>>> not xml", b"\x00\x01binary", b"<html><body>Not found</body></html>",
                                     b'<rss version="2.0"><channel><item><title>cut'])
def test_unparseable_feed_raises(hclient, clock, content):
    with pytest.raises(SourceError, match="unparseable"):
        yahoo_rss(hclient, **paced(clock)).parse(content, NVDA)


def test_entity_declarations_are_refused(hclient, clock):
    bomb = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaaaaaaaaaaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b'<rss version="2.0"><channel><item><title>&b;</title></item></channel></rss>')
    with pytest.raises(SourceError, match="entity"):
        yahoo_rss(hclient, **paced(clock)).parse(bomb, NVDA)


def test_html_entities_latin1_and_plain_doctype(hclient, clock):
    feed = ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" '
            '"http://my.netscape.com/publish/formats/rss-0.91.dtd">\n<rss version="0.91"><channel><title>t</title>'
            '<item><title>Ümber&nbsp;&otilde;htu &amp; AT&T</title><link>https://example.com/a</link>'
            '<pubDate>Sun, 20 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>').encode("latin-1")
    (doc,) = yahoo_rss(hclient, **paced(clock)).parse(feed, NVDA)
    assert doc.title == "Ümber õhtu & AT&T"


def test_http_errors_surface(http, hclient, clock):
    http.on(YAHOO, httpx.Response(404))
    with pytest.raises(SourceError, match="404"):
        yahoo_rss(hclient, **paced(clock)).fetch(NVDA, SINCE, NOW)


def test_entry_without_id_or_text_is_skipped(hclient, clock):
    feed = b"""<rss version="2.0"><channel><title>t</title>
      <item><pubDate>Sun, 20 Sep 2026 12:00:00 GMT</pubDate><description>no link</description></item>
      <item><link>https://example.com/x</link><pubDate>Sun, 20 Sep 2026 12:00:00 GMT</pubDate></item>
    </channel></rss>"""
    assert yahoo_rss(hclient, **paced(clock)).parse(feed, NVDA) == []


def test_dublin_core_date_and_creator(hclient, clock):
    feed = b"""<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>t</title>
      <item><title>A</title><link>https://example.com/a</link><dc:date>2026-09-20T12:00:00+03:00</dc:date>
        <dc:creator>Mari Maasikas</dc:creator><description>short</description>
        <content:encoded><![CDATA[<p>The <b>full</b> text.</p>]]></content:encoded></item>
    </channel></rss>"""
    (doc,) = yahoo_rss(hclient, **paced(clock)).parse(feed, NVDA)
    assert doc.published_at == datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    assert doc.author == "Mari Maasikas" and doc.body == "The full text." and doc.external_id == "https://example.com/a"


def test_parse_time():
    assert parse_time("Sun, 20 Sep 2026 12:00:00 GMT") == (datetime(2026, 9, 20, 12, 0, tzinfo=UTC), False)
    assert parse_time("2026-09-20T12:00:00") == (datetime(2026, 9, 20, 12, 0, tzinfo=UTC), True)
    assert parse_time("whenever") == (None, False)
    assert parse_time(None) == (None, False)


def test_valid_feed_without_entries_is_empty_not_an_error(hclient, clock):
    feed = b'<rss version="2.0"><channel><title>quiet</title></channel></rss>'
    assert yahoo_rss(hclient, **paced(clock)).parse(feed, NVDA) == []
