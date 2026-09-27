"""Part 3: the known sources (app/voistlusallikad.py) on saved real feeds, the Häkatonid interest,
age limits, the keyword prefilter and the per-run extraction cap (docs/VOISTLUSED-PLAN.md)."""

import logging
from datetime import date
from pathlib import Path

import pytest

from app import eligibility, voistlusallikad, voistlused
from app.voistlusallikad import BY_KEY

FEEDS = Path(__file__).resolve().parent.parent / "fixtures" / "voistlused"
TODAY = date(2026, 9, 27)
COASTHACK = "https://kesk-eesti.ee/sundmus/parnu-coasthack/"


def feed(name):
    return (FEEDS / name).read_bytes()


def proposals(conn):
    return {r["title"]: r for r in conn.execute("SELECT * FROM events WHERE status = 'pakutud' AND uid NOT LIKE '%:registreeri'")}


def only(conn, *interests):
    voistlused.save_settings(conn, {"huvid": list(interests), "markus": "", "iganadal": True})


# --- Readers ---------------------------------------------------------------------------------------

def test_ics_reader_robootika():
    items = voistlusallikad.ics_items(feed("robootika.ics"), TODAY)
    by = {i["title"]: i for i in items}
    assert len(items) == 6
    assert (by["RoboLahing 2026"]["start"], by["RoboLahing 2026"]["end"]) == (date(2026, 10, 31), date(2026, 10, 31))
    assert by["RoboLahing 2026"]["link"] == "https://www.robootika.ee/yritus/robolahing-2026/"
    ftc = by["FIRST Tech Challenge Estonia pilootvõistlus"]
    assert (ftc["start"], ftc["end"], ftc["location"]) == (date(2027, 4, 24), date(2027, 4, 25), "Kiili Gümnaasium, Kooli 2, Kiili, Estonia")
    assert all(voistlusallikad.wanted(BY_KEY["robootika"], i) for i in items)  # robotics days are competitions too


def test_ics_reader_etag_needs_pupils():
    items = voistlusallikad.ics_items(feed("etag.ics"), TODAY)
    assert len(items) == 10  # research-funding days and conferences, that day
    assert not any(voistlusallikad.wanted(BY_KEY["etag"], i) for i in items)
    contest = {"title": "Õpilaste teadustööde riikliku konkursi tähtaeg", "text": ""}
    assert voistlusallikad.wanted(BY_KEY["etag"], contest)
    assert not voistlusallikad.wanted(BY_KEY["etag"], {"title": "Konkursi infopäev teadlastele", "text": ""})


def test_rss_reader_miks():
    items = voistlusallikad.rss_items(feed("miks.xml"))
    assert len(items) == 12
    by = {i["title"]: i for i in items}
    robolahing = by["AHHAA Robolahing 2026 registreerumine"]
    assert robolahing["link"] == robolahing["id"] == "https://miks.ee/opilasele/sundmused/ahhaa-robolahing-2026-registreerumine/"
    assert "Registreerida saab kuni 11.oktoober 2026 (k.a)" in robolahing["text"] and "<p>" not in robolahing["text"]
    wanted = {i["title"] for i in items if voistlusallikad.wanted(BY_KEY["miks"], i)}
    assert {"AHHAA Robolahing 2026 registreerumine", "Õpilasleiutajate riiklik konkurss 2026", "E-rehkendus"} <= wanted
    assert "Teadlaste Öö Festival" not in wanted and "Rändnäitus „Lähme nulli!“" not in wanted


def test_rss_reader_with_html_entities_and_event_dates():
    items = voistlusallikad.rss_items(feed("kesk-eesti.xml"))  # &otilde; is not XML
    assert [i["title"] for i in items] == ["Pärnu CoastHack", "Garage48 Future of Wood 2026 makeathon"]
    assert items[0]["text"].startswith("startDate: 2026-10-02\nstartHour: 16:30\nendDate: 2026-10-04\nendHour: 18:00\nCoastHack on")


@pytest.mark.parametrize("body, message", [
    (b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>',
     "RSS-voos on DTD; seda ei loeta."),
    (b"<rss><channel><item><title>katki</item></rss>", "RSS-voog on vigane."),
])
def test_rss_reader_refuses(body, message):
    with pytest.raises(ValueError, match=message):
        voistlusallikad.rss_items(body)


