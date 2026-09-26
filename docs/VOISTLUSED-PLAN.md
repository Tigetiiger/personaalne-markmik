# Võistluste otsija: a competition finder (2026-09-26)

**The user's request:** an agent they can start that looks for competitions and then asks which
to put in the calendar. Olympiads come from <https://kalender.teaduskool.ut.ee/>; other
competitions too.

## The user's answers

| Question | Answer |
|---|---|
| How to choose | **Postkast**: each find is a card with Kinnita / Loobu, like the events found in email |
| What to look for | **Reaalained** (math, physics, informatics), **Loodusained** (chemistry, biology, geography), **Robootika ja tehnoloogia** (Robotex, FLL, hackathons, engineering). Changeable in Seaded, with a free-text field for class, school and extra wishes |
| Automatic | **A button, and weekly.** When the weekly run finds something new: an ntfy push, and "N uut võistlust" on Täna. Nothing goes into the calendar without the user |
| Registration | **Also add the registration deadline** when one is found: a "Registreeri: …" deadline with its reminder |

## Findings

- The teaduskool calendar reads one public CSV:
  `https://tk-kalender-files.s3.eu-central-1.amazonaws.com/kalender.csv`. It has about 1,350 lines
  since 2016 and group header lines (`Olümpiaadid`, `Võistlused`, `Laagrid/sessid`,
  `Rahvusvahelised`, `Muu`), then `title,start,end,url` rows. Dates are ISO, `dd.mm.yyyy` or
  `dd/mm/yyyy`; `end` is inclusive or empty; commas inside quotes are allowed. Last update:
  `kalender_last_updated.txt` next to it. It needs no AI.
- `events.source` has a CHECK constraint (`käsitsi, ekool, gmail, ics, ai`) and `kind` another
  one. Changing them means rebuilding the table, so neither changes.

## Lead's calls

- **Two sources, one pipeline.**
  1. **Teaduskool** (code only): fetch the CSV with the hardened fetcher in `app/ics.py` (public
     addresses only, size and time caps). Parse it. Keep future rows of the groups Olümpiaadid,
     Võistlused and Rahvusvahelised whose title matches the chosen interests; a keyword list per
     interest, in Estonian.
  2. **Web** (AI): a discovery run with **WebSearch only**. It gets no app tools, no notes, and
     no WebFetch. Its prompt holds only the interests, the free text and today's date, and it
     returns candidates `{title, url, why}`. Then **code** fetches each candidate URL with the
     same hardened fetcher. A second AI call with **no tools** and `json_schema` output extracts
     `{title, start_date, end_date?, registration_deadline?, location?, source_quote, confidence}`
     from the page text, as the email extraction does. Dates are resolved and checked in code:
     valid, in the future, within a year.
  Why: web pages are untrusted. An agent with WebFetch could be steered by a page into fetching
  internal addresses or leaking what it knows. This split gives the model nothing to leak and no
  way to reach the tailnet.
- **Stored as proposals:**
  - the competition is `kind='sündmus'`, `status='pakutud'`, `source='ai'`;
  - its origin sits in `source_ref`: `voistlus:teaduskool:<stable key>` or `voistlus:web:<url>`.
    Postkast shows it as "Teaduskool" or "Veeb" with the link and the quote;
  - the registration deadline is a second proposal, `kind='tähtaeg'`, "Registreeri: <title>",
    tied to the same `source_ref`. The Postkast shows one card per competition, and Kinnita
    confirms both;
  - Loobu rejects both, through `rejected_uids`, so they never return.
  - Stable uids: dedupe across the two sources and against existing events, by normalized title
    and date.
- **Running it:**
  - an "Otsi võistlusi" button on the Postkast crumb row, and a palette command. It starts a
    background run, shows "Otsin…", and the cards appear when it ends;
  - the weekly job `voistlused` runs through the worker registry;
  - one run at a time;
  - the AI calls count toward the existing daily cap, with at most a few web searches per run.
