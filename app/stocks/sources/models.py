"""Value types every source adapter emits, and the normalisation that makes them comparable
across sources (URL normalisation, content hash). Ported from stock-oracle's sources/models.py."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class InstrumentRef:
    """What an adapter needs to know to search for one instrument. `ticker` is the instrument's
    own symbol (documents are linked by it); `symbols` its provider_symbols_json."""

    ticker: str
    name: str
    cik: str | None = None
    aliases: tuple[str, ...] = ()
    exchange: str | None = None
    currency: str | None = None
    symbols: dict[str, str | None] = field(default_factory=dict, hash=False, compare=False)

    def symbol_at(self, provider: str) -> str | None:
        """The symbol at a provider, None when that provider does not cover it."""
        return self.symbols[provider] if provider in self.symbols else self.ticker


# Exchange names a US listing may carry: stock-oracle's ("US", "NASDAQ") and Yahoo's codes
# (NMS = Nasdaq GS, NYQ = NYSE, PCX = NYSE Arca, ...).
US_EXCHANGES = frozenset({"US", "NASDAQ", "NYSE", "NYSE ARCA", "NYSE AMERICAN", "AMEX", "BATS", "NMS", "NGM",
                          "NCM", "NYQ", "PCX", "ASE", "BTS", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSEARCA"})


def is_us_listing(ref: InstrumentRef) -> bool:
    """US-listed: by a known exchange name, else by the currency and a ticker without an
    exchange suffix (SAP.DE, BANB.SW are not)."""
    if ref.exchange and ref.exchange.upper() in US_EXCHANGES:
        return True
    if ref.currency:
        return ref.currency.upper() == "USD" and "." not in ref.ticker
    return "." not in ref.ticker


@dataclass(frozen=True)
class InsiderTransaction:
    """One row of a Form 4 non-derivative or derivative table."""

    insider_name: str
    insider_cik: str | None
    # e.g. "Director; Officer (President and CEO)"; None if the filing omits it.
    insider_role: str | None
    security_title: str
    trade_date: date
    # Form 4 transaction code: P purchase, S sale, A grant, M exercise, F tax, G gift...
    code: str
    # "A" acquired or "D" disposed.
    acquired_disposed: str | None
    shares: Decimal
    # None when the filing gives no price (grants, gifts, price only in a footnote).
    price: Decimal | None
    shares_after: Decimal | None
    direct: bool
    derivative: bool
    amendment: bool
    underlying_shares: Decimal | None = None
    exercise_price: Decimal | None = None

    @property
    def kind(self) -> str:
        # Only open-market purchases and sales carry the insider-conviction signal; grants,
        # exercises and tax withholding are compensation noise.
        return {"P": "buy", "S": "sell"}.get(self.code, "other")


@dataclass(frozen=True)
class DocumentIn:
    """A document as fetched, before it has a database id."""

    source: str
    external_id: str
    url: str | None
    title: str | None
    body: str | None
    author: str | None
    published_at: datetime
    instruments: tuple[str, ...]
    language: str | None = None
    meta: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    insider_trades: tuple[InsiderTransaction, ...] = ()

    def __post_init__(self) -> None:
        offset = self.published_at.utcoffset()
        if offset is None:
            raise ValueError(f"published_at must be timezone-aware: {self.published_at!r}")
        if offset != timedelta(0):
            raise ValueError(f"published_at must be UTC: {self.published_at!r}")

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.body)

    @property
    def content_key(self) -> str | None:
        """Content hash for duplicate matching, or None when there is no body: title-only items
        (a filing whose body failed to download, a headline feed) are too generic to be matched
        on text alone."""
        return self.content_hash if normalize_text(self.body) else None

    @property
    def url_hash(self) -> str | None:
        normalized = normalize_url(self.url)
        return sha256(normalized) if normalized else None


@dataclass(frozen=True)
class EarningsItem:
    ticker: str
    day: date
    # "bmo", "amc" or None.
    time_of_day: str | None
    fiscal_period: str | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None
    source: str


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "guccounter",
    "guce_referrer", "guce_referrer_sig", "ncid", "soc_src", "soc_trk", "yptr", ".tsrc", "tsrc", "ocid", "smid",
})


def normalize_url(url: str | None) -> str | None:
    """Canonical form for duplicate detection and the citation check: lower-case scheme and host,
    no "www.", no fragment, no tracking parameters, sorted query, no trailing slash. http and
    https are treated as the same page. None for anything that is not an absolute URL."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.")
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    query = sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
    )
    return urlunsplit(("https", host, parts.path.rstrip("/"), urlencode(query), ""))


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WS.sub(" ", _PUNCT.sub(" ", folded)).strip()


def content_hash(title: str | None, body: str | None) -> str:
    """Stable across whitespace, case, punctuation and Unicode-form differences, so the same wire
    story reformatted by two outlets hashes the same."""
    return sha256(normalize_text(title) + "\n" + normalize_text(body))
