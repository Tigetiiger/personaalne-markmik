"""Estonian dates and times in free text, resolved against a reference date. The one parser used by
tasks (`@reede`), quick capture and the e-mail extraction checks (docs/PLAN-OVERVIEW.md §8).

Recognised, case-insensitive:
  dates  2026-10-03 · 3.10 · 3.10. · 03.10.2026 · 3.10.26 · 3. oktoobril · 3 okt 2026 ·
         täna · homme · ülehomme · reedel · järgmisel reedel · tuleval esmaspäeval
  times  kell 14.15 · kl 9 · kella 8:05 · 14:15. A bare "14.15" is not a time: "9.10" is a date.

Resolution against `ref`:
  - a date without a year is its earliest occurrence at most 14 days before ref: a mail about
    something that just happened keeps its date, and "5.01" written in late December is next year;
  - a bare weekday is the next such day strictly after ref;
  - järgmisel/tuleval <weekday> is that weekday in the next calendar week (Monday to Sunday).
"""

import re
from datetime import date, timedelta

WEEKDAY_NAMES = ("esmaspäev", "teisipäev", "kolmapäev", "neljapäev", "reede", "laupäev", "pühapäev")
MONTH_NAMES = ("jaanuar", "veebruar", "märts", "aprill", "mai", "juuni", "juuli", "august",
               "september", "oktoober", "november", "detsember")

PAST_GRACE = timedelta(days=14)

# Case endings attach to the genitive stem: oktoobri-l, oktoobri-ks, reede-ni, esmaspäeva-st.
_ENDINGS = ("", "l", "ks", "st", "ni", "le", "lt", "s", "t", "ga")
_MONTH_STEMS = ("jaanuari", "veebruari", "märtsi", "aprilli", "mai", "juuni", "juuli", "augusti",
                "septembri", "oktoobri", "novembri", "detsembri")
_MONTH_ABBR = ("jaan", "veebr", "märts", "apr", "mai", "juuni", "juuli", "aug", "sept", "okt", "nov",
               "dets")
_WEEKDAY_STEMS = ("esmaspäeva", "teisipäeva", "kolmapäeva", "neljapäeva", "reede", "laupäeva",
                  "pühapäeva")

_MONTHS = {"sep": 9}
for _i, (_nom, _stem, _abbr) in enumerate(zip(MONTH_NAMES, _MONTH_STEMS, _MONTH_ABBR), 1):
    _MONTHS.update({_nom: _i, _abbr: _i, **{_stem + e: _i for e in _ENDINGS}})
_WEEKDAYS = {}
for _i, (_nom, _stem) in enumerate(zip(WEEKDAY_NAMES, _WEEKDAY_STEMS)):
    _WEEKDAYS.update({_nom: _i, **{_stem + e: _i for e in _ENDINGS}})
_RELATIVE = {"täna": 0, "homme": 1, "ülehomme": 2}


def _alt(words):
    # Longest first, so "oktoobril" wins over "okt".
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_DATE_RE = re.compile(rf"""
    (?<![\w.])(?:
        (?P<iso>\d{{4}}-\d{{2}}-\d{{2}})(?!\w)
      | (?P<d>\d{{1,2}})\.(?P<m>\d{{1,2}})(?:\.(?P<y>\d{{4}}|\d{{2}})(?!\d))?(?!\w|\.\d)
      | (?P<dn>\d{{1,2}})\.?\s*(?P<mon>{_alt(_MONTHS)})\.?(?:\s+(?P<yn>(?:19|20)\d{{2}}))?(?!\w)
      | (?P<rel>{_alt(_RELATIVE)})(?!\w)
      | (?:järgmisel|järgmine|järgmiseks|tuleval|tulev|tulevaks)\s+(?P<nwd>{_alt(_WEEKDAYS)})(?!\w)
      | (?P<wd>{_alt(_WEEKDAYS)})(?!\w)
    )""", re.I | re.X)

_TIME_RE = re.compile(
    r"(?<![\w.:])(?:(?:kella|kell|kl)\.?\s*(?P<h>\d{1,2})(?:[.:](?P<mi>\d{2}))?|(?P<h2>\d{1,2}):(?P<mi2>\d{2}))"
    r"(?!\w|[.:]\d)", re.I)

# What may stand between a weekday and its date, or a date and its time: "reedel, 3. oktoobril".
_ADJACENT = re.compile(r"[\s,()]*")


