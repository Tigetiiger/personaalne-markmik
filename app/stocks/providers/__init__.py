"""Market data adapters behind one interface (ported from stock-oracle's providers): Yahoo's chart
JSON for bars, actions, intraday bars and search; Finnhub for US quotes and search when its key
is set; Frankfurter for ECB FX rates. market.py wires them to the database."""

from .fallback import FallbackProvider
from .finnhub import FinnhubProvider
from .frankfurter import FrankfurterFX, FxPoint, rate_on
from .ratelimit import RateLimiter
from .types import (
    DIVIDEND,
    SPLIT,
    Bar,
    CorporateAction,
    InstrumentRef,
    IntradayBar,
    NotCovered,
    PriceProvider,
    ProviderError,
    Quote,
    RateLimited,
    SymbolMatch,
)
from .yahoo import YahooProvider

__all__ = [
    "DIVIDEND", "SPLIT", "Bar", "CorporateAction", "FallbackProvider", "FinnhubProvider", "FrankfurterFX",
    "FxPoint", "InstrumentRef", "IntradayBar", "NotCovered", "PriceProvider", "ProviderError", "Quote",
    "RateLimited", "RateLimiter", "SymbolMatch", "YahooProvider", "rate_on",
]
