"""app/sources/teaduskool.py on a saved copy of the real CSV (public data, trimmed) and on small
hand-written ones for the date formats the calendar page also reads."""

from datetime import date
from pathlib import Path

from app import ics
from app.sources import teaduskool

CSV = (Path(__file__).resolve().parent.parent / "fixtures" / "teaduskool" / "kalender.csv").read_bytes()
TODAY = date(2026, 9, 27)


def by_title(rows):
    return {r["title"]: r for r in rows}


def test_groups_switch_at_each_header():
    groups = {r["group"] for r in teaduskool.parse(CSV)}
    assert groups == {"Olümpiaadid", "Laagrid/sessid", "Võistlused", "Rahvusvahelised", "Muu"}
    rows = by_title(teaduskool.parse(CSV))
    assert rows["Informaatika talvine õppesessioon (TÜ)"]["group"] == "Laagrid/sessid"
    assert rows["Talvevaheaeg"]["group"] == "Muu"
    assert rows["Euroopa tüdrukute matemaatikaolümpiaad EGMO"]["group"] == "Rahvusvahelised"


def test_rows_quotes_and_dates():
    rows = by_title(teaduskool.parse(CSV))
    # A comma inside quotes, and doubled quotes inside a quoted title.
    assert "Keskkoolide Eesti meistrivõistlused väitluses, II etapp (Eesti Väitlusselts)" in rows
    assert rows['Matemaatikavõistlus "KÄNGURU" 1.-12. klassile (EMS, TÜ)']["start"] == date(2026, 3, 19)
    kanguru = rows["Matemaatikavõistlus KÄNGURU 1.-12. klassile (EMS ja TÜ)"]
    assert (kanguru["start"], kanguru["end"]) == (date(2027, 3, 18), date(2027, 3, 18))  # empty end: one day
    bio = rows["Bioloogiaolümpiaadi lõppvoor põhikoolile (TÜ)"]
    assert (bio["start"], bio["end"]) == (date(2027, 5, 27), date(2027, 5, 29))  # end is the last day
    # The source has this row ending before it starts: taken as one day.
    philo = rows["Filosoofiaolümpiaadi lõppvoor (TÜ)"]
    assert (philo["start"], philo["end"]) == (date(2027, 3, 25), date(2027, 3, 25))
    # No start date (IJSO): left out.
    assert "Rahvusvaheline loodusteaduste olümpiaad IJSO (TÜ)" not in rows


def test_links():
    rows = by_title(teaduskool.parse(CSV))
    assert rows["Põhja- ja Baltimaade füüsikaolümpiaad NBPhO (TalTech)"]["url"] == "https://www.nbpho.ee"  # a bare host
    assert rows["Sügisvaheaeg"]["url"] == teaduskool.CALENDAR_URL  # none given
    assert rows["Keemia lahtine võistlus (TÜ)"]["url"] == "https://teaduskool.ut.ee/et/olumpiaadid/lahtised/keemia"
    odd = teaduskool.parse('Võistlused,,,\ntitle,start,end,url\nA,2027-01-01,,javascript:alert(1)\nB,2027-01-01,,"https://x.ee/a b"\n')
    assert [r["url"] for r in odd] == [teaduskool.CALENDAR_URL, teaduskool.CALENDAR_URL]


def test_other_date_formats_and_a_bom():
    text = ("﻿,,,\nOlümpiaadid,,,\ntitle,start,end,url\n"
            "Keemia piirkonnavoor,27.01.2027,,https://a.ee\n"
            "Füüsika lõppvoor,3/4/2027,04/04/2027,https://b.ee\n"
            "Vigane kuupäev,31.02.2027,,https://c.ee\n"
            "Tundmatu kuju,2027.01.05,,https://d.ee\n")
    rows = teaduskool.parse(text.encode())
    assert [(r["title"], r["start"], r["end"], r["group"]) for r in rows] == [
        ("Keemia piirkonnavoor", date(2027, 1, 27), date(2027, 1, 27), "Olümpiaadid"),
        ("Füüsika lõppvoor", date(2027, 4, 3), date(2027, 4, 4), "Olümpiaadid"),
    ]


def test_a_title_alone_on_its_line_is_not_a_group():
    # Only a line followed by the column line names a group.
    text = "Võistlused,,,\ntitle,start,end,url\nPooleli rida,,,\nKeemia lahtine võistlus,2026-10-02,,\n"
    [row] = teaduskool.parse(text)
    assert row["group"] == "Võistlused"


def test_find_keeps_future_competitions_of_the_chosen_interests():
    found = by_title(teaduskool.find(CSV, ["reaalained"], TODAY))
    assert "Matemaatikaolümpiaadi piirkonnavoor 7.-12. klassile (TÜ)" in found
    assert "Füüsikaolümpiaadi lõppvoor (TÜ)" in found  # "Füüsika" read as "fuusika"
    assert "Informaatikaviktoriin KOBRAS I voor (TÜ)" in found  # Võistlused
    assert "Euroopa tüdrukute matemaatikaolümpiaad EGMO" in found  # Rahvusvahelised
    assert "Matemaatika õppesessioon (TÜ)" not in found  # Laagrid/sessid: not a competition
    assert "Matemaatika sügisene lahtine võistlus (TÜ)" not in found  # 26.09: yesterday
    assert "Keemia lahtine võistlus (TÜ)" not in found  # another interest
    assert all(r["start"] >= TODAY for r in found.values())
    chem = by_title(teaduskool.find(CSV, ["loodusained"], TODAY))
    assert "Keemia lahtine võistlus (TÜ)" in chem and "Geograafiaolümpiaadi lõppvoor (TÜ)" in chem
    tech = by_title(teaduskool.find(CSV, ["robootika"], TODAY))
    assert "Tehnikaolümpiaadi praktiline voor 10.-12. klassile (TalTech)" in tech
    assert not any("Talvevaheaeg" in t for t in by_title(teaduskool.find(CSV, list(teaduskool.KEYWORDS), TODAY)))
    assert teaduskool.find(CSV, [], TODAY) == []


def test_fetch_uses_the_hardened_fetcher(monkeypatch):
    asked = []
    monkeypatch.setattr(ics, "download", lambda url: asked.append(url) or b"csv")
    assert teaduskool.fetch() == b"csv"
    assert asked == [teaduskool.CSV_URL]
