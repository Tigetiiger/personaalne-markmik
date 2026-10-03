"""Templates (docs/ARVUTUS-PLAN.md): every note in the folder "Mallid" (and below) is one; a new note
made from it gets its text with the placeholders filled in."""

import json
import re
from datetime import datetime

from . import notes, util

FOLDER = "Mallid"
WEEKDAYS = ("esmaspäev", "teisipäev", "kolmapäev", "neljapäev", "reede", "laupäev", "pühapäev")
MONTHS = ("jaanuar", "veebruar", "märts", "aprill", "mai", "juuni", "juuli", "august", "september", "oktoober",
          "november", "detsember")
H1 = re.compile(r"^#[ \t]+.*$", re.M)


def is_template(note):
    first = (note["folder"] or "").split("/", 1)[0]
    return first.casefold() == FOLDER.casefold()


def fill(text, now=None):
    """{{kuupäev}} 2026-10-03, {{kuupäev_pikk}} 3. oktoober 2026, {{nädalapäev}} laupäev, {{kell}} 14:05."""
    now = now or datetime.now(util.TALLINN)
    values = {"kuupäev": now.date().isoformat(), "kuupäev_pikk": f"{now.day}. {MONTHS[now.month - 1]} {now.year}",
              "nädalapäev": WEEKDAYS[now.weekday()], "kell": now.strftime("%H:%M")}
    return re.sub(r"\{\{\s*([^{}\s]+)\s*\}\}", lambda m: values.get(m.group(1).casefold(), m.group(0)), text)


def defaults(template, now=None):
    """The new note's title and folder before the owner changes them: the template's own title
    (filled in, with today's date unless it already has one) and its frontmatter key mall_kaust."""
    title = fill(notes.derive_title(template["body"]), now)
    date = (now or datetime.now(util.TALLINN)).date().isoformat()
    if date not in title and "{{" not in notes.derive_title(template["body"]):
        title = f"{title} {date}"
    extra = json.loads(template["frontmatter_extra"] or "{}")
    return {"title": title, "folder": str(extra.get("mall_kaust") or "")}


def make(conn, template, title=None, folder=None, now=None):
    """A new note from the template. ValueError (Estonian) for a bad folder."""
    body = fill(template["body"], now)
    d = defaults(template, now)
    title = (title or "").strip() or d["title"]
    folder = d["folder"] if folder is None else folder
    if H1.search(body):
        body = H1.sub(lambda m: f"# {title}", body, count=1)
    else:
        body = f"# {title}\n\n{body}"
    return notes.create(conn, title, folder=folder, body=body)
