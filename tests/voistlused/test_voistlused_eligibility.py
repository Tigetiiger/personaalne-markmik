"""app/eligibility.py: facts from titles and pages, the decision in code, the grade's rollover and
the qualifying-round links, on the saved teaduskool CSV."""

from datetime import date
from pathlib import Path

import pytest

from app import eligibility
from app.sources import teaduskool

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "teaduskool"
ROWS = teaduskool.parse((FIXTURES / "kalender.csv").read_bytes())
KEEMIA = (FIXTURES / "pages" / "keemiaolumpiaad.txt").read_text()
TWELFTH = {"klass": 12, "seatud": "2026-09-27"}
SPRING = date(2027, 3, 13)


@pytest.mark.parametrize("title, group, grades, level, kind", [
    ("Matemaatikaolümpiaadi piirkonnavoor 7.-12. klassile (TÜ)", "Olümpiaadid", [7, 12], None, "qualifying"),
    ("Matemaatikaolümpiaadi lõppvoor 7.-8. klassile (TÜ)", "Olümpiaadid", [7, 8], None, "final"),
    ("Bioloogiaolümpiaadi lõppvoor gümnaasiumile (TÜ)", "Olümpiaadid", None, "gümnaasium", "final"),
    ("Bioloogiaviktoriin Siniroheline Maa põhikoolile I voor (TÜ)", "Võistlused", None, "põhikool", None),
    ("Viktoriin majandusest põhikooli 3. astmele ja meeskonnavõistluse Creatlon algus (Ja Eesti; TÜ)", "Võistlused", [7, 9], "põhikool", None),
    ("Majandusolümpiaadi piirkonnavoor gümnaasiumid ja kutsekoolid (JA Eesti)", "Olümpiaadid", None, "gümnaasium", "qualifying"),
    ("Keemiaolümpiaadi elektrooniline koolivoor (TÜ)", "Olümpiaadid", None, None, "qualifying"),
    ("Matemaatikavõistluse NUPUTA lõppvõistlus 5.-7. klassile Põlvamaal  (EMS KMÜ; TÜ)", "Võistlused", [5, 7], None, "final"),
    ("Euroopa tüdrukute matemaatikaolümpiaad EGMO", "Rahvusvahelised", None, None, "international"),
    ("Informaatikaviktoriin KOBRAS I voor (TÜ)", "Võistlused", None, None, None),
    ("Füüsikaviktoriin SPEKTER 8.-12. klassile (TÜ)", "Võistlused", [8, 12], None, None),
    ("Keeleteaduse viktoriin KIIL 5.-8. klassile (TÜ)", "Võistlused", [5, 8], None, None),
])
def test_title_facts(title, group, grades, level, kind):
    assert eligibility.title_facts(title, group) == {"grades": grades, "level": level, "round": kind}


def info(**facts):
    return eligibility.merge({"grades": None, "level": None, "round": None}, {}) | facts


@pytest.mark.parametrize("facts, result, reason, unknown", [
    (info(grades=[7, 8]), "no", "ainult 7.–8. klassile", False),
    (info(grades=[9, 9]), "no", "ainult 9. klassile", False),
    (info(level="gümnaasium"), "fits", None, False),
    (info(level="põhikool"), "no", "ainult põhikoolile", False),
    (info(grades=[10, 12], level="põhikool"), "fits", None, False),  # the grades are the more exact
    (info(round="final", grades=[9, 12]), "qualify", None, False),
    (info(round="international"), "qualify", None, True),
    (info(level="university"), "no", "ainult üliõpilastele", False),
    (info(level="open"), "fits", None, False),
    (info(), "fits", None, True),
    (None, "fits", None, True),
    (info(qualification={"kind": "qualifies", "through": "valikvõistlus"}), "qualify", None, True),
    # A round the title names decides, not the page's words about the whole olympiad.
    (info(round="qualifying", grades=[7, 12], qualification={"kind": "qualifies", "through": "lõppvooru kutsutakse"}),
     "fits", None, False),
    # A final without grades of its own: those of its qualifying round.
    (info(round="final", qualifier={"uid": "u", "title": "Loodusteaduste olümpiaadi piirkonnavoor 7.-8. klassile (TÜ)",
                                    "start": "2027-01-08"}), "no", "eelnev voor on ainult 7.–8. klassile", False),
    (info(round="final", qualifier={"uid": "u", "title": "Bioloogiaolümpiaadi piirkonnavoor põhikoolile (TÜ)",
                                    "start": "2027-03-24"}), "no", "eelnev voor on ainult põhikoolile", False),
    (info(round="final", qualifier={"uid": "u", "title": "Füüsikaolümpiaadi lõppvoor (TÜ)", "start": "2027-04-03"}),
     "qualify", None, True),
])
def test_decide(facts, result, reason, unknown):
    out = eligibility.decide(facts, SPRING, TWELFTH)
    assert (out["result"], out["reason"], out["unknown"], out["grade"]) == (result, reason, unknown, 12)


