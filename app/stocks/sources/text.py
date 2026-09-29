"""Text and timestamp helpers shared by the adapters (and by the AI package, on fetched pages).
Ported from stock-oracle's sources/text.py."""

import html
import re
from datetime import UTC, datetime, tzinfo
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
                         "article", "blockquote", "pre", "hr"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "template"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


_SPACES = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(markup: str | None) -> str:
    """Plain text from an HTML fragment or page: no scripts or styles, entities decoded, one blank
    line between blocks. "" for empty input."""
    if not markup:
        return ""
    if "<" not in markup:
        text = html.unescape(markup)
    else:
        parser = _TextExtractor()
        parser.feed(markup)
        parser.close()
        text = "".join(parser.parts)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def to_utc(moment: datetime, assume: tzinfo = UTC) -> datetime:
    """UTC version of `moment`; a naive value is taken to be in `assume`."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=assume)
    return moment.astimezone(UTC)


def from_unix(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def parse_iso(value: str | None, assume: tzinfo = UTC) -> datetime | None:
    """An ISO-8601 timestamp (a trailing Z too) as UTC; None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_utc(parsed, assume)
