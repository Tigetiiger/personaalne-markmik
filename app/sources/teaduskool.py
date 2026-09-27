"""Olympiads and competitions from the Tartu Ülikooli teaduskool calendar
(https://kalender.teaduskool.ut.ee/), for the competition finder (app/voistlused.py). Code only,
no AI.

The calendar page reads one public CSV: blocks of rows, each block a group name on a line of its
own, then the column line `title,start,end,url`. Dates are ISO, dd.mm.yyyy or dd/mm/yyyy; `end`
is the last day itself, or empty for a one-day event. Titles may hold quoted commas.
"""

import csv
import io
import re
import unicodedata
from datetime import date

from .. import ics

CSV_URL = "https://tk-kalender-files.s3.eu-central-1.amazonaws.com/kalender.csv"
CALENDAR_URL = "https://kalender.teaduskool.ut.ee/"
# Laagrid/sessid (study sessions) and Muu (school holidays) are not competitions.
GROUPS = ("Olümpiaadid", "Võistlused", "Rahvusvahelised")
COLUMNS = ["title", "start", "end", "url"]

# Word stems per interest (app.voistlused.INTERESTS), matched inside the accent-folded, lowercased
# title: "Füüsikaolümpiaadi" holds "fuusika".
KEYWORDS = {
    "reaalained": ("matemaatika", "fuusika", "informaatika", "astronoomia", "astrofuusika", "tehisaru", "kanguru",
                   "nuputa", "naboj", "kobras", "balti tee", "egmo", "rehkendus", "physics", "math"),
    "loodusained": ("keemia", "bioloogia", "geograafia", "geoloogia", "loodus", "eksperimentaalteadus", "ijso",
                    "eoes", "farmaatsia", "ravimiteadus", "keskkond", "chemistry", "biology"),
    "robootika": ("robo", "fll", "lego", "hakaton", "hackathon", "tehnika", "tehnoloogia", "insener", "leiutaja",
                  "tehisaru"),
    "hakatonid": ("hakaton", "hackathon", "hakk", "makeathon", "ideathon", "ctf", "kyber"),
    "humanitaar": ("keel", "kirjandus", "ajalugu", "ajaloo", "filosoofia", "lingvistika", "rahvaluule", "uhiskonna",
                   "vaitlus", "usundi", "kunsti", "inimeseopetus", "majandus", "rahatark", "riigikaitse"),
}


def fetch():
    """The CSV as bytes, through the hardened fetcher. ValueError (Estonian) when it fails."""
    return ics.download(CSV_URL)


def parse(data):
    """Every dated row: [{title, start, end, url, group}], `end` the last day (inclusive), `url` a
    link to open (the calendar page when the row has none). Rows without a readable start are
    left out."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    rows, group, lone = [], None, None
    for cells in csv.reader(io.StringIO(text)):
        title, start, end, url = [c.strip() for c in (cells + [""] * 4)[:4]]
        if [title.lower(), start.lower(), end.lower(), url.lower()] == COLUMNS:
            group, lone = lone, None  # the group's name stands on the line before its column line
            continue
        if title and not (start or end or url):
            lone = title
            continue
        first = parse_date(start)
        if not title or first is None:
            continue
        last = parse_date(end)
        rows.append({"title": " ".join(title.split()), "start": first,
                     "end": last if last is not None and last >= first else first,
                     "url": _link(url), "group": group})
    return rows


def find(rows, interests, today):
    """Of parse()'s rows, those of the competition groups that start today or later and whose
    title matches one of `interests` (keys of KEYWORDS)."""
    stems = [s for key in interests for s in KEYWORDS.get(key, ())]
    return [row for row in rows
            if row["group"] in GROUPS and row["start"] >= today and any(s in fold(row["title"]) for s in stems)]


def parse_date(text):
    """'2026-10-03', '3.10.2026' or '3/10/2026' (day first) -> date, else None."""
    m = (re.fullmatch(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})", text)
         or re.fullmatch(r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})", text))
    if m is None:
        return None
    try:
        return date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return None


def fold(text):
    """Lowercase without accents: 'Füüsikaolümpiaad' -> 'fuusikaolumpiaad'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _link(url):
    # Some rows give a bare host ("www.nbpho.ee"); anything that is not a web address is dropped.
    if re.match(r"(?i)https?://", url):
        return url if re.fullmatch(r"https?://[^\s\"'<>]+", url, re.I) else CALENDAR_URL
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/[^\s\"'<>]*)?", url):
        return "https://" + url
    return CALENDAR_URL
