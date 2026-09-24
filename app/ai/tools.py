"""The Abiline's tools: in-process MCP tools the model reaches as mcp__markmik__<name>. Every
registered tool is shown to the model and only these are registered, so only these can run
(docs/spikes/agent-sdk.md §4).

Handlers run on the runner's thread, so each opens its own database connection. Notes, e-mail and
web pages the model reads may carry injected instructions, so the tools that change something are
narrow: at most WRITE_CAP writes per turn, and the only note text the model can add goes to the
note open in the asker's editor, as a proposal the asker accepts or drops in the browser.
"""

import importlib
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from claude_agent_sdk import tool

from .. import db, events, notes, search, util

NOTE_CAP = 20_000  # characters of a note the model reads at once
LIST_CAP = 100
RANGE_DAYS = 400
WRITE_CAP = 3  # loo_markme + loo_sundmus + lisa_markmesse calls per turn
KINDS = ("tund", "kontrolltöö", "kodutöö", "sündmus", "tähtaeg", "isiklik")
NAMES = ("otsi_markmeid", "loe_markmet", "loo_markme", "lisa_markmesse", "sundmused", "loo_sundmus",
         "ulesanded", "aju_otsing")


class Context:
    """One chat turn: where the database is, the note open in the asker's editor (a Row or None),
    and what the tools did that the browser must hear about."""

    def __init__(self, conn, note=None):
        self.path = conn.execute("PRAGMA database_list").fetchone()["file"]
        self.note = note
        self.created = []  # (tool input, {id, title, start}) for each event made
        self.proposals = []  # text proposed for the open note
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
        if len(body) > NOTE_CAP:
            body = body[:NOTE_CAP] + "\n\n[... märge on pikem, ülejäänu jäi välja]"
        return _text({"slug": note["slug"], "title": note["title"], "folder": note["folder"], "body": body})

    @tool("loo_markme", "Loo uus märge. body on Markdown ilma pealkirjata; folder nt 'kool/matemaatika'.",
          _schema(["title", "body"], title="pealkiri", body="sisu (Markdown)", folder="kaust (valikuline)"))
    async def loo_markme(args):
        if not ctx.may_write():
            return CAPPED
        try:
            with ctx.db() as conn:
                note = notes.create(conn, args["title"], folder=args.get("folder") or "", body=args["body"])
        except ValueError as exc:
            return _error(str(exc))
        return _text({"slug": note["slug"], "title": note["title"]})

    @tool("lisa_markmesse", "Paku tekst (Markdown) kasutajal lahti oleva märkme lõppu; kasutaja otsustab, "
          "kas see lisatakse. Teisi märkmeid muuta ei saa.",
          _schema(["slug", "text"], slug="lahti oleva märkme slug", text="lisatav tekst"))
    async def lisa_markmesse(args):
        text = args["text"].strip()
        if ctx.note is None or args["slug"] != ctx.note["slug"]:
            return _error("Teksti saab pakkuda ainult märkmesse, mis kasutajal praegu lahti on.")
        if not text:
            return _error("Tekst on tühi.")
        if not ctx.may_write():
            return CAPPED
        # No server write: the asker's editor shows it and applies it as an ordinary edit, so
        # co-editors see it and Ctrl+Z undoes it (docs/PLAN-OVERVIEW.md §6).
        ctx.proposals.append(text)
        return _text("Ettepanek on kasutajale näidatud; tema otsustab, kas see lisatakse.")

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
        title, kind, at = args["title"].strip(), args.get("kind") or "sündmus", args.get("time") or ""
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

    return [otsi_markmeid, loe_markmet, loo_markme, lisa_markmesse, sundmused, loo_sundmus, ulesanded,
            aju_otsing]
