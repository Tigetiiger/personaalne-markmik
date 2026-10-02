"""The Abiline's tools: in-process MCP tools the model reaches as mcp__markmik__<name>. Every
registered tool is shown to the model and only these are registered, so only these can run
(docs/spikes/agent-sdk.md §4).

Handlers run on the runner's thread, so each opens its own database connection. Notes, e-mail and
web pages the model reads may carry injected instructions, so the tools that change something are
narrow: at most WRITE_CAP writes per turn, and a note is only ever changed through a proposal the
asker reviews change by change in the browser. In a note's Abiline that is the note open in the
asker's editor; in the home chat on / (`home`, docs/KODU-PLAN.md) any note the model has read in
this turn, or an addition the server builds itself (lisa_markmesse).
"""

import importlib
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from claude_agent_sdk import tool

from .. import capture, db, events, images, notes, reminders, search, util

NOTE_CAP = 20_000  # characters of a note the model reads at once
LIST_CAP = 100
RANGE_DAYS = 400
WRITE_CAP = 3  # loo_markme + loo_sundmus + muuda_markmet calls per turn
TITLE_CAP = 200  # an event's title (events.MAX_TITLE); the tool takes no description
KINDS = ("tund", "kontrolltöö", "kodutöö", "sündmus", "tähtaeg", "isiklik")
IMAGES_CAP = 4  # pictures loo_markme embeds
DEFAULT_REMINDER_TIME = "09:00"
NAMES = ("otsi_markmeid", "loe_markmet", "loo_markme", "muuda_markmet", "sundmused", "loo_sundmus",
         "ulesanded", "aju_otsing")
HOME_NAMES = (*NAMES, "lisa_markmesse", "loo_ulesanne", "meeldetuletused", "loo_meeldetuletus",
              "muuda_meeldetuletust")
PROPOSAL_TOOLS = ("muuda_markmet", "lisa_markmesse")


class Context:
    """One chat turn: where the database is, the note open in the asker's editor (a Row or None),
    and what the tools did that the browser must hear about."""

    def __init__(self, conn, note=None, *, home=False, images=()):
        self.path = conn.execute("PRAGMA database_list").fetchone()["file"]
        self.note = note
        self.home = home
        self.images = list(images)  # the names of the pictures sent with this message
        self.created = []  # (tool input, {id, title, start}) for each event made
        self.reminders = []  # (tool input, {id, text, remind_at}) for each reminder made or changed
        # (tool input, {slug, title, base, rev, markdown}) for each note text proposed
        self.proposals = []
        self.read = {}  # home: slug -> (body, rev) of the notes loe_markmet showed in this turn
        self.writes = 0

    @contextmanager
    def db(self):
        conn = db.connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def may_write(self):
        self.writes += 1
        return self.writes <= WRITE_CAP


def _text(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _error(message):
    return {"content": [{"type": "text", "text": message}], "is_error": True}


CAPPED = _error(f"Ühe vastusega saab teha kuni {WRITE_CAP} muudatust. Ütle kasutajale, mis jäi tegemata.")


def _schema(required, **props):
    return {"type": "object", "properties": {k: {"type": "string", "description": d} for k, d in props.items()},
            "required": list(required)}


def _clock(text):
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(text)):
        raise ValueError(f"Vigane kellaaeg {text!r}: kasuta kuju HH:MM.")
    return datetime.strptime(str(text), "%H:%M").time()


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def append_to(body, text, under=None):
    """body with text added at the end, or at the end of the section under the heading `under`
    (matched without case and #). A list item right under another one joins that list. ValueError
    (Estonian) when there is no such heading."""
    text = text.strip("\n")
    lines = body.rstrip("\n").split("\n") if body.strip() else []
    end = len(lines)
    if under:
        want = under.strip().lstrip("#").strip().casefold()
        start = None
        fence = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            m = None if fence else _HEADING.match(line)
            if not m:
                continue
            if start is None and m.group(2).casefold() == want:
                start, level = i, len(m.group(1))
            elif start is not None and len(m.group(1)) <= level:
                end = i
                break
        if start is None:
            raise ValueError(f"Pealkirja {under!r} märkmes ei ole.")
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    joins = end > 0 and capture._LIST_ITEM.match(lines[end - 1]) and capture._LIST_ITEM.match(text)
    head, tail = lines[:end], lines[end:]
    while tail and not tail[0].strip():
        tail.pop(0)
    out = head + ([] if joins or not head else [""]) + text.split("\n") + ([""] + tail if tail else [])
    return "\n".join(out) + "\n"