def long_date(day):
    """2026-09-24 -> 'Neljapäev, 24. september 2026' (daily note titles)."""
    return f"{WEEKDAY_NAMES[day.weekday()].capitalize()}, {day.day}. {MONTH_NAMES[day.month - 1]} {day.year}"


def parse_date(text, ref):
    """The first date named in text, or None."""
    found = find(text, ref)
    return found[0]["date"] if found else None


def parse_time(text):
    """The first time in text as 'HH:MM', or None."""
    return next((t for _, _, t in _times(text)), None)


def find(text, ref):
    """Every date in text: [{"start", "end", "date", "time"}], spans into text. A weekday next to an
    explicit date ("reedel, 3. oktoobril") and a time next to a date ("homme kell 9") join that
    date's match; the explicit date wins over the weekday (see weekday_conflict)."""
    times = _times(text)
    found = []
    for m in _date_matches(text, times):
        day = _resolve(m, ref)
        if day is None:
            continue
        item = {"start": m.start(), "end": m.end(), "date": day, "time": None,
                "explicit": _is_explicit(m), "weekday": _weekday_of(m) is not None}
        prev = found[-1] if found else None
        if prev and _adjacent(text, prev["end"], item["start"]) and (
                (prev["weekday"] and item["explicit"]) or (prev["explicit"] and item["weekday"])):
            prev["end"] = item["end"]
            prev["date"] = prev["date"] if prev["explicit"] else item["date"]
            prev["explicit"], prev["weekday"] = True, False
            continue
        found.append(item)
    for start, end, clock in times:
        for item in found:
            if item["time"] is None and (_adjacent(text, item["end"], start) or _adjacent(text, end, item["start"])):
                item.update(time=clock, start=min(item["start"], start), end=max(item["end"], end))
                break
    return [{k: item[k] for k in ("start", "end", "date", "time")} for item in found]


def weekday_conflict(text, day):
    """True when text names a weekday that is not day's weekday. With an explicit date in the text
    only weekdays right next to one count: "esmaspäeval proov, reedel 3. oktoobril" checks reede."""
    matches = list(_date_matches(text, _times(text)))
    explicit = [m for m in matches if _is_explicit(m)]
    for m in matches:
        weekday = _weekday_of(m)
        if weekday is None or weekday == day.weekday():
            continue
        if not explicit or any(_adjacent(text, m.end(), e.start()) or _adjacent(text, e.end(), m.start())
                               for e in explicit):
            return True
    return False


def _times(text):
    out = []
    for m in _TIME_RE.finditer(text):
        h, mi = (m["h"], m["mi"]) if m["h"] is not None else (m["h2"], m["mi2"])
        if int(h) < 24 and int(mi or 0) < 60:
            out.append((m.start(), m.end(), f"{int(h):02d}:{int(mi or 0):02d}"))
    return out


def _date_matches(text, times):
    for m in _DATE_RE.finditer(text):
        # "kell 9.10" is a time, not the 9th of October.
        if not any(s < m.end() and m.start() < e for s, e, _ in times):
            yield m


def _adjacent(text, end, start):
    return end <= start and _ADJACENT.fullmatch(text, end, start) is not None


def _is_explicit(m):
    return bool(m["iso"] or m["d"] or m["dn"])


def _weekday_of(m):
    name = m["wd"] or m["nwd"]
    return _WEEKDAYS[name.lower()] if name else None


def _resolve(m, ref):
    if m["iso"]:
        try:
            return date.fromisoformat(m["iso"])
        except ValueError:
            return None
    if m["d"] or m["dn"]:
        day, month = (int(m["d"]), int(m["m"])) if m["d"] else (int(m["dn"]), _MONTHS[m["mon"].lower()])
        year = m["y"] or m["yn"]
        if year is None:
            return _next_occurrence(month, day, ref)
        try:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        except ValueError:
            return None
    if m["rel"]:
        return ref + timedelta(days=_RELATIVE[m["rel"].lower()])
    if m["nwd"]:
        next_monday = ref + timedelta(days=7 - ref.weekday())
        return next_monday + timedelta(days=_weekday_of(m))
    return ref + timedelta(days=(_weekday_of(m) - ref.weekday() - 1) % 7 + 1)


def _next_occurrence(month, day, ref):
    earliest = ref - PAST_GRACE
    # Several years ahead for 29.02; an impossible day (31.04) never matches.
    for year in range(earliest.year, earliest.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= earliest:
            return candidate
    return None
