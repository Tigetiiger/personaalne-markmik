"""Can the student take part? A competition's participation facts, and the decision made from
them in code (docs/VOISTLUSED-PLAN.md, part 2).

Facts come from a teaduskool row's title ("7.-12. klassile", "gümnaasiumile", "lõppvoor"), from
the competition's page read by the no-tools extraction, and for a web find from the same call
that reads its dates. The model only reports; code keeps what it can check and decides:
- `fits`;
- `qualify`: a final or an international round. The card says which round it goes through,
  and confirming it confirms that round too;
- `no`, with a reason ("ainult 7.–8. klassile"). Postkast lists these apart; they can still be
  confirmed.
Unknown facts fit, marked as unknown. The grade on a competition's date is the grade set in
Seaded, plus one for every 1 September since it was set and before that date. An age limit
("16+") counts from the birth year set in Seaded: the age reached in the competition's year.
Without a birth year it is only shown.
"""

import re
from datetime import date

from .sources.teaduskool import CALENDAR_URL, fold

LEVELS = ("põhikool", "gümnaasium", "open", "university")
LEVEL_GRADES = {"põhikool": (1, 9), "gümnaasium": (10, 12)}
GRADUATED = 13  # the grade after 12th: no school round fits
TEXT_MAX = 200
REQUIREMENTS_MAX = 300
# Subjects that tie an international olympiad to the national one it selects from.
SUBJECTS = ("matemaatika", "fuusika", "keemia", "bioloogia", "informaatika", "geograafia", "astronoomia",
            "filosoofia", "lingvistika", "loodusteadus", "majandus", "tehisaru")

_nullable = lambda t, **kw: {"type": [t, "null"], **kw}  # noqa: E731
FACTS_PROPERTIES = {
    "grade_min": _nullable("integer", description="lowest school grade (1-12) that may take part"),
    "grade_max": _nullable("integer", description="highest school grade (1-12) that may take part"),
    "age_min": _nullable("integer", description="youngest age that may take part, e.g. 16 for '16+'"),
    "age_max": _nullable("integer", description="oldest age that may take part"),
    "level": _nullable("string", enum=[*LEVELS, None],
                       description="põhikool (grades 1-9), gümnaasium (10-12), open (anyone), university"),
    "team": _nullable("string", enum=["individual", "team", None]),
    "team_size": _nullable("string", description="e.g. '2-4', only for teams"),
    "qualification": _nullable("string", enum=["open", "qualifies", None],
                               description="open: anyone who fits may enter; qualifies: only through an earlier round or selection"),
    "qualifies_through": _nullable("string", description="the earlier round or selection, when qualifies"),
    "registration_by": _nullable("string", enum=["self", "school", "teacher", None],
                                 description="who registers the student"),
    "registration_how": _nullable("string", description="how to register, one sentence"),
    "fee": _nullable("string", description="participation fee as the page states it, or null"),
    "where": _nullable("string", description="the place, or 'online'"),
    "requirements": _nullable("string", description="anything else needed to take part, one or two sentences"),
    "facts_quote": _nullable("string", description="the sentence of the page saying who may take part, verbatim"),
}
FACTS_SCHEMA = {"type": "object", "properties": FACTS_PROPERTIES, "required": list(FACTS_PROPERTIES),
                "additionalProperties": False}


def title_facts(title, group=None):
    """What a title says: {grades, level, round, ages}, each possibly None."""
    t = fold(title)
    grades = level = None
    if m := re.search(r"(?<![\d.])(\d{1,2})\.?\s*[-–]\s*(\d{1,2})\.\s*klass", t):
        grades = _grades(int(m[1]), int(m[2]))
    elif m := re.search(r"(?<![\d.–-])(\d{1,2})\.\s*klass", t):
        grades = _grades(int(m[1]), int(m[1]))
    elif "pohikooli 3. ast" in t:  # "põhikooli 3. astmele": grades 7-9
        grades = [7, 9]
    if "gumnaasium" in t or "kutsekool" in t:
        level = "gümnaasium"
    elif "pohikool" in t:
        level = "põhikool"
    elif "uliopilas" in t:
        level = "university"
    return {"grades": grades, "level": level, "round": round_of(title, group), "ages": age_facts(title)}


