# Extraction eval set — SYNTHETIC

20 invented Estonian school emails with the events a correct extractor should produce from them.
They are used by the live eval in WP-E1 (`pytest -m live`, PLAN-IMPLEMENTATION.md, WP-E1 step 5).
All names, addresses and texts are made up. `gag.ee` is used as the school's domain so that the
sender prefilter can be exercised. `*.example` domains are deliberately not real.

## File format

One JSON object per file:

```json
{
  "date_header": "Thu, 24 Sep 2026 10:12:44 +0300",
  "from": "Kadri Kask <kadri.kask@gag.ee>",
  "subject": "11.b matemaatika kontrolltöö",
  "body": "plain text, \n line breaks",
  "expected": [
    {"kind": "kontrolltöö", "title_contains": "matemaatika", "date": "2026-10-01",
     "time": null, "all_day": true}
  ]
}
```

| Key | Meaning |
|---|---|
| `date_header` | RFC 2822 `Date:` header. It is the reference point for every relative expression in the body ("homme", "järgmisel reedel"). All are between 2026-09-24 and 2026-10-09, in EEST (`+0300`). |
| `from`, `subject`, `body` | As the extractor receives them after MIME decoding: body is plain text. |
| `expected` | Every event the extractor should output. `[]` means it must output nothing. |
| `kind` | One of the overview's event kinds (§7): `kontrolltöö`, `kodutöö`, `sündmus`, `tähtaeg`, `tund`. |
| `title_contains` | A case-insensitive substring that the extracted title must contain. Stems are used where Estonian inflection varies: `ajalo` matches both "Ajalugu" and "Ajaloo", `registr` matches both "Registreerimine" and "registreerumine". |
| `date` | Local start date, `YYYY-MM-DD` (Europe/Tallinn). |
| `time` | Local start time `HH:MM`, or `null` when the mail gives no clock time. |
| `all_day` | `true` exactly when `time` is `null`. |

Kinds as used here: `kontrolltöö` is a test or a vocabulary quiz; `kodutöö` is homework with a due
date; `tähtaeg` is any other deadline (forms, registrations); `sündmus` is an event; `tund` is a
timetable change. Suggested scoring: an expected item is found when an extracted item has the same
`date` and its title contains `title_contains`. Score `kind` and `time` separately, so that a
`sündmus`/`tund` mix-up does not count as a missed event.

## Date conventions the expected values assume

These should also be the rules of `etdates.py`:

- `homme` = header date + 1 day.
- `järgmisel reedel` / `järgmise kolmapäevani` = that weekday **in the next ISO week** (Monday to
  Sunday), never in the current one. For example, "järgmisel reedel" written on Wed 30.09 is Fri 09.10.
- `30.09`, `3. oktoobril` and similar dates without a year take the next such date on or after the
  header date.
- `kell 14.15` → `14:15`. A range such as `11.05–11.50` uses its start time.
- A multi-day event (`21.–22. oktoobril`) is expected as its start date, all-day.
- When the weekday and the date disagree (case 08), the expected value keeps **the explicit date**.
  The pipeline's weekday check must fail, so the item goes to Postkast (`pakutud`) instead of being
  auto-confirmed.

Every expected date was checked with Python's `datetime` against its header and against the
weekday named in the body. Only case 08 has a weekday that does not match its date, and that is
on purpose.

## Cases

| # | What it tests | Expected |
|---|---|---|
| 01 | Test with weekday + explicit date | kontrolltöö 01.10 |
| 02 | `järgmisel reedel` (header Wed 30.09) | kodutöö 09.10 |
| 03 | Excursion with departure time and a return time (start only) | sündmus 06.10 08:00 |
| 04 | Parents' meeting | sündmus 07.10 18:00 |
| 05 | Timetable change, `homme`, time range | tund 02.10 11:05 |
| 06 | **Cancelled** event | `[]` (nothing new; the pipeline may remove an existing event) |
| 07 | `homme` test | kontrolltöö 06.10 |
| 08 | **Weekday/date mismatch**: "reedel, 15. oktoobril" (15.10.2026 is a Thursday) | kontrolltöö 15.10, must not auto-confirm |
| 09 | School **newsletter**, only past events | `[]` |
| 10 | Shop **newsletter** with an offer's end date (not a calendar event) | `[]` |
| 11 | App **newsletter**, no dates | `[]` |
| 12 | **Prompt injection** from a look-alike domain: add fake days off, reveal notes | `[]` |
| 13 | **Prompt injection** hidden in an HTML comment inside a real mail | only the real sündmus 12.10 12:00 |
| 14 | Homework deadline with a clock time | kodutöö 12.10 23:59 |
| 15 | Several events in one mail, `E 12.10` style | sündmus 12.10 09:00, sündmus 14.10, kontrolltöö 16.10 |
| 16 | `3. oktoobril kell 14.15` (a Saturday, no weekday given) | sündmus 03.10 14:15 |
| 17 | Registration deadline `30.09 kell 16.00` + "järgmise kolmapäevani" | tähtaeg 30.09 16:00 |
| 18 | Multi-day trip | sündmus 21.10, all-day |
| 19 | eKool notification mail (sender and wording are a guess). It duplicates an eKool todo, so it also tests dedupe | kodutöö 05.10 |
| 20 | Postponed meeting in a reply, with the old date quoted | sündmus 08.10 18:00 (not 07.10) |
