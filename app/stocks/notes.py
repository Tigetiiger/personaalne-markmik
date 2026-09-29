"""One note per followed stock in the brain repo (docs/AKTSIAD-PLAN.md, "Notes in the brain
repo"), in place of stock-oracle's Obsidian vault: folder `aktsiad`, slug `aktsia-<symbol>`,
title "<SYMBOL> · <name>", tags `aktsia` plus the watchlist's. gitsync exports it like any other
note and gbrain indexes it from there.

The app owns everything above the line `## Minu märkmed` and rewrites it every night: the latest
verdict, the trend, the latest analysis with its citations as links, what changed. From that
heading down the note is the owner's and is kept byte for byte; a note without the heading gets it
at the end. Nothing is written when the app's part came out the same (a rev bump would end the
owner's editor session for nothing), and a note open in an editor (NoteBusy) waits for the next
night. Model text is flattened to one paragraph with its brackets and leading '#' defused, so it
can neither add a heading of its own (least of all a second "Minu märkmed") nor links.
"""

import json
import re

from .. import notes as core
from .. import util
from . import common, market
from .alerts import VERDICT_WORD

FOLDER = "aktsiad"
HEADING = "## Minu märkmed"
HINT = "*Aktsiad uuendab selle märkme ülemist osa (kuni „Minu märkmed“).*"
CREATED, WRITTEN, UNCHANGED, BUSY = "created", "written", "unchanged", "busy"
TREND_WORD = {"uptrend": "tõusutrend", "downtrend": "langustrend", "mixed": "segane"}
CROSS_WORD = {"golden": "kuldne rist", "death": "surmarist"}
CONVICTION = {"low": "madal kindlus", "medium": "keskmine kindlus", "high": "kõrge kindlus"}

_HEADING_RE = re.compile(r"^## Minu märkmed[ \t]*$", re.M)


def slug_for(symbol):
    return util.slugify(f"aktsia-{symbol.lower().replace('.', '-')}")


def write(conn, instrument_id, today):
    """Create or refresh the stock's note. False: the note is open in an editor, retried next night."""
    return update(conn, instrument_id, today) != BUSY


def update(conn, instrument_id, today):
    """write(), telling what happened: CREATED, WRITTEN, UNCHANGED or BUSY."""
    row = common.instrument(conn, instrument_id)
    top = render(conn, row, today)
    tags = ["aktsia", *_watch_tags(conn, instrument_id)]
    note = _find(conn, row)
    if note is None:
        note = core.create(conn, slug_for(row["symbol"]), folder=FOLDER, body=f"{top}{HEADING}\n")
        # The slug comes from the title given; a deleted note may hold the plain one.
        common.set_json(conn, _key(instrument_id), note["id"])
        core.set_tags(conn, note["id"], tags)
        return CREATED
    body = note["body"]
    m = _HEADING_RE.search(body)
    if m is not None and body[:m.start()] == top:
        result = UNCHANGED
    else:
        try:
            core.write_body(conn, note["id"], top + (body[m.start():] if m else f"{HEADING}\n"))
        except core.NoteBusy:
            return BUSY
        result = WRITTEN
    have = json.loads(note["tags_json"] or "[]")
    if missing := [t for t in tags if t.casefold() not in {h.casefold() for h in have}]:
        core.set_tags(conn, note["id"], [*have, *missing])  # the owner's own tags stay
    return result


