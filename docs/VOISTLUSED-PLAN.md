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