def test_html_reader_and_active_sources(monkeypatch):
    monkeypatch.setattr(voistlusallikad, "download", lambda url: b"<p>Future of Wood 2026</p><p>When 16-18 October 2026</p>")
    [item] = voistlusallikad.read(BY_KEY["garage48"], TODAY)
    assert item == {"id": "https://garage48.org/events", "title": "Garage48", "link": "https://garage48.org/events",
                    "text": "Future of Wood 2026\nWhen 16-18 October 2026"}
    assert [s.key for s in voistlusallikad.active(["hakatonid"])] == ["kesk-eesti", "garage48", "kyberolympia"]
    assert [s.key for s in voistlusallikad.active(["humanitaar"])] == ["etag"]


# --- Runs ------------------------------------------------------------------------------------------

def test_a_calendar_source_needs_no_model(conn, world):
    world.csv = b""
    only(conn, "robootika")
    world.feed("robootika", "robootika.ics")
    result = voistlused.run(conn)
    assert result["allikad"] == 6 and result["errors"] == []
    assert not [c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]
    row = proposals(conn)["RoboLahing 2026"]
    assert row["source_ref"] == "voistlus:allikas:robootika:3947-1793404800-1793491199@www.robootika.ee"
    assert row["description"] == "Link: https://www.robootika.ee/yritus/robolahing-2026/\nrobootika.ee"
    assert row["location"] == "Tartu Näitused messikeskuses, F. R. Kreutzwaldi 60, Tartu, Estonia"
    assert voistlused.origin(row) == "robootika.ee"
    # The qualification and the show a fortnight apart, alike in name: two events.
    assert "RoboLahing 2026 kvalifikatsioonivõistlus" in proposals(conn)
    assert voistlused.run(conn)["allikad"] == 0


def test_feed_items_go_through_the_extraction(conn, world):
    world.csv = b""
    only(conn, "hakatonid")
    world.feed("kesk-eesti", "kesk-eesti.xml")
    world.page(COASTHACK, "", world.event(
        title="Pärnu CoastHack", date_text="startDate: 2026-10-02", start_date="2026-10-02", end_date="2026-10-04",
        location="Tartu Ülikooli Pärnu kolledž", source_quote="CoastHack toimub 2.-4. oktoobril 2026 Tartu Ülikooli Pärnu kolledžis.",
        age_min=16, facts_quote="CoastHack on mõeldud kõigile alates 16. eluaastast.", team="team"))
    result = voistlused.run(conn)
    calls = [c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]
    assert len(calls) == 2 and all(c["web_search"] is False and c["model"] == "sonnet" for c in calls)
    fence = calls[0]["prompt"].split("<data-", 1)[1][:12]
    assert f'<data-{fence} name="page">\n{COASTHACK}\n\nstartDate: 2026-10-02' in calls[0]["prompt"]
    assert result["allikad"] == 1
    row = proposals(conn)["Pärnu CoastHack"]
    assert row["source_ref"] == f"voistlus:allikas:kesk-eesti:{COASTHACK}" and voistlused.origin(row) == "Kesk-Eesti inkubaator"
    facts = voistlused.info(conn, row["uid"])
    assert facts["ages"] == [16, None] and facts["team"] == {"kind": "team", "size": None}
    decided, _ = voistlused.eligibility_of(conn, row)
    assert decided["result"] == "fits" and decided["unknown"] is False  # no birth year: the limit is only shown
    voistlused.save_settings(conn, {**voistlused.settings(conn), "synniaasta": 2011})
    assert voistlused.eligibility_of(conn, row)[0]["reason"] == "vanusepiirang 16+"
    voistlused.save_settings(conn, {**voistlused.settings(conn), "synniaasta": 2008})
    assert voistlused.eligibility_of(conn, row)[0]["result"] == "fits"


def test_items_read_once_are_not_read_again(conn, world):
    world.csv = b""
    only(conn, "hakatonid")
    world.feed("kesk-eesti", "kesk-eesti.xml")
    voistlused.run(conn)
    world.calls.clear()
    voistlused.run(conn)
    assert not [c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]
    # A post changed since: read again.
    world.sources["kesk-eesti"] = world.sources["kesk-eesti"].replace(b"16. eluaastast", b"15. eluaastast")
    voistlused.run(conn)
    assert len([c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]) == 1