def _day(text):
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        raise ValueError(f"Vigane kuupäev {text!r}: kasuta kuju AAAA-KK-PP.") from None


def build(ctx):
    """The tools for one chat turn, bound to ctx."""

    @tool("otsi_markmeid", "Otsi kasutaja märkmetest sõnade järgi. Tagastab slug, pealkiri, lõik.",
          _schema(["query"], query="otsisõnad"))
    async def otsi_markmeid(args):
        with ctx.db() as conn:
            hits = search.search(conn, args["query"], limit=10)
        for h in hits:
            h["snippet"] = h["snippet"].replace(search.HL_START, "").replace(search.HL_END, "")
        return _text(hits)

    @tool("loe_markmet", "Loe märkme tekst (Markdown) slugi järgi.", _schema(["slug"], slug="märkme slug"))
    async def loe_markmet(args):
        with ctx.db() as conn:
            note = notes.by_slug(conn, args["slug"])
        if note is None:
            return _error("Märget ei leitud.")
        body = note["body"]
        if ctx.home:
            ctx.read[note["slug"]] = (body, note["rev"])
        if len(body) > NOTE_CAP:
            body = body[:NOTE_CAP] + "\n\n[... märge on pikem, ülejäänu jäi välja]"
        return _text({"slug": note["slug"], "title": note["title"], "folder": note["folder"], "body": body})

    markme_schema = _schema(["title", "body"], title="pealkiri", body="sisu (Markdown)", folder="kaust (valikuline)")
    if ctx.home:
        markme_schema["properties"]["images"] = {
            "type": "array", "items": {"type": "string"},
            "description": "sõnumi piltide nimed (<images> seest), mis märkme lõppu lisada (valikuline)"}

    @tool("loo_markme", "Loo uus märge. body on Markdown ilma pealkirjata; folder nt 'kool/matemaatika'."
          + (" images: kasutaja saadetud piltide nimed, need lisatakse märkme lõppu." if ctx.home else ""),
          markme_schema)
    async def loo_markme(args):
        names = args.get("images") or [] if ctx.home else []
        if not isinstance(names, list) or len(names) > IMAGES_CAP or not all(
                isinstance(n, str) and images.NAME.match(n) for n in names):
            listed = f" Selle sõnumi pildid: {', '.join(ctx.images)}." if ctx.images else ""
            return _error(f"images peab olema kuni {IMAGES_CAP} pildi täpne nimi <images> seest.{listed}")
        if not ctx.may_write():
            return CAPPED
        body = args["body"]
        try:
            with ctx.db() as conn:
                known = {r["name"] for r in conn.execute(
                    f"SELECT name FROM images WHERE name IN ({', '.join('?' * len(names))})", names)} if names else set()
                if set(names) - known:
                    return _error("Sellist pilti pole.")
                if names:
                    body = body.rstrip() + "\n\n" + "\n\n".join(f"![]({images.url(n)})" for n in dict.fromkeys(names))
                note = notes.create(conn, args["title"], folder=args.get("folder") or "", body=body)
                if names:
                    images.allow(conn, note["id"], names)
        except ValueError as exc:
            return _error(str(exc))
        return _text({"slug": note["slug"], "title": note["title"]})

    @tool("muuda_markmet", "Paku kasutajal lahti olevale märkmele (<note>) uus tekst: kogu märge Markdownis, "
          "pealkiri ja kõik, mida kasutaja muuta ei palunud, täpselt nagu <note> sees. Kasutaja vaatab "
          "muudatused üle ja otsustab, mis jääb. Teisi märkmeid muuta ei saa.",
          _schema(["slug", "markdown"], slug="lahti oleva märkme slug", markdown="märkme uus tekst tervikuna"))
    async def muuda_markmet(args):
        markdown = args["markdown"].strip()
        if ctx.home:
            return _propose_read(args["slug"], markdown, args)
        if ctx.note is None or args["slug"] != ctx.note["slug"]:
            return _error("Muuta saab ainult märget, mis kasutajal praegu lahti on.")
        if not markdown:
            return _error("Tekst on tühi.")
        # The model saw no more than NOTE_CAP of it (chat.NOTE_CAP): the rest would be dropped.
        if len(ctx.note["body"]) > NOTE_CAP:
            return _error("Märge on tervikuna muutmiseks liiga pikk. Ütle kasutajale, mida ja kus muuta.")
        if not ctx.may_write():
            return CAPPED
        # No server write: the asker reviews it in the browser, and what they keep goes in as
        # ordinary edits, so co-editors see it and Ctrl+Z undoes it (docs/AI-REVIEW-PLAN.md).
        ctx.proposals.append((args, {"slug": ctx.note["slug"], "markdown": markdown}))
        return _text(PROPOSED)

    def _propose_read(slug, markdown, args):
        """Home: a whole new text for a note loe_markmet showed in this turn, against that text."""
        if slug not in ctx.read:
            return _error("Loe märge enne loe_markmet tööriistaga läbi, siis paku uus tekst.")
        base, rev = ctx.read[slug]
        if not markdown:
            return _error("Tekst on tühi.")
        if len(base) > NOTE_CAP:
            return _error("Märge on tervikuna muutmiseks liiga pikk. Kasuta lisa_markmesse või ütle kasutajale, mida muuta.")
        if not ctx.may_write():
            return CAPPED
        with ctx.db() as conn:
            note = notes.by_slug(conn, slug)
        if note is None:
            return _error("Märget ei leitud.")
        ctx.proposals.append((args, {"slug": slug, "title": note["title"], "base": base, "rev": rev, "markdown": markdown}))
        return _text(PROPOSED)

    @tool("sundmused", "Kalendri sündmused vahemikus start..end (kuupäevad AAAA-KK-PP, end kaasa arvatud).",
          _schema(["start", "end"], start="alguskuupäev", end="lõppkuupäev"))
    async def sundmused(args):
        try:
            start, end = _day(args["start"]), _day(args["end"])
        except ValueError as exc:
            return _error(str(exc))
        if end < start or (end - start).days > RANGE_DAYS:
            return _error(f"Vahemik peab olema 0 kuni {RANGE_DAYS} päeva.")
        keys = ("id", "title", "start", "end", "all_day", "kind", "status", "location")
        with ctx.db() as conn:
            found = events.occurrences(conn, start, end + timedelta(days=1))
        return _text([{k: o.get(k) for k in keys} for o in found[:LIST_CAP]])

    @tool("loo_sundmus", f"Lisa kalendrisse sündmus. date AAAA-KK-PP, time HH:MM (puudub = terve päev), "
          f"kind üks: {', '.join(KINDS)}.",
          _schema(["title", "date"], title="pealkiri", date="kuupäev", time="kellaaeg (valikuline)",
                  kind="liik (valikuline)"))
    async def loo_sundmus(args):
        title, kind, at = args["title"].strip()[:TITLE_CAP], args.get("kind") or "sündmus", args.get("time") or ""
        try:
            day = _day(args["date"])
            if not title:
                raise ValueError("Pealkiri on puudu.")
            if kind not in KINDS:
                raise ValueError(f"Tundmatu liik {kind!r}.")
            if at and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
                raise ValueError(f"Vigane kellaaeg {at!r}: kasuta kuju HH:MM.")
            fields = {"title": title, "kind": kind, "status": "kinnitatud", "source": "ai",
                      "note_id": ctx.note["id"] if ctx.note is not None else None}
            if at:
                local = datetime.combine(day, datetime.strptime(at, "%H:%M").time(), util.TALLINN)
                fields.update(all_day=0, start_at=util.iso(local))
                start = local.strftime("%Y-%m-%dT%H:%M")
            else:
                fields.update(all_day=1, start_date=day.isoformat(), end_date=(day + timedelta(days=1)).isoformat())
                start = day.isoformat()
            if not ctx.may_write():
                return CAPPED
            with ctx.db() as conn:
                event_id = events.create(conn, fields)
        except ValueError as exc:
            return _error(str(exc))
        made = {"id": event_id, "title": title, "start": start}
        ctx.created.append((args, made))
        return _text(made)

    @tool("ulesanded", "Märkmete ülesanded (- [ ] ...). from/to (AAAA-KK-PP) piiravad tähtaega; "
          "ilma nendeta tulevad tegemata ülesanded.", _schema([], **{"from": "alates", "to": "kuni"}))
    async def ulesanded(args):
        sql = ("SELECT t.text, t.done, t.due_date, t.due_time, n.slug AS note_slug, n.title AS note_title "
               "FROM tasks t JOIN notes n ON n.id = t.note_id WHERE n.deleted_at IS NULL")
        params = []
        try:
            for key, op in (("from", ">="), ("to", "<=")):
                if args.get(key):
                    sql += f" AND t.due_date {op} ?"
                    params.append(_day(args[key]).isoformat())
        except ValueError as exc:
            return _error(str(exc))
        if not params:
            sql += " AND t.done = 0"
        sql += " ORDER BY t.due_date IS NULL, t.due_date, t.due_time IS NULL, t.due_time, t.id LIMIT ?"
        with ctx.db() as conn:
            rows = [dict(r, done=bool(r["done"])) for r in conn.execute(sql, [*params, LIST_CAP])]
        return _text(rows)

    @tool("aju_otsing", "Otsi kasutaja teadmistebaasist (aju, gbrain). Ainult lugemiseks.",
          _schema(["query"], query="otsisõnad"))
    async def aju_otsing(args):
        try:
            gbrain = importlib.import_module("app.gbrain")
        except ModuleNotFoundError:
            return _error("Aju (gbrain) pole saadaval.")
        with ctx.db() as conn:
            return _text(gbrain.search(args["query"], limit=8, conn=conn))

    common = [otsi_markmeid, loe_markmet, loo_markme, muuda_markmet, sundmused, loo_sundmus, ulesanded,
              aju_otsing]
    if not ctx.home:
        return common

    @tool("lisa_markmesse", "Paku olemasolevale märkmele lisandust: text lisatakse märkme lõppu või pealkirja "
          "under all oleva osa lõppu (nt poe nimekirja uus rida '- munad'). Märget ise ümber kirjutada pole vaja. "
          "Kasutaja vaatab muudatuse üle.",
          _schema(["slug", "text"], slug="märkme slug", text="lisatav Markdown",
                  under="pealkiri, mille alla lisada (valikuline)"))
    async def lisa_markmesse(args):
        text = str(args.get("text") or "").strip()
        if not text:
            return _error("Tekst on tühi.")
        with ctx.db() as conn:
            note = notes.by_slug(conn, args["slug"])
        if note is None:
            return _error("Märget ei leitud. Otsi õige slug otsi_markmeid tööriistaga.")
        try:
            markdown = append_to(note["body"], text, args.get("under") or None)
        except ValueError as exc:
            return _error(str(exc))
        if not ctx.may_write():
            return CAPPED
        ctx.proposals.append((args, {"slug": note["slug"], "title": note["title"], "base": note["body"],
                                     "rev": note["rev"], "markdown": markdown}))
        return _text(PROPOSED)

    @tool("loo_ulesanne", "Lisa ülesanne (- [ ] ...) tänasesse päevikusse. date AAAA-KK-PP = tähtaeg (valikuline).",
          _schema(["text"], text="ülesanne", date="tähtaeg (valikuline)"))
    async def loo_ulesanne(args):
        text = " ".join(str(args.get("text") or "").split())
        try:
            if not text:
                raise ValueError("Ülesanne on tühi.")
            due = f" @{_day(args['date']).isoformat()}" if args.get("date") else ""
        except ValueError as exc:
            return _error(str(exc))
        if not ctx.may_write():
            return CAPPED
        try:
            with ctx.db() as conn:
                out = capture.add_task(conn, text + due, util.local_today())
        except notes.NoteBusy:
            return _error("Tänane päevik on teises aknas lahti; ülesannet ei saanud lisada.")
        return _text(out)

    @tool("meeldetuletused", "Meeldetuletused vahemikus from..to (AAAA-KK-PP, to kaasa arvatud); vaikimisi "
          "täna ja järgmised 30 päeva koos saatmata jäänutega.", _schema([], **{"from": "alates", "to": "kuni"}))
    async def meeldetuletused(args):
        try:
            start = _day(args["from"]) if args.get("from") else util.local_today()
            end = _day(args["to"]) if args.get("to") else start + timedelta(days=30)
        except ValueError as exc:
            return _error(str(exc))
        if end < start or (end - start).days > RANGE_DAYS:
            return _error(f"Vahemik peab olema 0 kuni {RANGE_DAYS} päeva.")
        with ctx.db() as conn:
            found = reminders.items(conn, start, end + timedelta(days=1), missed=not args.get("from"))
        return _text(found[:LIST_CAP])

    @tool("loo_meeldetuletus", f"Saada kasutaja telefoni meeldetuletus (ntfy) kindlal ajal. date AAAA-KK-PP, "
          f"time HH:MM (puudub = {DEFAULT_REMINDER_TIME}); event_id: sündmus, mille kohta see on (valikuline). "
          f"Ei lisa kalendrisse sündmust.",
          _schema(["text", "date"], text="mida meelde tuletada", date="kuupäev", time="kellaaeg (valikuline)",
                  event_id="sündmuse id (valikuline)"))
    async def loo_meeldetuletus(args):
        try:
            at = _local(args["date"], args.get("time") or DEFAULT_REMINDER_TIME)
            event_id = _event_id(args.get("event_id"))
            if event_id is not None:
                with ctx.db() as conn:
                    if events.get(conn, event_id) is None:
                        raise ValueError("Sündmust ei leitud.")
            if not ctx.may_write():
                return CAPPED
            with ctx.db() as conn:
                rid = reminders.create(conn, args.get("text", ""), at, event_id=event_id, source="ai")
                made = reminders.get(conn, rid)
        except ValueError as exc:
            return _error(str(exc))
        made = {k: made[k] for k in ("id", "text", "remind_at")}
        ctx.reminders.append((args, made))
        return _text(made)

    @tool("muuda_meeldetuletust", "Muuda meeldetuletust (id meeldetuletused tööriistast): uus date, time, text "
          "või done ('jah'/'ei').",
          _schema(["id"], id="meeldetuletuse id", date="uus kuupäev", time="uus kellaaeg", text="uus tekst",
                  done="tehtud: jah või ei"))
    async def muuda_meeldetuletust(args):
        try:
            rid = int(args["id"])
        except (TypeError, ValueError):
            return _error("Vigane id.")
        with ctx.db() as conn:
            old = reminders.get(conn, rid)
        if old is None:
            return _error("Meeldetuletust ei leitud.")
        try:
            at = None
            if args.get("date") or args.get("time"):
                at = _local(args.get("date") or old["remind_at"][:10], args.get("time") or old["remind_at"][11:16])
            done = {"jah": True, "ei": False}.get(str(args.get("done") or "").lower()) if args.get("done") else None
            if not ctx.may_write():
                return CAPPED
            with ctx.db() as conn:
                reminders.update(conn, rid, text=args.get("text") or None, remind_at=at, done=done)
                made = reminders.get(conn, rid)
        except ValueError as exc:
            return _error(str(exc))
        made = {k: made[k] for k in ("id", "text", "remind_at", "done")}
        ctx.reminders.append((args, made))
        return _text(made)

    return [*common, lisa_markmesse, loo_ulesanne, meeldetuletused, loo_meeldetuletus, muuda_meeldetuletust]


PROPOSED = "Muudatusettepanek on kasutajale näidatud; tema vaatab muudatused üle ja otsustab, mis jääb."


def _local(day, clock):
    """A reminder's time from the model's local date and clock; not in the past."""
    at = datetime.combine(_day(day), _clock(clock), util.TALLINN)
    if at < datetime.now(util.TALLINN) - timedelta(minutes=1):
        raise ValueError("See aeg on juba möödas.")
    return at


def _event_id(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("Vigane event_id.") from None
