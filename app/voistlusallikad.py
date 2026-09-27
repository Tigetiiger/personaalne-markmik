"""Known sources the competition finder reads on every run (docs/VOISTLUSED-PLAN.md, part 3; the
research is docs/VOISTLUSED-ALLIKAD.md). Code fetches each with the hardened fetcher and reads it
here, with no model:
- `ics`: the calendar's own dates, through ics.parse_ics;
- `rss`: one item per post. Its post date is not the event's, so app.voistlused hands the item's
  text to the no-tools extraction, as it does a web page;
- `html`: one list page, read by that extraction as a whole.
An item goes further only when its text names a competition (KEYWORDS): a model call costs, and
most items of these feeds are fairs, trainings or info days.
"""

import html.entities
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from icalendar import Calendar

from . import ics
from .sources.base import html_to_text
from .sources.teaduskool import fold
from .util import TALLINN

FETCH_SECONDS = 30
TEXT_CHARS = 40_000
MAX_ITEMS = 100


@dataclass(frozen=True)
class Source:
    key: str
    name: str        # shown on the Postkast card as its origin
    url: str
    kind: str        # "ics" | "rss" | "html"
    interests: tuple  # read when one of these is chosen in Seaded
    must: tuple = ()  # stems an item must also hold


SOURCES = (
    Source("robootika", "robootika.ee", "https://www.robootika.ee/?post_type=tribe_events&ical=1", "ics", ("robootika",)),
    # Mostly research-funding days; the pupils' contests are what counts.
    Source("etag", "ETAg", "https://www.etag.ee/uritus/?ical=1", "ics",
           ("reaalained", "loodusained", "robootika", "humanitaar"), must=("opilas",)),
    Source("miks", "Miks.ee", "https://miks.ee/opilasele/sundmused/feed/", "rss", ("reaalained", "loodusained", "robootika")),
    Source("kesk-eesti", "Kesk-Eesti inkubaator", "https://kesk-eesti.ee/sundmus/feed/", "rss", ("hakatonid",)),
    Source("garage48", "Garage48", "https://garage48.org/events", "html", ("hakatonid",)),
    Source("kyberolympia", "KüberOlümpia", "https://sites.google.com/view/kyberolympia/avaleht", "html",
           ("reaalained", "hakatonid")),
)
BY_KEY = {s.key: s for s in SOURCES}

# Accent-folded stems of words that name a competition.
KEYWORDS = ("voistlus", "olumpiaad", "konkurs", "hakaton", "hakk", "hack", "makeathon", "ideathon", "game jam",
            "ctf", "kyber", "robolahing", "robotex", "folkrace", "sumo", "delta x", "first tech", "first lego", "cansat",
            "viktoriin", "turniir", "challenge", "karikas", "competition", "contest", "rehkendus", "robootikapaev")


def active(interests):
    """The sources that serve at least one of `interests`."""
    return [s for s in SOURCES if set(s.interests) & set(interests)]


def wanted(src, item):
    """The item names a competition (and holds what the source requires)."""
    text = fold(f"{item['title']}\n{item['text']}")
    return any(k in text for k in KEYWORDS) and all(m in text for m in src.must)


def download(url):
    """The hardened fetcher (tests replace this)."""
    return ics.download(url, FETCH_SECONDS)


def read(src, today):
    """The source's items: [{id, title, link, text}], an `ics` one also {start, end (the last day),
    location}. ValueError (Estonian, no URL) when it cannot be had or read."""
    data = download(src.url)
    if src.kind == "ics":
        return ics_items(data, today)
    if src.kind == "rss":
        return rss_items(data)
    return [{"id": src.url, "title": src.name, "link": src.url,
             "text": html_to_text(data.decode("utf-8", errors="replace"))[:TEXT_CHARS]}]


def ics_items(data, today):
    """A calendar's occurrences from today on, within a year."""
    occurrences = ics.parse_ics(data, today, today + timedelta(days=365))
    try:
        urls = {str(c["uid"]): str(c["url"]) for c in Calendar.from_ical(data).walk("VEVENT") if c.get("uid") and c.get("url")}
    except Exception:  # parse_ics has read it already; a link is a nicety
        urls = {}
    items = []
    for o in occurrences[:MAX_ITEMS]:
        if o["start_date"]:
            start = date.fromisoformat(o["start_date"])
            end = date.fromisoformat(o["end_date"]) - timedelta(days=1) if o["end_date"] else start
        else:
            start = _local_day(o["start_at"])
            end = _local_day(o["end_at"]) if o["end_at"] else start
        items.append({"id": o["uid"] + (f"|{o['recurrence_id']}" if o["recurrence_id"] else ""), "title": o["title"],
                      "link": _https(urls.get(o["uid"])), "text": f"{o['description']}\n{o['location']}".strip(),
                      "start": start, "end": max(end, start), "location": o["location"]})
    return items


def rss_items(data):
    """An RSS feed's items, their HTML as text. A feed's own event dates (The Events Calendar's
    and MEC's fields) go into the text, where the extraction reads them."""
    text = data.decode("utf-8", errors="replace")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("RSS-voos on DTD; seda ei loeta.")  # entity tricks have no place in a feed
    try:
        root = ET.fromstring(_xml_entities(text))
    except ET.ParseError:
        raise ValueError("RSS-voog on vigane.") from None
    items = []
    for it in root.iter("item"):
        extra = [f"{_local_name(e.tag)}: {e.text.strip()}" for e in it
                 if e.text and re.search(r"(?i)start|end|date", _local_name(e.tag)) and _local_name(e.tag) != "pubDate"]
        body = next((e.text for e in it if _local_name(e.tag) == "encoded" and e.text), None) or it.findtext("description") or ""
        link = _https(it.findtext("link"))
        # The link, not WordPress's "?p=123" guid: it names the post in source_ref and logs.
        items.append({"id": (link or it.findtext("guid") or it.findtext("title") or "").strip(),
                      "title": " ".join((it.findtext("title") or "").split()), "link": link,
                      "text": "\n".join([*extra, html_to_text(body)])[:TEXT_CHARS]})
        if len(items) == MAX_ITEMS:
            break
    return items


def _xml_entities(text):
    """HTML's named entities (&otilde; &nbsp;) as numeric ones: WordPress feeds use them, XML knows
    only five."""
    def one(m):
        name = m[1]
        if name in ("amp", "lt", "gt", "quot", "apos") or name not in html.entities.name2codepoint:
            return m[0]
        return f"&#{html.entities.name2codepoint[name]};"
    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", one, text)


def _local_name(tag):
    return tag.rpartition("}")[2]


def _https(url):
    url = (url or "").strip()
    return url if re.fullmatch(r"https://[^\s\"'<>]{4,500}", url) else None


def _local_day(utc_text):
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00")).astimezone(TALLINN).date()