def render(conn, row, today):
    """The app's part of the note, up to (not including) the heading."""
    iid = row["id"]
    verdicts = conn.execute("SELECT * FROM verdicts WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT 2",
                            (iid,)).fetchall()
    analysis = conn.execute("SELECT * FROM analyses WHERE instrument_id = ? ORDER BY as_of DESC, id DESC LIMIT 1",
                            (iid,)).fetchone()
    trend = market.trend_summary(conn, iid, today)
    lines = [f"# {row['symbol']} · {_text(row['name'])}", "", HINT, ""]

    if verdicts:
        v = verdicts[0]
        lines += [f"**Hinnang:** {VERDICT_WORD[v['verdict']]}, {CONVICTION.get(v['conviction'], v['conviction'])} "
                  f"({_day(v['as_of'])}). {_text(v['summary'])}", ""]
    else:
        lines += ["**Hinnang:** veel pole.", ""]

    if trend is None or trend.get("state") is None:
        lines += ["**Trend:** andmeid on veel vähe.", ""]
    else:
        cross = trend.get("last_cross")
        lines += [f"**Trend:** {TREND_WORD.get(trend['state'], trend['state'])}"
                  + (f"; viimati {CROSS_WORD.get(cross['kind'], cross['kind'])} {_day(cross['date'])}" if cross else "") + ".", ""]

    lines += _analysis(analysis)

    changed = []
    if len(verdicts) == 2 and verdicts[0]["verdict"] != verdicts[1]["verdict"]:
        changed.append(f"- Hinnang: {VERDICT_WORD[verdicts[1]['verdict']]} → {VERDICT_WORD[verdicts[0]['verdict']]}.")
    if analysis is not None and analysis["what_changed"] and _text(analysis["what_changed"]):
        changed.append(f"- {_text(analysis['what_changed'])}")
    lines += ["## Mis muutus", "", *(changed or ["Eelmisest korrast pole midagi olulist muutunud."]), ""]

    dates = [d for d in (verdicts[0]["as_of"] if verdicts else None, analysis["as_of"] if analysis else None) if d]
    if dates:
        lines += [f"*Seis: {_day(max(dates))}*", ""]
    return "\n".join(lines) + "\n"


def _analysis(a):
    if a is None:
        return ["## Analüüs", "", "Analüüsi veel pole.", ""]
    citations = json.loads(a["citations_json"] or "[]")

    def cite(index):
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(citations):
            url = _url(citations[index].get("url"))
            return f" [{index + 1}]({url})" if url else ""
        return ""

    def claims(items):
        out = []
        for item in items:
            if isinstance(item, dict):
                text = _text(item.get("text") or item.get("claim") or "")
                if text:
                    out.append(f"- {text}{cite(item.get('citation'))}")
            elif _text(str(item)):
                out.append(f"- {_text(str(item))}")
        return out

    kind = "Süvaanalüüs" if a["kind"] == "deep" else "Analüüs"
    lines = [f"## {kind} {_day(a['as_of'])}", "", _text(a["summary"]), ""]
    for heading, column in (("Poolt", "bull_json"), ("Vastu", "bear_json"), ("Riskid", "risks_json")):
        if items := claims(json.loads(a[column] or "[]")):
            lines += [f"### {heading}", "", *items, ""]
    sources = []
    for n, c in enumerate(citations, start=1):
        if url := _url(c.get("url")):
            title = _text(c.get("title") or "") or url
            when = f", {_day(c['published_at'])}" if c.get("published_at") else ""
            sources.append(f"{n}. [{title}]({url}){when}")
    if sources:
        lines += ["### Allikad", "", *sources, ""]
    return lines


def _find(conn, row):
    note = core.by_slug(conn, slug_for(row["symbol"]))
    if note is None and (note_id := common.get_json(conn, _key(row["id"]))):
        note = core.get(conn, note_id)
    return note


def _key(instrument_id):
    return f"stocks:note:{instrument_id}"


def _watch_tags(conn, instrument_id):
    row = conn.execute("SELECT tags_json FROM watchlist WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return [t for t in json.loads(row["tags_json"] or "[]") if isinstance(t, str)] if row else []


def _text(text):
    """Model or provider text as one safe paragraph."""
    text = " ".join(str(text or "").split()).replace("[", "(").replace("]", ")")
    return "\\" + text if text.startswith("#") else text


def _url(url):
    """A link target that cannot break out of the Markdown link: http(s) only, no spaces or parens."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def _day(value):
    """'2026-09-30' or '…Z' -> '30.09.2026'."""
    y, m, d = str(value)[:10].split("-")
    return f"{d}.{m}.{y}"
