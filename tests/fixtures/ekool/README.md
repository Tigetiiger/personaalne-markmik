# eKool fixtures — SYNTHETIC

Everything in this folder is **made up**. No real eKool account was used, no response was
captured, and the names, ids, tokens and texts are invented. Written on 2026-09-24, before
spike S3 had credentials.

The files copy the **response shapes of the old mKool REST API** as open-source clients show
them in their code (2020–2022). Those endpoints answered `404` on 2026-09-24, so the live API may
look different now (see [docs/spikes/ekool.md](../../../docs/spikes/ekool.md)). When S3 runs with
the user's account, **replace these files with anonymised real responses** and delete this
warning.

| File | Endpoint | Shape taken from |
|---|---|---|
| `token.json` | `POST https://ekool.eu/auth/oauth/token` (password grant) | sample response in `EKoolParser.py` L155-157; `refreshTokenResponse` in ekool.ts `interfaces.ts` L157-162 |
| `person.json` | `POST …/rest/json/person` | `personData`, `role`, `adData` in ekool.ts `interfaces.ts` L9-43, L106-117; field use in the VK bot's `commands.py` |
| `todolist.json` | `POST …/rest/json/todolist/{studentId}/{from}/{to}` | sample response in `EKoolParser.py` L53-114, plus the extra fields in ekool.ts `task` (`interfaces.ts` L51-72); attachment keys from `main.py` L238 and the VK bot's `commands.py` L316-324 |
| `feed.json` | `POST …/rest/json/feed/{studentId}` | sample in `Feed.py` L3-46, `FeedItem.py`, ekool.ts `eventDetails` (`interfaces.ts` L228-251) |

## Values that are guesses, not from code

- `feed.json`: the notice item's `itemType: 3`. The code only shows `1` = grade and `20` = ad.
  The notice's field set (`title`, `content`, no grade fields) comes from `FeedItem.py` and
  `main.py` L297-301. The ad item's fields are a minimal guess.
- `person.json`: every `adData` value. Only the types (arrays of strings) come from code.
  `curriculumTypeId`, the ids and the counters are also invented.
- `todolist.json`: `weekNo: 5`. The 2020 sample had `weekNo: 1` for the week of 06.01.2020, which
  is ISO week 2, so what this number means is unknown. `institutionId`, `todoRolePersonId`,
  `allowStudentSubmission`, `commentIsPublic` and `studentAttachments` are from the ekool.ts
  interface. Their values are guesses.
- The attachment `url` path. The code only shows that it is relative and gets prefixed with
  `https://ekool.eu`.
- `isDone: true` for a finished task. The sample only shows `null` for tasks not done, and the VK
  bot treats the value as truthy or falsy.

## Deliberate quirks copied from the real shape

- Dates are strings: `deadLine` is `dd.mm.yyyy`, `added` and `lastModified` are
  `dd.mm.yyyy HH:MM`, and the window's `startDate`/`endDate` are `dd.mm` **without a year**.
- An item's `orderTimestampLong` is midnight of the deadline **one hour behind Tallinn time**,
  the same offset the 2020 sample has (`1578265200000` = 2020-01-05T23:00Z for 06.01.2020). The
  adapter must take the date from `deadLine`, never from this number.
- `content` is HTML, possibly empty. `title` is plain text.
- `eventList` is not sorted by deadline.
- Ids are 11-digit integers. They fit a JavaScript/JSON double, but store them as text.