def rss(n):
    items = "".join(f"<item><title>Häkaton {i}</title><link>https://naide.ee/{i}</link>"
                    f"<description>Noorte häkaton nr {i}.</description></item>" for i in range(n))
    return f"<rss><channel>{items}</channel></rss>".encode()


def test_at_most_fifteen_extraction_calls_a_run(conn, world, caplog):
    world.csv = b""
    only(conn, "hakatonid")
    world.sources["kesk-eesti"] = rss(20)
    with caplog.at_level(logging.INFO, logger="markmik.voistlused"):
        voistlused.run(conn)
    calls = [c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]
    assert len(calls) == voistlused.SOURCE_CALLS == 15
    assert "source kesk-eesti: 'Häkaton 15' left for a later run (15 calls a run)" in caplog.text
    world.calls.clear()
    voistlused.run(conn)  # the next run reads the rest, and garage48's page
    assert len([c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA]) == 5


def test_the_daily_cap_still_applies(conn, world, monkeypatch):
    from app.ai import runner
    world.csv = b""
    only(conn, "hakatonid")
    world.sources["kesk-eesti"] = rss(3)
    monkeypatch.setattr(runner, "DAILY_CAP", 2)
    result = voistlused.run(conn)
    assert "Allikate lugemine jäi pooleli: päevane AI-päringute piir (2) on täis" in result["errors"]


def test_items_without_a_competition_cost_nothing(conn, world):
    world.csv = b""
    only(conn, "reaalained")
    world.feed("miks", "miks.xml")
    voistlused.run(conn)
    read = {c["prompt"].split('name="candidate">\n', 1)[1].split("\n", 1)[0] for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA}
    assert "AHHAA Robolahing 2026 registreerumine" in read and "Teadlaste Öö Festival" not in read
    assert len(read) < 12


def test_a_source_that_fails_is_noted_and_the_rest_go_on(conn, world):
    world.csv = b""
    only(conn, "robootika")
    world.sources["miks"] = ValueError("Lehte ei saanud alla laadida.")
    world.feed("robootika", "robootika.ics")
    result = voistlused.run(conn)
    assert result["errors"] == ["Miks.ee: allikat ei saanud lugeda."] and result["allikad"] == 6


def test_sources_are_read_for_their_interests_only(conn, world):
    world.csv = b""
    only(conn, "humanitaar")
    voistlused.run(conn)
    assert world.read_sources == ["etag"]


def test_a_list_page_gives_several_competitions(conn, world):
    world.csv = b""
    only(conn, "hakatonid")
    world.sources["garage48"] = (FEEDS / "garage48.txt").read_bytes()
    world.page("https://garage48.org/events", "",
               world.event(title="Future of Wood 2026", date_text="When 16-18 October 2026", start_date="2026-10-16",
                           end_date="2026-10-18", location="Väimela", source_quote="Future of Wood 2026"),
               world.event(title="kood/Garage48 Empowering Women Hackathon", date_text="When 16 - 18 October 2026",
                           start_date="2026-10-16", end_date="2026-10-18", location="Jõhvi",
                           source_quote="kood/Garage48 Empowering Women Hackathon"))
    voistlused.run(conn)
    [call] = [c for c in world.calls if c["schema"] is voistlused.PAGE_SCHEMA and "garage48.org" in c["prompt"]]
    assert "Report at most 8 events" in call["system"]
    rows = proposals(conn)
    assert {"Future of Wood 2026", "kood/Garage48 Empowering Women Hackathon"} <= set(rows)
    refs = {rows[t]["source_ref"] for t in ("Future of Wood 2026", "kood/Garage48 Empowering Women Hackathon")}
    assert len(refs) == 2 and all(r.startswith("voistlus:allikas:garage48:") for r in refs)


def test_the_same_event_from_two_sources_is_one_proposal(conn, world):
    world.csv = b""
    only(conn, "robootika")
    world.feed("robootika", "robootika.ics")
    world.feed("miks", "miks.xml")
    link = "https://miks.ee/opilasele/sundmused/ahhaa-robolahing-2026-registreerumine/"
    world.page(link, "", world.event(title="RoboLahing 2026 kvalifikatsioon", date_text="16.oktoobril 2026", start_date="2026-10-16",
                                     end_date=None, source_quote="Enne sündmust toimub kvalifikatsioon AHHAA teaduskeskuses 16.oktoobril 2026."))
    voistlused.run(conn)
    on_day = [r for r in proposals(conn).values() if r["start_date"] == "2026-10-16"]
    assert [r["title"] for r in on_day] == ["RoboLahing 2026 kvalifikatsioonivõistlus"]


