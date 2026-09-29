"""URL normalisation, content hash, text and time helpers (ported from stock-oracle's
test_models.py)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.stocks.sources.models import DocumentIn, InstrumentRef, content_hash, is_us_listing, normalize_url
from app.stocks.sources.text import html_to_text, parse_iso, to_utc


def doc(**overrides):
    fields = {"source": "test", "external_id": "1", "url": "https://example.com/a", "title": "Title", "body": "Body",
              "author": None, "published_at": datetime(2026, 9, 21, tzinfo=UTC), "instruments": ("NVDA",)}
    fields.update(overrides)
    return DocumentIn(**fields)


def test_published_at_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        doc(published_at=datetime(2026, 9, 21))


def test_published_at_must_be_utc():
    with pytest.raises(ValueError, match="UTC"):
        doc(published_at=datetime(2026, 9, 21, tzinfo=timezone(timedelta(hours=3))))


@pytest.mark.parametrize(("a", "b"), [
    ("https://www.Reuters.com/tech/story/?utm_source=x&utm_medium=y#comments", "http://reuters.com/tech/story"),
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ("https://example.com/a?fbclid=abc&.tsrc=rss", "https://example.com/a"),
    ("https://example.com:443/a", "https://example.com/a"),
])
def test_normalize_url_equivalences(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_keeps_meaningful_differences():
    assert normalize_url("https://example.com/a?id=1") != normalize_url("https://example.com/a?id=2")
    assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"


@pytest.mark.parametrize("url", [None, "", "   ", "not a url", "/relative/path", "https://example.com:99999/a"])
def test_normalize_url_rejects_non_urls(url):
    assert normalize_url(url) is None


def test_url_hash_is_none_without_url():
    assert doc(url=None).url_hash is None
    assert doc(url="https://example.com/a?utm_x=1").url_hash == doc().url_hash


def test_content_hash_ignores_case_whitespace_punctuation_and_unicode_form():
    assert content_hash("Nvidia  Unveils", "New chips!") == content_hash("nvidia unveils", "new chips")
    assert content_hash("ＮＶＩＤＩＡ", None) == content_hash("NVIDIA", "")  # NFKC folds full-width letters
    assert content_hash("Nvidia", "a") != content_hash("Nvidia", "b")


def test_content_hash_handles_non_latin_text():
    assert content_hash("エヌビディア株", "最高値") == content_hash("エヌビディア株 ", "最高値。")
    assert content_hash("Nvidia-Aktie", "über") != content_hash("Nvidia-Aktie", "uber")


def test_content_key_needs_a_body():
    assert doc(body=None).content_key is None
    assert doc(body="  ").content_key is None
    assert doc().content_key == doc().content_hash


def test_html_to_text_drops_markup_scripts_and_entities():
    markup = ("<html><head><style>p{}</style></head><body><p>Hello&nbsp;<b>world</b> &amp; co.</p>"
              "<script>evil()</script><ul><li>one</li><li>two</li></ul></body></html>")
    assert html_to_text(markup) == "Hello world & co.\n\none\n\ntwo"


def test_html_to_text_plain_and_empty():
    assert html_to_text("AT&amp;T  results") == "AT&T results"
    assert html_to_text("") == "" and html_to_text("<p> </p>") == "" and html_to_text(None) == ""


def test_parse_iso_variants():
    assert parse_iso("2026-09-21T07:30:00.000Z") == datetime(2026, 9, 21, 7, 30, tzinfo=UTC)
    assert parse_iso("2026-09-21T10:30:00+03:00") == datetime(2026, 9, 21, 7, 30, tzinfo=UTC)
    # A timezone-less value is taken to be UTC unless told otherwise.
    assert parse_iso("2026-09-21T07:30:00") == datetime(2026, 9, 21, 7, 30, tzinfo=UTC)
    assert parse_iso("nope") is None and parse_iso(None) is None


def test_to_utc_assumes_the_given_zone_for_naive_values():
    assert to_utc(datetime(2026, 9, 21, 12, 0), timezone(timedelta(hours=-4))) == datetime(2026, 9, 21, 16, 0, tzinfo=UTC)


def test_us_listing_by_exchange_currency_or_ticker_suffix():
    assert is_us_listing(InstrumentRef("AAPL", "Apple", exchange="US"))
    assert is_us_listing(InstrumentRef("BRK.B", "Berkshire", exchange="NYSE"))
    assert is_us_listing(InstrumentRef("NVDA", "Nvidia", exchange="NMS", currency="USD"))
    assert not is_us_listing(InstrumentRef("HAG.DE", "Hensoldt", exchange="XETRA"))
    assert not is_us_listing(InstrumentRef("VWCE.DE", "Vanguard", exchange="GER", currency="EUR"))
    assert not is_us_listing(InstrumentRef("NOVO-B.CO", "Novo", exchange="CPH", currency="DKK"))
    assert is_us_listing(InstrumentRef("NVDA", "Nvidia"))
    assert not is_us_listing(InstrumentRef("SAP.DE", "SAP"))


def test_symbol_at_a_provider():
    ref = InstrumentRef("VWCE.DE", "Vanguard", symbols={"finnhub": None, "yahoo": "VWCE.DE"})
    assert ref.symbol_at("finnhub") is None and ref.symbol_at("yahoo") == "VWCE.DE" and ref.symbol_at("other") == "VWCE.DE"