def test_qualify_names_the_round():
    facts = info(round="final", qualifier={"uid": "voistlus:x", "title": "Keemiaolümpiaadi piirkonnavoor", "start": "2027-01-27"})
    assert eligibility.decide(facts, SPRING, TWELFTH)["through"] == "Keemiaolümpiaadi piirkonnavoor"
    facts = info(qualification={"kind": "qualifies", "through": "Eesti eelvoor"})
    assert eligibility.decide(facts, SPRING, TWELFTH)["through"] == "Eesti eelvoor"


def test_the_grade_goes_up_every_first_of_september():
    assert eligibility.grade_on(TWELFTH, date(2027, 8, 31)) == 12
    assert eligibility.grade_on(TWELFTH, date(2027, 9, 1)) == 13
    assert eligibility.grade_on({"klass": 9, "seatud": "2026-03-01"}, date(2026, 9, 27)) == 10
    assert eligibility.grade_on({"klass": 9, "seatud": "2026-03-01"}, date(2026, 8, 31)) == 9


def test_after_twelfth_grade_no_school_round_fits():
    autumn = date(2027, 10, 1)  # 13th "grade": school is over
    assert eligibility.decide(info(grades=[1, 12]), autumn, TWELFTH)["reason"] == "ainult 1.–12. klassile"
    assert eligibility.decide(info(level="gümnaasium"), autumn, TWELFTH)["reason"] == "ainult gümnaasiumile"
    assert eligibility.decide(info(round="qualifying"), autumn, TWELFTH)["reason"] == "ainult koolinoortele"
    assert eligibility.decide(info(level="university"), autumn, TWELFTH)["result"] == "fits"
    assert eligibility.decide(info(), autumn, TWELFTH)["result"] == "fits"  # a hackathon, say


def test_page_facts_count_only_with_the_pages_own_words():
    quote = "Kooli- ja piirkonnavoorus võistlevad 8.–12. klasside ja lõppvoorus 9.–12. klasside õpilased."
    raw = {**dict.fromkeys(eligibility.FACTS_PROPERTIES), "grade_min": 8, "grade_max": 12, "level": "open",
           "qualification": "qualifies", "qualifies_through": "piirkonnavoor", "registration_by": "school",
           "registration_how": "  Õpetaja   registreerib koolivooru tulemuste põhjal. ", "team": "individual",
           "fee": "tasuta", "where": "Tartu", "requirements": "x" * 400, "facts_quote": quote}
    facts = eligibility.page_facts(raw, KEEMIA)
    assert facts["grades"] == [8, 12] and facts["level"] == "open" and facts["source_quote"] == quote
    assert facts["qualification"] == {"kind": "qualifies", "through": "piirkonnavoor"}
    assert facts["registration"] == {"who": "school", "how": "Õpetaja registreerib koolivooru tulemuste põhjal."}
    assert facts["team"] == {"kind": "individual", "size": None} and len(facts["requirements"]) == 300
    # Not the page's words: what decides is dropped, what is only shown stays.
    invented = eligibility.page_facts({**raw, "facts_quote": "Osaleda võivad kõik 1.–12. klassi õpilased."}, KEEMIA)
    assert invented["grades"] is None and invented["level"] is None and invented["qualification"] is None
    assert invented["source_quote"] is None and invented["fee"] == "tasuta"
    # A level the grades contradict is dropped: the grades are the exact part.
    mixed = eligibility.page_facts({**raw, "level": "põhikool"}, KEEMIA)
    assert mixed["grades"] == [8, 12] and mixed["level"] is None
    assert eligibility.page_facts({**raw, "grade_min": 10, "level": "gümnaasium"}, KEEMIA)["level"] == "gümnaasium"
    # Words and numbers out of range are dropped.
    odd = eligibility.page_facts({**raw, "grade_min": 0, "level": "kõik", "team": "pere", "registration_by": "ema",
                                  "grade_max": True}, KEEMIA)
    assert odd["grades"] is None and odd["level"] is None and odd["team"] is None and odd["registration"]["who"] is None