def test_age_limits_from_a_calendar_text(conn, world):
    world.csv = b""
    only(conn, "robootika")
    world.sources["robootika"] = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Robotite v\xc3\xb5istlus\r\n"
                                  b"DTSTART;VALUE=DATE:20261120\r\nDESCRIPTION:Osalema on oodatud 14-19-aastased noored.\r\n"
                                  b"END:VEVENT\r\nEND:VCALENDAR\r\n")
    voistlused.run(conn)
    row = proposals(conn)["Robotite võistlus"]
    assert voistlused.info(conn, row["uid"])["ages"] == [14, 19]


# --- Discovery, settings ---------------------------------------------------------------------------

def test_hackathon_hints_go_to_discovery_only_with_the_interest(conn, world):
    world.csv = b""
    only(conn, "hakatonid")
    voistlused.run(conn)
    [discovery] = [c for c in world.calls if c["schema"] is voistlused.DISCOVERY_SCHEMA]
    assert "Pärnu CoastHack" in discovery["system"] and "häkaton gümnaasiumiõpilastele" in discovery["system"]
    assert "robootika.ee, miks.ee, etag.ee, kesk-eesti.ee, garage48.org" in discovery["system"]
    assert discovery["prompt"] == voistlused.DISCOVERY_PROMPT.format(
        today="2026-09-27", interests="Häkatonid (häkatonid, ideekonkursid, CTF-id, game jam'id)", note="(none)")
    world.calls.clear()
    only(conn, "reaalained")
    voistlused.run(conn)
    [discovery] = [c for c in world.calls if c["schema"] is voistlused.DISCOVERY_SCHEMA]
    assert "CoastHack" not in discovery["system"]


def test_birth_year_setting(conn):
    base = {"huvid": ["hakatonid"], "markus": "", "iganadal": True}
    assert voistlused.save_settings(conn, {**base, "synniaasta": 2008})["synniaasta"] == 2008
    assert voistlused.save_settings(conn, base)["synniaasta"] == 2008  # left as it was
    assert voistlused.save_settings(conn, {**base, "synniaasta": None})["synniaasta"] is None
    for bad in (1990, 2025, "2008", True):
        with pytest.raises(ValueError, match="Sünniaasta peab olema 1996 kuni 2021, või tühi."):
            voistlused.save_settings(conn, {**base, "synniaasta": bad})


@pytest.mark.parametrize("text, ages", [
    ("Pärnu CoastHack, vanus 16+", [16, None]),
    ("CoastHack on mõeldud kõigile alates 16. eluaastast.", [16, None]),
    ("KüberNaaskel eelvoor 5.-9.10.2026 (14.-25. aastased)", [14, 25]),
    ("kõiki 6–20-aastaseid noori", [6, 20]),
    ("noored vanuses 13–21", [13, 21]),
    ("Participants are mainly 18 and over", [18, None]),
    ("ages 14-19", [14, 19]),
    ("vähemalt 16-aastased", [16, None]),
    ("kuni 21-aastased", [None, 21]),
    ("7.-12. klass", None),
    ("2026+ hooaeg", None),
    ("1+1 pakkumine", None),
])
def test_age_facts(text, ages):
    assert eligibility.age_facts(text) == ages


@pytest.mark.parametrize("ages, born, result, reason", [
    ([16, None], None, "fits", None),
    ([16, None], 2011, "no", "vanusepiirang 16+"),  # 15 in 2026
    ([16, None], 2010, "fits", None),
    ([14, 25], 2000, "no", "vanusepiirang 14–25"),
    ([None, 19], 2006, "no", "vanusepiirang kuni 19"),
])
def test_decide_with_ages(ages, born, result, reason):
    info = {**eligibility.merge({"grades": None, "level": None, "round": None, "ages": None}, None), "ages": ages}
    out = eligibility.decide(info, date(2026, 11, 20), {"klass": 12, "seatud": "2026-09-27", "synniaasta": born})
    assert (out["result"], out["reason"]) == (result, reason)