- **The push:** an ntfy message "N uut võistlust" through the existing ntfy config, only for new
  finds. Täna's Postkast count already shows them.
- **Seaded → Võistlused:**
  - the interest checkboxes (the three chosen, plus Humanitaar off);
  - a free-text field (class, school, extra wishes);
  - the weekly on/off switch;
  - the last run's time and result.

## Checklist (package `wp/voistlused`)

- [ ] `app/sources/teaduskool.py`: fetch and parse the CSV, filter by interest and group, future
      only. Tests on a saved copy of the real CSV (public data) under `tests/fixtures/`,
      including the date formats, empty end dates, quoted commas and group switching.
- [ ] `app/voistlused.py`: the discovery run (WebSearch only), the hardened page fetch, extraction
      with no tools, date checks, dedupe, proposals with a registration deadline, and rejected
      uids. Tests with the fake runner. Also: a page with injected instructions changes nothing;
      a past date or a date more than a year away is dropped.
- [ ] The `voistlused` job (weekly), the ntfy message for new finds, one run at a time. Tests.
- [ ] `POST /api/voistlused/run` (owner, CSRF) and a status endpoint; the button and the palette
      command; Postkast cards showing the origin, the link and the deadline together. Playwright
      with `AI_FAKE`.
- [ ] Seaded → Võistlused section. Playwright.
- [ ] CONTRACTS.md: the settings keys (`voistlused_huvid`, `voistlused_markus`,
      `voistlused_iganadal`), the endpoints, the job, the `source_ref` format.
- [ ] Whole suite green; then review, merge, deploy.

## Part 2: can I take part? (the user, 2026-09-27)

The user asked the finder to find out what is needed to take part, and to filter out what they
cannot enter.

| Question | Answer |
|---|---|
| Grade | **12th grade** now. The grade goes up by itself every 1 September; after 12th grade no school-grade rounds fit |
| Rounds you qualify into (lõppvoor, international) | **Show them with a note, and also add the qualifying round automatically**: confirming a final also puts its qualifying round (eelvoor, koolivoor or piirkonnavoor of the same olympiad and season) into the calendar |
| Competitions you cannot enter | **A collapsed list** at the end of the Postkast, "Ei sobi (N)". Each entry gives its reason (e.g. "ainult 7.–8. klassile") and can still be confirmed |

**Lead's calls**

- **Participation facts per find**, stored as JSON next to the proposal. A new table
  `voistlus_info(uid PRIMARY KEY, info_json)` is created with `CREATE TABLE IF NOT EXISTS`, so no
  existing table changes. The fields:
  - `grades` (min–max, or null);
  - `level` (põhikool, gümnaasium, open, university);
  - `team` (individual or team, with its size);
  - `qualification` (open, or qualifies through <round>);
  - `registration` (who registers: self, the school or the teacher; how; the deadline);
  - `fee`, `location` (or online), `requirements` (free text, capped), `source_quote`.
- **Where the facts come from:**
  - Teaduskool rows: first the title ("7.–12. klassile", "gümnaasiumile", "lõppvoor", "eelvoor").
    Then the row's own URL (the olympiad page), fetched with the hardened fetcher and read by the
    no-tools extraction. Fetch each olympiad page once per run, not once per row; many rows
    share it.
  - Web finds: the same extraction call returns the facts together with the dates.
- **Eligibility is decided in code** from the facts, the grade and the date. The grade on the
  competition's date is the grade now, plus one when 1 September falls in between. The result:
  `fits`, `qualify` (a final: the note plus its qualifying round), or `no` (with a reason).
  Unknown facts count as `fits`, with "Tingimused teadmata" on the card.
- **Qualifying rounds:**
  - a final or an international round is linked to the earlier round(s) of the same olympiad in
    the same season, from the teaduskool data (same subject, the round names);
  - Kinnita on the final confirms the qualifying round too, when it is still a proposal;
  - the card shows "Pääseb edasi: <round, date>".