def age_facts(text):
    """An age limit a text states, [min, max] with None for an open end, or None: "16+",
    "vähemalt 16-aastased", "alates 16. eluaastast", "14–25-aastased", "vanuses 13–21",
    "ages 14-19", "18 and over"."""
    t = fold(text)
    if m := re.search(r"(?<![\d.])(\d{1,2})\.?\s*[-–]\s*(\d{1,2})\.?\s*-?\s*aastas|vanuses\s+(\d{1,2})\s*[-–]\s*(\d{1,2})"
                      r"|ages?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})|(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(?:y\b|years)", t):
        lo, hi = (int(g) for g in [g for g in m.groups() if g][:2])
        return _ages(lo, hi)
    if m := re.search(r"(?<![\d.+])(\d{1,2})\s?\+(?![\d+])|vahemalt\s+(\d{1,2})\s*-?\s*aastas"
                      r"|(\d{1,2})\s+(?:and|or)\s+(?:over|older)|(\d{1,2})\s*-?\s*aastased\s+ja\s+vanemad"
                      r"|alates\s+(\d{1,2})\.?\s*(?:eluaast|aasta\s+vanus)", t):
        return _ages(int(next(g for g in m.groups() if g)), None)
    if m := re.search(r"kuni\s+(\d{1,2})\s*-?\s*aastas|under\s+(\d{1,2})\b", t):
        return _ages(None, int(next(g for g in m.groups() if g)))
    return None


def round_of(title, group=None):
    """'international', 'final', 'qualifying' (eelvoor, koolivoor, piirkonnavoor, a first "I voor")
    or None."""
    if group == "Rahvusvahelised":
        return "international"
    t = fold(title)
    if re.search(r"loppvoor|loppvoistlus|finaal", t):
        return "final"
    if re.search(r"eelvoor|koolivoor|piirkonnavoor|piirkondlik|\bi (?:voor|etapp)\b", t):
        return "qualifying"
    return None


def page_facts(raw, page):
    """The extraction's facts, checked: grades 1-12 in order, known words only, text capped. The
    ones that decide (grades, level, qualification) count only when their quote is the page's own
    words; the rest is only shown."""
    quote = _text(raw.get("facts_quote"), 1000)
    grounded = bool(quote) and _squash(quote) in _squash(page)
    lo, hi = raw.get("grade_min"), raw.get("grade_max")
    age_lo, age_hi = (v if _int(v) else None for v in (raw.get("age_min"), raw.get("age_max")))
    team = raw.get("team") if raw.get("team") in ("individual", "team") else None
    qualification = raw.get("qualification") if raw.get("qualification") in ("open", "qualifies") else None
    grades = _grades(lo, hi) if grounded and _int(lo) and _int(hi) else None
    level = raw.get("level") if grounded and raw.get("level") in LEVELS else None
    if grades and level in LEVEL_GRADES and not _within(grades, LEVEL_GRADES[level]):
        level = None  # "8.-12. klass" and "põhikool" at once: the grades are the exact part
    return {
        "grades": grades,
        "level": level,
        "ages": _ages(age_lo, age_hi) if grounded else None,
        "team": {"kind": team, "size": _text(raw.get("team_size")) if team == "team" else None} if team else None,
        "qualification": {"kind": qualification, "through": _text(raw.get("qualifies_through"))}
        if grounded and qualification else None,
        "registration": {"who": raw.get("registration_by") if raw.get("registration_by") in ("self", "school", "teacher") else None,
                         "how": _text(raw.get("registration_how"))},
        "fee": _text(raw.get("fee")),
        "location": _text(raw.get("where")),
        "requirements": _text(raw.get("requirements"), REQUIREMENTS_MAX),
        "source_quote": quote if grounded else None,
    }


def merge(title, page):
    """One info dict: the title's grades and level win (they name the round; a page speaks of the
    whole olympiad), the page gives the rest."""
    info = {"grades": None, "level": None, "ages": None, "team": None, "qualification": None,
            "registration": None, "fee": None, "location": None, "requirements": None, "source_quote": None,
            "round": None, "qualifier": None, **(page or {})}
    if title.get("grades") or title.get("level"):
        info["grades"], info["level"] = title.get("grades"), title.get("level")
    info["ages"] = title.get("ages") or info["ages"]
    info["round"] = title.get("round")
    return info


def school_year(day):
    return day.year if day.month >= 9 else day.year - 1


def grade_on(setting, day):
    """The student's grade on `day`: the grade set in Seaded, one higher for every 1 September in
    between. setting = {"klass": int, "seatud": "YYYY-MM-DD"}."""
    return setting["klass"] + school_year(day) - school_year(date.fromisoformat(setting["seatud"]))


