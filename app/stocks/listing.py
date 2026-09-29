"""Turning a symbol-search match into an instrument row (ported from stock-oracle's
web/listing.py).

Search results don't carry the trading currency, so it is guessed from the exchange suffix (Yahoo
convention, e.g. VWCE.DE) and shown in the add form for the user to correct before anything is
stored.
"""

from dataclasses import dataclass
from typing import Any

from .providers import FallbackProvider, PriceProvider, ProviderError, SymbolMatch

# suffix -> (exchange, currency)
SUFFIXES = {
    "DE": ("XETRA", "EUR"),
    "F": ("FRA", "EUR"),
    "AS": ("AMS", "EUR"),
    "PA": ("PAR", "EUR"),
    "MI": ("MIL", "EUR"),
    "MC": ("MCE", "EUR"),
    "BR": ("BRU", "EUR"),
    "HE": ("HEL", "EUR"),
    "TL": ("TAL", "EUR"),
    "VI": ("VIE", "EUR"),
    "IR": ("ISE", "EUR"),
    "L": ("LSE", "GBP"),
    "SW": ("SIX", "CHF"),
    "ST": ("STO", "SEK"),
    "CO": ("CPH", "DKK"),
    "OL": ("OSL", "NOK"),
    "TO": ("TSX", "CAD"),
}
CURRENCIES = ("USD", "EUR", "GBP", "CHF", "SEK", "DKK", "NOK", "CAD")
US_EXCHANGE = "US"
# Yahoo's quoteType values that are not a stock or a fund one could follow.
NOT_ADDABLE = {"FUTURE", "INDEX", "CURRENCY", "CRYPTOCURRENCY", "OPTION", "MONEYMARKET"}


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    exchange: str
    currency: str
    kind: str  # instruments.kind: stock | etf
    source: str


def provider_symbols_for(symbol: str, exchange: str) -> dict[str, Any]:
    # Finnhub's free tier only covers US listings; an explicit empty entry makes the fallback go
    # straight to Yahoo for everything else.
    if exchange == US_EXCHANGE:
        return {}
    return {"finnhub": None}


def guess_kind(kind: str | None) -> str:
    text = (kind or "").lower()
    if any(word in text for word in ("etf", "etp", "fund")):
        return "etf"
    return "stock"


def guess_exchange(symbol: str) -> tuple[str, str]:
    """(exchange, currency) from the symbol's suffix; a symbol without one is a US listing."""
    _, dot, suffix = symbol.rpartition(".")
    return SUFFIXES.get(suffix, (US_EXCHANGE, "USD")) if dot else (US_EXCHANGE, "USD")


def guess_listing(match: SymbolMatch) -> Listing:
    symbol = match.symbol.strip().upper()
    exchange, currency = guess_exchange(symbol)
    return Listing(symbol, match.name, exchange, currency, guess_kind(match.kind), match.source)


def addable(match: SymbolMatch) -> bool:
    return (match.kind or "").upper() not in NOT_ADDABLE


def search_symbols(providers: list[PriceProvider] | PriceProvider, query: str, limit: int = 12) -> list[SymbolMatch]:
    """Ask every underlying provider: Finnhub finds US stocks, Yahoo finds the EU-listed ETFs
    Finnhub doesn't know. First match per symbol wins."""
    if isinstance(providers, FallbackProvider):
        providers = [providers.primary, providers.fallback]
    elif not isinstance(providers, list):
        providers = [providers]
    seen: dict[str, SymbolMatch] = {}
    errors = 0
    for source in providers:
        try:
            matches = source.search(query)
        except ProviderError:
            errors += 1
            continue
        for match in matches:
            if addable(match):
                seen.setdefault(match.symbol.upper(), match)
    if errors == len(providers):
        raise ProviderError("search", "symbol search failed")
    return list(seen.values())[:limit]
