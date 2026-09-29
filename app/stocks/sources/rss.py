"""RSS/Atom feeds: Yahoo Finance per ticker, Google News per company name, company IR feeds.
Ported from stock-oracle, with feedparser replaced by the standard library's XML parser.

Yahoo and Google News feeds are undocumented but long-stable:
- https://feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>&region=US&lang=en-US
- https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en
Those two hosts are fixed and fetched with our own HTTP client (pacing, retries). An IR feed's
address is the owner's (Seaded → Aktsiad), so it goes through ics.download: public addresses only,
size and time caps.

Feeds in the wild are often broken (a bare "&", HTML entities, a truncated tail). Like
feedparser, this reads what parsed before the break and says so in the log."""

import html.entities
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from ... import ics
from .base import HttpFetcher, RateLimiter, Sleep, SourceError
from .models import DocumentIn, InstrumentRef
from .text import html_to_text, to_utc

log = logging.getLogger(__name__)

YAHOO_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
USER_AGENT = "Markmik/1.0 (+personal research)"

FEED_ROOTS = frozenset({"rss", "feed", "RDF"})
ENTRY_TAGS = frozenset({"item", "entry"})
# Where an entry's time may be, best first (RSS pubDate, Atom, Dublin Core).
TIME_TAGS = ("pubDate", "published", "issued", "date", "updated", "modified", "created")
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

FeedUrls = Callable[[InstrumentRef], Sequence[str]]


def parse_time(raw: str | None) -> tuple[datetime | None, bool]:
    """(UTC time, whether the time zone had to be assumed) from an RFC 822 or ISO 8601 text."""
    if not raw or not raw.strip():
        return None, False
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None, False
    return to_utc(parsed), parsed.tzinfo is None


def _local(tag: Any) -> str:
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _xml_entities(text: str) -> str:
    """HTML's named entities (&nbsp;) as numeric ones, and a bare "&" as "&amp;": XML knows five
    names and no bare ampersand, feeds use both."""
    def one(m: re.Match) -> str:
        ref = m[1]
        if ref is None:
            return "&amp;"
        name = ref[:-1]
        if ref.startswith("#") or name in ("amp", "lt", "gt", "quot", "apos"):
            return m[0]
        if name in html.entities.name2codepoint:
            return f"&#{html.entities.name2codepoint[name]};"
        return "&amp;" + ref
    return re.sub(r"&(#[0-9]+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)?", one, text)


def _decode(content: bytes) -> str:
    m = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", content)
    try:
        text = content.decode(m[1].decode() if m else "utf-8", errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    # The declaration names the bytes' encoding; the parser gets text.
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("\ufeff"))


def _entries(content: bytes, name: str) -> tuple[list[ET.Element], str | None]:
    """The feed's items or entries, and its language. Items after a break are lost; a document
    that is no feed at all raises SourceError."""
    text = _decode(content)
    if "<!ENTITY" in text:
        raise SourceError(f"{name}: unparseable feed: entity declarations")
    # A plain DOCTYPE (old RSS 0.91) carries nothing we need.
    text = re.sub(r"<!DOCTYPE[^>\[]*>", "", text, count=1)
    if "<!DOCTYPE" in text:
        raise SourceError(f"{name}: unparseable feed: DTD")
    parser = ET.XMLPullParser(events=("start", "end"))
    root: ET.Element | None = None
    language = None
    entries: list[ET.Element] = []
    problem = None
    try:
        parser.feed(_xml_entities(text))
        parser.close()
    except ET.ParseError as exc:
        problem = exc
    try:
        for event, el in parser.read_events():
            if event == "start":
                if root is None:
                    root = el
                    language = el.get(XML_LANG)
                continue
            if _local(el.tag) in ENTRY_TAGS:
                entries.append(el)
            elif _local(el.tag) == "language" and language is None and el.text:
                language = el.text.strip()
    except ET.ParseError as exc:
        problem = exc
    if root is None or _local(root.tag) not in FEED_ROOTS:
        raise SourceError(f"{name}: unparseable feed: {problem or 'not RSS or Atom'}")
    if problem is not None:
        if not entries:
            raise SourceError(f"{name}: unparseable feed: {problem}")
        log.info("%s: feed has errors, using what parsed: %s", name, problem)
    return entries, language


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    # Atom's type="xhtml" puts markup in child elements.
    value = "".join(el.itertext()) if len(el) else el.text
    return value.strip() if value and value.strip() else None


