"""Estonian date and time parsing (app/etdates.py). Reference day: Thursday 2026-09-24."""

from datetime import date

import pytest

from app import etdates

THU = date(2026, 9, 24)
assert THU.weekday() == 3


@pytest.mark.parametrize("text, ref, expected", [
    # absolute
    ("2026-10-03", THU, date(2026, 10, 3)),
    ("3.10", THU, date(2026, 10, 3)),
    ("3.10.", THU, date(2026, 10, 3)),
    ("03.10.2026", THU, date(2026, 10, 3)),
    ("3.10.26", THU, date(2026, 10, 3)),
    ("3.10.2027", THU, date(2027, 10, 3)),
    ("3. oktoobril", THU, date(2026, 10, 3)),
    ("3. oktoober", THU, date(2026, 10, 3)),
    ("3.oktoobriks", THU, date(2026, 10, 3)),
    ("3 okt", THU, date(2026, 10, 3)),
    ("3. Okt. 2027", THU, date(2027, 10, 3)),
    ("24. detsembril 2027", THU, date(2027, 12, 24)),
    ("5. jaanuari", THU, date(2027, 1, 5)),
    ("15. märtsil", THU, date(2027, 3, 15)),
    ("1. mail", THU, date(2027, 5, 1)),
    ("30. septembrini", THU, date(2026, 9, 30)),
    ("7. novembrist", THU, date(2026, 11, 7)),
    # relative words
    ("täna", THU, THU),
    ("homme", THU, date(2026, 9, 25)),
    ("Ülehomme", THU, date(2026, 9, 26)),
    # bare weekday: the next one strictly after ref
    ("reedel", THU, date(2026, 9, 25)),
    ("reede", THU, date(2026, 9, 25)),
    ("laupäeval", THU, date(2026, 9, 26)),
    ("pühapäeval", THU, date(2026, 9, 27)),
    ("esmaspäev", THU, date(2026, 9, 28)),
    ("esmaspäeval", THU, date(2026, 9, 28)),
    ("teisipäeval", THU, date(2026, 9, 29)),
    ("kolmapäevaks", THU, date(2026, 9, 30)),
    ("neljapäeval", THU, date(2026, 10, 1)),
    # järgmisel/tuleval: that weekday in the next calendar week (Mon 2026-09-28 .. Sun 10-04)
    ("järgmisel reedel", THU, date(2026, 10, 2)),
    ("tuleval esmaspäeval", THU, date(2026, 9, 28)),
    ("järgmine neljapäev", THU, date(2026, 10, 1)),
    ("Järgmisel pühapäeval", THU, date(2026, 10, 4)),
    ("järgmisel esmaspäeval", date(2026, 12, 28), date(2027, 1, 4)),
    # no year: within 14 days in the past stays, older rolls to next year
    ("15.09", THU, date(2026, 9, 15)),
    ("10.09", THU, date(2026, 9, 10)),
    ("9.09", THU, date(2027, 9, 9)),
    ("24.09", THU, THU),
    # year rollover both ways
    ("5.01", date(2026, 12, 20), date(2027, 1, 5)),
    ("28.12", date(2027, 1, 3), date(2026, 12, 28)),
    ("reedel", date(2026, 12, 31), date(2027, 1, 1)),
    ("29.02", THU, date(2028, 2, 29)),
    # inside a sentence: the first date
    ("Kontrolltöö on 3. oktoobril, mitte homme", THU, date(2026, 10, 3)),
    # nothing
    ("31.04", THU, None),
    ("31.02.2026", THU, None),
    ("2026-02-30", THU, None),
    ("13.13", THU, None),
    ("2.50 eurot", THU, None),
    ("versioon 1.2.3.4", THU, None),
    ("kell 9.10", THU, None),
    ("reedeõhtu", THU, None),
    ("tere", THU, None),
    ("", THU, None),
])
def test_parse_date(text, ref, expected):
    assert etdates.parse_date(text, ref) == expected


def test_weekday_answers_are_real_weekdays():
    assert [date(2026, 9, d).weekday() for d in (25, 26, 27, 28)] == [4, 5, 6, 0]
    assert date(2026, 10, 2).weekday() == 4 and date(2027, 1, 4).weekday() == 0


@pytest.mark.parametrize("text, expected", [
    ("kell 14.15", "14:15"),
    ("kell 14:15", "14:15"),
    ("kl 9", "09:00"),
    ("kl. 7.30", "07:30"),
    ("Kella 8:05 algab", "08:05"),
    ("14:15", "14:15"),
    ("algus 9:00, lõpp 10:30", "09:00"),
    ("14.15", None),
    ("kell 25", None),
    ("kell 14.75", None),
    ("9:5", None),
    ("3.10", None),
    ("", None),
])
def test_parse_time(text, expected):
    assert etdates.parse_time(text) == expected


def test_find_joins_weekday_date_and_time():
    text = "Kontrolltöö reedel, 3. oktoobril kell 14.15 ruumis 201"
    [m] = etdates.find(text, THU)
    assert (m["date"], m["time"]) == (date(2026, 10, 3), "14:15")
    assert text[m["start"]:m["end"]] == "reedel, 3. oktoobril kell 14.15"


@pytest.mark.parametrize("text, expected", [
    ("homme ja 3.10", [("homme", date(2026, 9, 25), None), ("3.10", date(2026, 10, 3), None)]),
    ("homme kell 9", [("homme kell 9", date(2026, 9, 25), "09:00")]),
    ("kell 9.10 reedel", [("kell 9.10 reedel", date(2026, 9, 25), "09:10")]),
    ("3.10 (laupäev) 14:00", [("3.10 (laupäev) 14:00", date(2026, 10, 3), "14:00")]),
    ("2026-10-03 14:15", [("2026-10-03 14:15", date(2026, 10, 3), "14:15")]),
    ("järgmisel reedel, 2. oktoobril", [("järgmisel reedel, 2. oktoobril", date(2026, 10, 2), None)]),
    ("Koosolek kell 18, ilma kuupäevata", []),
])
def test_find_spans(text, expected):
    found = etdates.find(text, THU)
    assert [(text[m["start"]:m["end"]], m["date"], m["time"]) for m in found] == expected


@pytest.mark.parametrize("text, day, expected", [
    ("reedel 3. oktoobril", date(2026, 10, 3), True),        # 3.10.2026 is a Saturday
    ("laupäeval, 3. oktoobril", date(2026, 10, 3), False),
    ("3. oktoobril (reedel)", date(2026, 10, 3), True),
    ("3. oktoobril", date(2026, 10, 3), False),
    ("reedel", date(2026, 10, 2), False),
    ("reedel", date(2026, 10, 3), True),
    ("järgmisel reedel", date(2026, 10, 2), False),
    ("järgmisel neljapäeval", date(2026, 10, 2), True),
    # only the weekday next to the date counts
    ("Esmaspäeval proov, kontrolltöö laupäeval 3.10", date(2026, 10, 3), False),
    ("Esmaspäeval proov, kontrolltöö reedel 3.10", date(2026, 10, 3), True),
    ("homme", date(2026, 10, 3), False),
])
def test_weekday_conflict(text, day, expected):
    assert etdates.weekday_conflict(text, day) is expected


def test_long_date():
    assert etdates.long_date(THU) == "Neljapäev, 24. september 2026"
    assert etdates.long_date(date(2027, 3, 1)) == "Esmaspäev, 1. märts 2027"