def decide(info, start, setting):
    """{"result": "fits" | "qualify" | "no", "reason", "unknown", "through", "grade"} for a
    competition starting on `start`."""
    grade = grade_on(setting, start)
    info = info or {}
    out = {"result": "fits", "reason": None, "unknown": False, "through": None, "grade": grade}
    grades, level = info.get("grades"), info.get("level")
    qualifier = info.get("qualifier") or {}
    ages, born = info.get("ages"), setting.get("synniaasta")
    if ages and born:
        age = start.year - born
        if (ages[0] is not None and age < ages[0]) or (ages[1] is not None and age > ages[1]):
            return {**out, "result": "no", "reason": f"vanusepiirang {age_text(ages)}"}
    before = ""
    if not grades and not level and qualifier.get("title"):
        # A final that says nothing of grades is open to those who can enter its qualifying round.
        inherited = title_facts(qualifier["title"])
        grades, level = inherited["grades"], inherited["level"]
        before = "eelnev voor on "
    if grades:
        lo, hi = grades
        if not lo <= grade <= hi:
            span = f"{lo}. klassile" if lo == hi else f"{lo}.–{hi}. klassile"
            return {**out, "result": "no", "reason": f"{before}ainult {span}"}
    elif level in LEVEL_GRADES:
        lo, hi = LEVEL_GRADES[level]
        if not lo <= grade <= hi:
            school = "põhikoolile" if level == "põhikool" else "gümnaasiumile"
            return {**out, "result": "no", "reason": f"{before}ainult {school}"}
    elif level == "university":
        if grade < GRADUATED:
            return {**out, "result": "no", "reason": "ainult üliõpilastele"}
    elif grade >= GRADUATED and info.get("round"):
        return {**out, "result": "no", "reason": "ainult koolinoortele"}
    else:
        out["unknown"] = level != "open" and not ages
    # A round the title names decides; a page speaks of the whole olympiad ("lõppvooru kutsutakse").
    qualification = info.get("qualification") or {}
    if info.get("round") in ("final", "international") or (info.get("round") is None and qualification.get("kind") == "qualifies"):
        return {**out, "result": "qualify", "through": qualifier.get("title") or qualification.get("through")}
    return out


def link_qualifiers(rows):
    """For each final or international teaduskool row, the round it goes through: the latest
    earlier round of the same olympiad in the same season whose grades or level can be the
    final's. The same olympiad: the same page for a national round; for an international one, a
    national olympiad of the same subject. -> {(title, start): row}"""
    links = {}
    for final in rows:
        kind = round_of(final["title"], final["group"])
        if kind not in ("final", "international"):
            continue
        if kind == "final":
            if final["url"] == CALENDAR_URL:  # no page of its own: nothing says which olympiad it is
                continue
            same = [r for r in rows if r["url"] == final["url"] and round_of(r["title"], r["group"]) == "qualifying"]
        else:
            subject = next((s for s in SUBJECTS if s in fold(final["title"])), None)
            same = [r for r in rows if subject and r["group"] == "Olümpiaadid" and subject in fold(r["title"])
                    and round_of(r["title"], r["group"]) in ("qualifying", "final")]
        earlier = [r for r in same if r["start"] < final["start"] and school_year(r["start"]) == school_year(final["start"])
                   and _overlap(title_facts(r["title"]), title_facts(final["title"]))]
        if earlier:
            links[(final["title"], final["start"])] = max(earlier, key=lambda r: r["start"])
    return links


def age_text(ages):
    """[16, None] -> '16+', [14, 25] -> '14–25', [None, 21] -> 'kuni 21'."""
    lo, hi = ages
    return f"{lo}+" if hi is None else f"kuni {hi}" if lo is None else f"{lo}–{hi}"


def _ages(lo, hi):
    ok = lambda v: v is None or 5 <= v <= 99  # noqa: E731
    if (lo is None and hi is None) or not ok(lo) or not ok(hi) or (lo is not None and hi is not None and lo > hi):
        return None
    return [lo, hi]


def _within(inner, outer):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _overlap(a, b):
    ra, rb = _range(a), _range(b)
    return ra is None or rb is None or (ra[0] <= rb[1] and rb[0] <= ra[1])


def _range(facts):
    return facts["grades"] or LEVEL_GRADES.get(facts["level"])


def _grades(lo, hi):
    return [lo, hi] if 1 <= lo <= hi <= 12 else None


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value, limit=TEXT_MAX):
    return " ".join(value.split())[:limit] or None if isinstance(value, str) else None


def _squash(text):
    return " ".join(text.split()).casefold()
