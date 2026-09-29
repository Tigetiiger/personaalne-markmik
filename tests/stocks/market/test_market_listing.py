"""Search results into instrument rows (listing.py) and market.search across providers."""

import pytest

from app import vault
from app.stocks import listing, market
from app.stocks.providers import FallbackProvider, ProviderError, SymbolMatch


class Source:
    def __init__(self, name, matches=None, error=False):
        self.name, self.matches, self.error = name, matches or [], error

    def search(self, query):
        if self.error:
            raise ProviderError(self.name, "down")
        return self.matches


@pytest.mark.parametrize("symbol, exchange, currency", [("VWCE.DE", "XETRA", "EUR"), ("BANB.SW", "SIX", "CHF"),
                                                        ("NOVO-B.CO", "CPH", "DKK"), ("AAPL", "US", "USD"),
                                                        ("BRK-B", "US", "USD"), ("X.ZZ", "US", "USD")])
def test_guess_listing_from_the_suffix(symbol, exchange, currency):
    listing_ = listing.guess_listing(SymbolMatch(symbol.lower(), "N", "yahoo", kind="EQUITY"))
    assert (listing_.symbol, listing_.exchange, listing_.currency, listing_.kind) == (symbol, exchange, currency, "stock")


@pytest.mark.parametrize("kind, guess", [("ETF", "etf"), ("ETP", "etf"), ("MUTUALFUND", "etf"), ("Common Stock", "stock"), (None, "stock")])
def test_guess_kind(kind, guess):
    assert listing.guess_kind(kind) == guess


def test_finnhub_is_marked_not_covering_non_us_listings():
    assert listing.provider_symbols_for("AAPL", "US") == {}
    assert listing.provider_symbols_for("VWCE.DE", "XETRA") == {"finnhub": None}


def test_search_asks_every_source_first_match_wins_and_drops_futures():
    finnhub = Source("finnhub", [SymbolMatch("AAPL", "APPLE INC", "finnhub")])
    yahoo = Source("yahoo", [SymbolMatch("aapl", "Apple Inc.", "yahoo", kind="EQUITY"),
                             SymbolMatch("SAAPL=F", "Apple futures", "yahoo", kind="FUTURE"),
                             SymbolMatch("APC.DE", "Apple Inc.", "yahoo", kind="EQUITY")])
    matches = listing.search_symbols(FallbackProvider(finnhub, yahoo), "apple")
    assert [(m.symbol, m.source) for m in matches] == [("AAPL", "finnhub"), ("APC.DE", "yahoo")]


def test_search_survives_one_failing_source_but_not_all():
    assert [m.symbol for m in listing.search_symbols([Source("f", error=True), Source("y", [SymbolMatch("X", "X", "y")])], "x")] == ["X"]
    with pytest.raises(ProviderError):
        listing.search_symbols([Source("f", error=True), Source("y", error=True)], "x")


def test_market_search_uses_finnhub_only_when_keyed(conn, fake):
    fake.add("VWCE.DE", [], name="Vanguard FTSE All-World", type="ETF")
    assert [m["symbol"] for m in market.search(conn, "vanguard")] == ["VWCE.DE"]
    assert not [r for r in fake.calls if r.url.host == "finnhub.io"]
    vault.set(conn, "finnhub_key", "test-key")
    # The fake has no Finnhub search: that source fails and Yahoo's answer still comes back.
    assert [m["symbol"] for m in market.search(conn, "vanguard")] == ["VWCE.DE"]
    assert [r.url.path for r in fake.calls if r.url.host == "finnhub.io"] == ["/api/v1/search"]