def test_the_title_wins_over_the_page_for_grades():
    page = eligibility.page_facts({**dict.fromkeys(eligibility.FACTS_PROPERTIES), "grade_min": 8, "grade_max": 12,
                                   "facts_quote": "Kooli- ja piirkonnavoorus võistlevad 8.–12. klasside"}, KEEMIA)
    final = eligibility.merge(eligibility.title_facts("Matemaatikaolümpiaadi lõppvoor 9.-12. klassile (TÜ)"), page)
    assert final["grades"] == [9, 12] and final["round"] == "final"
    plain = eligibility.merge(eligibility.title_facts("Keemiaolümpiaadi lõppvoor (TÜ)"), page)
    assert plain["grades"] == [8, 12]


def links():
    return {title: q["title"] for (title, _), q in eligibility.link_qualifiers(ROWS).items() if _.year >= 2026}


def test_finals_link_to_their_qualifying_round():
    found = links()
    assert found["Keemiaolümpiaadi lõppvoor (TÜ)"] == "Keemiaolümpiaadi piirkonnavoor 8.-12. klassile (TÜ)"
    assert found["Informaatikaolümpiaadi lõppvoor (TÜ)"] == "Informaatikaolümpiaadi eelvoor (TÜ)"
    # Of two piirkonnavoor rounds, the one of the same school level.
    assert found["Bioloogiaolümpiaadi lõppvoor gümnaasiumile (TÜ)"] == "Bioloogiaolümpiaadi piirkonnavoor gümnaasiumile (TÜ)"
    assert found["Bioloogiaolümpiaadi lõppvoor põhikoolile (TÜ)"] == "Bioloogiaolümpiaadi piirkonnavoor põhikoolile (TÜ)"
    assert found["Matemaatikaolümpiaadi lõppvoor 9.-12. klassile (TÜ)"] == "Matemaatikaolümpiaadi piirkonnavoor 7.-12. klassile (TÜ)"
    # An international olympiad: the national one of its subject, its last round before it.
    assert found["Euroopa tüdrukute matemaatikaolümpiaad EGMO"] == "Matemaatikaolümpiaadi lõppvoor 9.-12. klassile (TÜ)"
    assert found["Põhja- ja Baltimaade füüsikaolümpiaad NBPhO (TalTech)"] == "Füüsikaolümpiaadi lõppvoor (TÜ)"
    # Nothing earlier of the same olympiad that season.
    assert "Võistkondlik matemaatikavõistlus Balti tee (TÜ)" not in found


def test_rows_without_a_page_of_their_own_are_not_linked():
    rows = teaduskool.parse(("Olümpiaadid,,,\ntitle,start,end,url\n"
                             "Kunstiolümpiaadi piirkonnavoor,2027-02-06,,\nFilosoofiaolümpiaadi lõppvoor,2027-03-25,,\n").encode())
    assert eligibility.link_qualifiers(rows) == {}