- **Seaded → Võistlused:** a grade field (defaults to 12 now), stored with the year it was set,
  so it can advance itself.
- **Existing proposals:** the next run re-checks proposals already waiting (the 50 there now)
  and moves the unsuitable ones into "Ei sobi". Nothing confirmed or rejected is touched.
- **Web reliability, from the first live run:**
  - page extraction uses `sonnet`, not `haiku`. On 2026-09-27 haiku read the same Robotex page
    right once and wrong in two full runs;
  - log each candidate's outcome at INFO (skipped: why; found: how many);
  - the fetch error must not say "ICS-aadress" for a web page.

## Checklist, part 2

- [ ] `voistlus_info` table; facts from the teaduskool title and page, and from web extraction.
      Tests with fixtures (saved olympiad pages).
- [ ] Eligibility in code: grade on the date, level, qualification. Table tests (7.–8. klassile no;
      gümnaasiumile yes; lõppvoor qualify; university no; unknown fits).
- [ ] Qualifying-round link and Kinnita confirming both. Tests.
- [ ] Postkast: the facts on the card (who, how to register, team, fee, where), the "Ei sobi (N)"
      section with reasons, and confirm still possible. Playwright.
- [ ] The grade setting with its yearly rollover. Tests.
- [ ] Re-checking waiting proposals on the next run.
- [ ] The web reliability items above.
- [ ] Whole suite green; review; merge; deploy; one live run, reporting what fits and what not.

## Part 3: hackathons and known sources (the user, 2026-09-27)

The user asked the web search to find hackathons and similar competitions too, and asked for a
list of who organizes them and where they post. The research is in `docs/VOISTLUSED-ALLIKAD.md`
(all checked on 2026-09-27). The Teaduskool CSV has no hackathons, no CanSat, no Robolahing, and
nothing on Robotex since 2019.

**Lead's calls**

- **Fixed sources, read each run by code** (the hardened fetcher; no AI for the fetch). The feeds
  come first:
  - `robootika.ee` iCal (`?post_type=tribe_events&ical=1`) and `etag.ee/uritus/?ical=1`, through
    the existing ICS parsing: dates are structured;
  - `miks.ee/opilasele/sundmused/feed/` and `kesk-eesti.ee/sundmus/feed/` RSS. The item's post
    date is not the event date, so the item's text goes through the no-tools extraction like a
    web page;
  - the HTML list pages, `garage48.org/events` and the KüberOlümpia site: the page text goes
    through the same extraction.
  - Each source is a small entry in one list: name, URL, kind (`ics`, `rss`, `html`), and which
    interests it serves. A source that fails is skipped and noted in the run's errors; the rest
    go on.
- **Hackathons become an interest of their own**, "Häkatonid", on by default. The discovery
  prompt gets the working queries from the research, and the known organizers, as hints.
- **Filter:** the same eligibility code (part 2): age limits such as 16+ or 18+, the grade, and
  the level. Ages count from the user's age, a new setting: the birth year, asked in Seaded.
  Without it, age limits are shown as "Vanusepiirang: 16+" and treated as fits.
- **Cost:** feed items are filtered by keywords before any AI call. At most N extraction calls
  per run for feed and HTML sources (N=15), and the daily cap still applies.
- Devpost, CTFtime and Codeforces JSON are left out for now: international and mostly online.
  They may come later if the user wants them.

## Checklist, part 3

- [ ] Source list and readers: `ics`, `rss` and `html`, each with a fixture test (saved feeds under
      tests/fixtures/voistlused/).
- [ ] The "Häkatonid" interest; the discovery hints.
- [ ] The birth-year setting and age limits in the eligibility code.
- [ ] Keyword prefilter and the per-run extraction cap.
- [ ] One live dry run on a copy of the live DB: what each source yields.