def _fields(entry: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for child in entry:
        tag = _local(child.tag)
        if tag == "link":
            href = child.get("href")
            if _text(child):
                out.setdefault("link", _text(child))
            elif href and child.get("rel", "alternate") == "alternate":
                out.setdefault("link", href.strip())
        elif tag == "author":
            out.setdefault("author", _text(child.find("{*}name")) or _text(child))
        elif tag == "creator":
            out.setdefault("author", _text(child))
        elif tag == "source":
            out.setdefault("publisher", _text(child))
        elif tag in ("encoded", "content"):
            out.setdefault("content", _text(child))
        elif tag in ("description", "summary"):
            out.setdefault("summary", _text(child))
        elif tag in ("guid", "id", "title", *TIME_TAGS):
            out.setdefault(tag, _text(child))
    return out


class RssSource:
    def __init__(self, name: str, client: httpx.Client, feed_urls: FeedUrls, *, limiter: RateLimiter | None = None,
                 sleep: Sleep = time.sleep, strip_publisher_suffix: bool = False, keep_body: bool = True,
                 download: Callable[[str], bytes] | None = None, covers: Callable[[InstrumentRef], bool] | None = None):
        self.name = name
        self.feed_urls = feed_urls
        self.strip_publisher_suffix = strip_publisher_suffix
        self.keep_body = keep_body
        self._covers = covers
        self.http = HttpFetcher(client, limiter or RateLimiter(1.0, sleep=sleep), sleep=sleep,
                                headers={"User-Agent": USER_AGENT})
        self._download = download

    def covers(self, instrument: InstrumentRef) -> bool:
        return self._covers is None or self._covers(instrument)

    def fetch(self, instrument: InstrumentRef, since: datetime, now: datetime) -> list[DocumentIn]:
        docs: list[DocumentIn] = []
        for url in self.feed_urls(instrument):
            content = self._get(url)
            docs.extend(doc for doc in self.parse(content, instrument) if since <= doc.published_at <= now)
        return docs

    def _get(self, url: str) -> bytes:
        if self._download is None:
            return self.http.get(url).content
        self.http.limiter.acquire()
        try:
            return self._download(url)
        except ValueError as exc:
            raise SourceError(f"{self.name}: {exc}") from None  # ics.download's messages name no URL

    def parse(self, content: bytes, instrument: InstrumentRef) -> list[DocumentIn]:
        entries, feed_language = _entries(content, self.name)
        docs = []
        for entry in entries:
            doc = self._document(_fields(entry), instrument, feed_language)
            if doc is not None:
                docs.append(doc)
        return docs

    def _document(self, entry: dict[str, Any], instrument: InstrumentRef, feed_language: str | None) -> DocumentIn | None:
        published, tz_assumed = None, False
        for key in TIME_TAGS:
            published, tz_assumed = parse_time(entry.get(key))
            if published is not None:
                break
        if published is None:
            # An accurate publish time is the one thing we cannot guess.
            log.info("%s: dropping entry without a date", self.name)
            return None
        link = entry.get("link")
        title = html_to_text(entry.get("title")) or None
        publisher = entry.get("publisher")
        if self.strip_publisher_suffix and title and publisher:
            title = title.removesuffix(f" - {publisher}")
        body = None
        if self.keep_body:
            body = html_to_text(entry.get("content")) or html_to_text(entry.get("summary")) or None
        external_id = entry.get("guid") or entry.get("id") or link
        if not external_id or not (title or body):
            return None
        meta: dict[str, Any] = {}
        if publisher:
            meta["publisher"] = publisher
        if tz_assumed:
            meta["tz_assumed"] = "UTC"
        return DocumentIn(source=self.name, external_id=external_id, url=link, title=title, body=body,
                          author=entry.get("author"), published_at=published, instruments=(instrument.ticker,),
                          language=feed_language[:8] if feed_language else None, meta=meta)


def yahoo_rss(client: httpx.Client, **kwargs: Any) -> RssSource:
    return RssSource("yahoo_rss", client, lambda i: [YAHOO_URL.format(ticker=quote_plus(i.symbol_at("yahoo")))],
                     covers=lambda i: i.symbol_at("yahoo") is not None, **kwargs)


def google_news_rss(client: httpx.Client, **kwargs: Any) -> RssSource:
    def urls(instrument: InstrumentRef) -> list[str]:
        query = " OR ".join(f'"{name}"' for name in [instrument.name, *instrument.aliases])
        return [GOOGLE_NEWS_URL.format(query=quote_plus(query))]

    # Google News descriptions only repeat the headline as a link list.
    return RssSource("google_news", client, urls, strip_publisher_suffix=True, keep_body=False, **kwargs)


def ir_rss(client: httpx.Client, feeds: Mapping[str, Sequence[str]], *, download: Callable[[str], bytes] = ics.download,
           **kwargs: Any) -> RssSource:
    """Company investor-relations feeds, configured per ticker (config.settings()["ir_feeds"])."""
    return RssSource("ir_rss", client, lambda i: list(feeds.get(i.ticker, ())), download=download,
                     covers=lambda i: bool(feeds.get(i.ticker)), **kwargs)
