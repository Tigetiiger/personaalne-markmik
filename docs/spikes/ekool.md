# Spike S3 — eKool (groundwork, no credentials yet)

Written 2026-09-24 on branch `wp/spike-sources`. **No eKool account was used.** This part is desk
research: open-source client code, archived eKool documentation, and three unauthenticated
status-code checks. The live part of S3 (PLAN-IMPLEMENTATION.md, Wave 0.5) still has to run with
the user present. Its checklist is in §9.

## 1. Summary

- The plan's eKool route (overview, research finding 3) is the old **mKool mobile API**: an OAuth
  password grant at `https://ekool.eu/auth/oauth/token` with `client_id=mKool`, then
  `POST https://postikana.ekool.eu/rest/json/...`. Four independent open-source clients from
  2020–2023 agree on it exactly, so the request and response shapes below are well documented.
- **On 2026-09-24 both of those endpoints answered `404`** to an unauthenticated POST
  (§5). A live Spring OAuth endpoint answers `401` there. The old API has most likely been retired
  since eKool moved to `login.ekool.eu` (2023) and the rebuilt eKool app (v7.x in 2026).
- What the code shows is live in 2026: `login.ekool.eu` (a new OAuth server; its `/oauth/token`
  returned `400`), the web app `family.ekool.eu` driven by **DWR** calls with an `EKOOLSESSION`
  cookie (a companion app committed in May 2026 uses exactly this), and a 2024 note that the new
  mobile app uses an authorization-code flow with `scope=mkool-api`.
- The official public API (`api-v2.ekool.eu`, credentials from help@ekool.eu) only exposes identity
  data: user, roles, schools, children. It has **no homework, tests or timetable**.
- **Recommendation**: keep the adapter interface and the normalisation described here (it does not
  depend on the transport). In the live S3, first confirm that the legacy route is dead. Then pick
  the transport in this order: (A) web session + DWR calls, captured from a Playwright login;
  (B) the new app's `mkool-api` OAuth, only if the user accepts using credentials extracted from
  the app; (C) Playwright DOM scraping. See §7–§8. The Ühendused card may need to change from
  "username + password" to something else, which is a plan change for the lead (§7.1).

## 2. Sources examined

| Source | Last commit | What it shows |
|---|---|---|
| [Rohukas/ekool-cli](https://github.com/Rohukas/ekool-cli/tree/f4f12572acf0604c54d90edbaa2c420ffebf3cdf) (Python) | 2020-11-13 | password grant, query-base checksum, `/person`, `/todolist`, `/feed`, `/feeditem`, `/family`, `/absences90Days`, with sample responses |
| [erik5227/ekool.ts](https://github.com/erik5227/ekool.ts/tree/69372ff75e2d1739909322ab10fe24d8369903ad) (TypeScript) | 2022-01-28 | same login, **refresh flow**, typed interfaces, and more endpoints: `/lessons`, `/timetable`, `/grades`, `/gradesheet`, `/todoChange`, personal tasks |
| [ledjajev/VK-ekool-bot](https://github.com/ledjajev/VK-ekool-bot/tree/d6c6ac46efd86a1188ab96a390e49f79174dc5c8) (Python) | 2023-01-03 | same API still used at the start of 2023; `roles[].timetableUrl`, attachment fields; lists Smart-ID and DWR URLs |
| [mar4ello6/eKoolCpp](https://github.com/mar4ello6/eKoolCpp/tree/dd793f0d44de7936a81c10ade79842c50b26983c) (C++) | 2023-08-19 | **new web login**: `login.ekool.eu/login` form → `family.ekool.eu` DWR |
| [illucc/unofficial-ekool-python-api](https://github.com/illucc/unofficial-ekool-python-api/blob/1b28c2fd03d15c9a8743415439b3a8c597ede3f3/README.md) | 2024-11-01 | the new app's flow: `login.ekool.eu/oauth/authorize … scope=mkool-api`, client id/secret from the APK |
| [SamTheProto/QoLC](https://github.com/SamTheProto/QoLC/tree/f1eebae2a1cf34799cb6aa7d86b797a481ed3b1d/app/src/main/java/com/samtheproto/qolc/subapps/EkoolCompanion) (Kotlin) | 2026-05-07 | **current**: WebView login at `login.ekool.eu`, then `EKOOLSESSION` cookie + DWR calls on `family.ekool.eu` |
| [SoggyDoggy/EkoolNavigator](https://github.com/SoggyDoggy/EkoolNavigator/blob/737d5c67bb034d65b168e3e5ea74d9aa35ea30d7/EkoolNavigatorStudent.py) (Selenium) | 2023-05-17 | UI automation of the login form and the grade report |
| [Fellespe/ekool `api_scheldure`](https://github.com/Fellespe/ekool/blob/5660bd54f12bdf761050d543fe1552c51eec5dd4/api_scheldure) | 2024-04-13 | a raw DWR timetable reply (`ee.ekool.model.calendar.Workday`, `LessonTime`, `Room`, …) |
| eKool public API Postman collection ([Wayback copy, 2025-01-27](https://web.archive.org/web/20250127133015/https://api-docs-public.ekool.eu/api/collections/17171853/UVsSNPRK?segregateAuth=true&versionTag=latest)) | published 2022-03-23 | official OAuth auth-code flow and the five identity endpoints on `api-v2.ekool.eu` |
| Wayback index of `*.ekool.eu` | — | `www.ekool.eu/dwr.interface.todoManager` exists (the web app's homework service); `api-v2.ekool.eu/user/me/*` answered 401 in 2024 |

`gh search code` for `postikana.ekool.eu`, `ekool.eu/auth/oauth`, `mkool-api`, `login.ekool.eu`
and `family.ekool.eu`, plus a repository search for `ekool`/`mkool`, found nothing else that
calls these APIs (the rest were school exercises and unrelated projects). GitHub's code index
misses some small repositories, so this list may be incomplete.

Links below use `C:` = ekool-cli, `T:` = ekool.ts, `V:` = VK-ekool-bot, `P:` = eKoolCpp,
`Q:` = QoLC.

## 3. Legacy mKool REST API — what the code shows

All of §3 is **confirmed from code only**, and all of it is from before 2023.

### 3.1 Token request (password grant)

```
POST https://ekool.eu/auth/oauth/token
Authorization: Basic base64("mKool:" + <client secret>)
Content-Type: application/x-www-form-urlencoded

grant_type=password&client_id=mKool&username=<eKool e-mail/username>&password=<password>
```

- URL and body: [C: EKoolParser.py L140-159](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L140-L159),
  [T: index.ts L53-82](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L53-L82),
  [V: ekool.py L72-85](https://github.com/ledjajev/VK-ekool-bot/blob/d6c6ac46efd86a1188ab96a390e49f79174dc5c8/ekool.py#L72-L85).
- `client_id=mKool` goes both in the form and as the Basic username. **No `scope` field is sent**;
  the response grants `scope: "read"`.
- The client secret is the 60-character string that all three clients hard-code, base64-encoded
  inside the Basic header ([C: L151](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L151)).
  It is public, but it is eKool's app secret, so it is deliberately not copied into this repo.
  Decode it from that line if the legacy route ever turns out to be alive.
- Response ([C: L155-157](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L155-L157)):
  `{"access_token": "<uuid>", "token_type": "bearer", "refresh_token": "<uuid>", "scope": "read"}`.
  UUID tokens, the Spring Security OAuth2 style. **No `expires_in`** appears in either sample, so
  the token lifetime is unknown.
- Failure: only "status is not 200" is handled. The error body is unknown.

### 3.2 Refresh

Only ekool.ts implements it ([T: L412-430](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L412-L430)).
It uses the same URL and the same Basic header:

```
grant_type=refresh_token&client_id=mKool&refresh_token=<refresh_token>
```

The response has the same four keys, and the client **replaces both tokens**, so rotation must be
assumed.

### 3.3 Data calls: one envelope for everything

Every data call is a **POST** whose path carries the parameters and whose JSON body is a fixed
"query base" ([C: L168-220](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L168-L220),
[T: L467-526](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L467-L526)):

```
POST https://postikana.ekool.eu/rest/json/<path…>
Authorization: Bearer <access_token>
Content-Type: application/json;charset=UTF-8

{"langCode": "et", "version": "4.6.6", "deviceId": "1234567", "userAgent": "Google Chrome",
 "checksum": "<md5>", "pushType": "1", "localTime": "<epoch>",
 "gradePush": true, "absencePush": true, "noticePush": true, "todoPush": true, "messagePush": true}
```

- `checksum = md5(langCode + reverse(version) + deviceId + userAgent + pushType + version + localTime)`,
  as a hex digest.
- `localTime`: ekool-cli sends epoch **seconds**, ekool.ts sends epoch **milliseconds**
  ([T: L519](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L519)).
  Both worked, so the server probably checks only that the checksum matches. `langCode` selects
  the language (the VK bot sends `ru`).
- `version: "4.6.6"` is the 2020 app version. The 2026 app is 7.x.

### 3.4 `/person` → student id

`POST …/rest/json/person` ([C: L125-128](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L125-L128)).
Shape from [T: interfaces.ts L9-43, L106-117](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/interfaces.ts#L9-L117):

- Top level: `id` (person id), `name1` (first name), `name2` (last name), `profileImgFn`, `premium`,
  `premiumPackageType`, `expirationDate`, `recurring`, `adData` (ad-targeting arrays of strings),
  `roles[]`.
- `roles[]`: `personId`, **`studentId`** (number), `firstName`, `lastName`, `schoolName`,
  `curriculumTypeId`, `newNotificationsAmount`, `isMe`, `gender`, `allowedPersonId`, `newGrades`,
  `newAbsences`, `newTodos`, `newFeedEvents`, `mKoolActivated`, `userProfileImhFn` (sic),
  **`timetableUrl`** (string or null; [V: commands.py L383-387](https://github.com/ledjajev/VK-ekool-bot/blob/d6c6ac46efd86a1188ab96a390e49f79174dc5c8/commands.py#L383-L387)).
- Every client takes `roles[0].studentId`. For a student account that is the student itself (a
  parent would have one role per child). Märkmik should pick the role with `isMe: true`, falling
  back to `roles[0]`.

### 3.5 `/todolist/{studentId}/{from}/{to}` → homework and tests

`POST …/rest/json/todolist/14400123456/28.09.2026/09.10.2026`, with dates as **`dd.mm.yyyy`**
([C: L34-39, L116-122](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L34-L122)).
Sample response in [C: L53-114](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/EKoolParser.py#L53-L114),
typed in [T: interfaces.ts L51-72, L179-185](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/interfaces.ts#L51-L185):

```json
{"startDate": "06.01", "endDate": "10.01", "weekNo": 1, "orderTimestampLong": 1578261600000,
 "eventList": [ { "id": 15159286778, "subjectName": "Matemaatika", "title": "…", "content": "",
                  "deadLine": "06.01.2020", "added": "13.12.2019 13:33", "isTest": false,
                  "isGraded": false, "isDone": null, "authorName": "…", "typeId": 1, … } ]}
```

| Need | Field | Notes |
|---|---|---|
| stable id | `id` | integer, 11 digits in 2020 |
| subject | `subjectName` | e.g. "Matemaatika", "A-võõrkeel (inglise keel)", "Inimeseõpetus" |
| date | `deadLine` | `dd.mm.yyyy`, date only, **no time** |
| title | `title` | plain text, can be long ("õpik(suur) ül. 355 ( 5;6;8)…") |
| body | `content` | **HTML** (links as `<a …>`), often `""` |
| test vs homework | `isTest` | `true` = test (kontrolltöö); `isGraded` is usually true with it |
| done flag | `isDone` | `null` when not done. Truthy when done ([V: commands.py L305-307](https://github.com/ledjajev/VK-ekool-bot/blob/d6c6ac46efd86a1188ab96a390e49f79174dc5c8/commands.py#L305-L307)). ekool.ts types it oddly as `unknown[]` |
| attachments | `teacherAttachments[]` | `{fileName, size, url}`, where `url` is relative to `https://ekool.eu` ([C: main.py L235-238](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/main.py#L235-L238)) |
| other | `authorName`, `added` (`dd.mm.yyyy HH:MM`), `orderTimestampLong`, `isHot`, `typeId` (always 1 in samples), `comments`, `url`; ekool.ts also lists `institutionId`, `todoRolePersonId`, `studentAttachments`, `allowStudentSubmission`, `commentIsPublic` |

Quirks:

- `startDate`/`endDate` have no year.
- `weekNo` is not the ISO week (2020-01-06 is ISO week 2, but the sample says 1).
- An item's `orderTimestampLong` is deadline midnight **one hour behind Tallinn time**
  (`1578265200000` = 2020-01-05T23:00Z for 06.01.2020). Never derive the date from it.
- Window size: ekool-cli asks Monday to Saturday, and the reply's `endDate` is the Friday. ekool.ts
  asks for 7 days, the VK bot for single days. **Longer windows were never shown to work.**
- Marking done: `POST …/todoChange/{weekStart}/{weekEnd}`, with the query base plus
  `studentId`, `todo: <bool>`, `todoId`
  ([T: L193-226](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L193-L226)).
  Märkmik does not write to eKool in v1.

### 3.6 `/feed/{studentId}` (+ `/feeditem/{studentId}/{eventId}`)

Returns an array ([C: Feed.py L3-46](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/Feed.py#L3-L46),
[T: interfaces.ts L228-258](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/interfaces.ts#L228-L258)).

- Items have `id`, `orderSeq` (epoch ms), `lastModified` (`dd.mm.yyyy HH:MM`), `itemType`,
  `actionType`, `hot`, `authorName`.
- `itemType: 1` = **grade**, with `abbr` (the grade), `gradeTypeId` (18 = course grade,
  [C: main.py L286-287](https://github.com/Rohukas/ekool-cli/blob/f4f12572acf0604c54d90edbaa2c420ffebf3cdf/main.py#L286-L287)),
  `subjectName`, `subjectId`, `lessonDate`, `termName`, `textContent`, `test`, `amendment`,
  `hasStatistics`, and in ekool.ts also `gradeInPoints`, `maxPoints`, `published`.
- `itemType: 20` = **advert**, which clients drop.
- Everything else is a notice with `title` and `content` (HTML). Its numeric `itemType` values are
  not in any code.
- `/feeditem/{studentId}/{id}` returns `{lastEvent, dbGrade?, previousEvents?, statistics?}`.

### 3.7 Other endpoints seen (none verified)

- `/family`, `/absences90Days/{sid}` (absences: `lessonDate`, `lessonNumber`, `code`,
  `subjectName`, …)
- `/lessons/{sid}/{from}/{to}` (`eventList` of `{id, courseName, subjectName, lessonNumber,
  eventDate(ms), events, authorName, name}`)
- `/timetable/{sid}/{from}/{to}`: ekool.ts types it as `{eventList, weekNo}` reusing the feed
  item type, which looks like a placeholder, so **the timetable shape is unknown**.
- `/grades/{sid}/{gradeId}`, `/subjGrade/{sid}/{subjectId}`,
  `/gradesheet/{sid}[/consolidated/{journalId}[/term/{gradeId}]]`, `/grades/dilbeh/{sid}/{typeId}`,
  `/thread/{id}`, `/premiumPackages`, `/addNewPersonalTask`, `/updatePersonalTask/{id}`,
  `/removePersonalTask/{id}`
- All in [T: index.ts L88-410](https://github.com/erik5227/ekool.ts/blob/69372ff75e2d1739909322ab10fe24d8369903ad/src/index.ts#L88-L410).
- Timetable alternatives: `roles[].timetableUrl` (a link, probably to a web timetable) and the web
  app's DWR timetable reply (Fellespe dump above).

### 3.8 Date and time formats

| Where | Format |
|---|---|
| request path dates | `dd.mm.yyyy` |
| `deadLine`, `lessonDate` | `dd.mm.yyyy` |
| `added`, `lastModified` | `dd.mm.yyyy HH:MM` (local time) |
| `startDate`, `endDate` of a todolist window | `dd.mm` |
| `orderSeq`, `orderTimestampLong`, `eventDate` | epoch milliseconds |
| personal task `deadline` (request) | `yyyy-mm-dd` |

## 4. What replaced it (2023 onward)

- **Web login**, eKoolCpp 2023 ([P: eKoolCpp.cpp L126-172](https://github.com/mar4ello6/eKoolCpp/blob/dd793f0d44de7936a81c10ade79842c50b26983c/eKoolCpp.cpp#L126-L172)):
  1. `GET https://login.ekool.eu/` for cookies.
  2. `POST /login` (form: `email`, `password`, `recaptcha=""`, `two_factor_code=""`, header
     `X-Requested-With: XMLHttpRequest`), which returns `{"success": true, "url": "…"}`.
  3. `GET url` sets the auth cookie and redirects students to `https://family.ekool.eu`.
  4. DWR: `POST /dwr/call/plaincall/__System.generateId.dwr` gives a script-session id, then e.g.
     `userAccountManager.getSessData.dwr`. The replies are JavaScript
     (`var s0=new ee.ekool.model.Person(); s0.name1="…";`).
- **May 2026** (QoLC): the user logs in inside a WebView at `https://login.ekool.eu/#/en/`
  ([Q: ui/LoginComponent.kt L19](https://github.com/SamTheProto/QoLC/blob/f1eebae2a1cf34799cb6aa7d86b797a481ed3b1d/app/src/main/java/com/samtheproto/qolc/subapps/EkoolCompanion/ui/LoginComponent.kt#L19)).
  - The app keeps the **`EKOOLSESSION` cookie (domain `ekool.eu`)**
    ([Q: EkoolServerInterface.kt L31-41](https://github.com/SamTheProto/QoLC/blob/f1eebae2a1cf34799cb6aa7d86b797a481ed3b1d/app/src/main/java/com/samtheproto/qolc/subapps/EkoolCompanion/EkoolServerInterface.kt#L31-L41))
    and calls `family.ekool.eu/dwr/call/plaincall/…` (`__System.generateId`,
    `userAccountManager.getSessData`, `dashboardManager.getDashboardData`, L60-201).
  - It checks the session every minute. The dashboard reply has grades, absences and journal
    messages (`jmList`). The model still has fields like `isTest`, `lessonDate`, `newTodos` and
    `timetableUrl`.
  - Homework lives in the DWR service `todoManager` (seen in the Wayback index). Its method names
    and parameters are **not in any code found**.
- **New mobile app** (illucc, 2024): `https://login.ekool.eu/oauth/authorize?client_id=<app>&redirect_uri=https%3A%2F%2Flogin.ekool.eu%2Fcallback&response_type=code&scope=mkool-api`,
  then a code exchange at `login.ekool.eu/oauth/token` with the app's `client_id` and
  `client_secret`, which it says to take "from `eu/ekool/app/BuildConfig.java`" after decompiling
  the APK. The data host for `mkool-api` is not stated. `api-v2.ekool.eu` is the likely candidate:
  it served `/user/me/package-features` and `/user/me/premium/detailed`, which are app features.
- **Official public API** (archived Postman collection):
  - Auth-code flow on `https://login.ekool.eu/oauth/authorize` and `/oauth/token`, with form
    fields `grant_type`, `client_id`, `client_secret`, `redirect_uri`, `code`.
  - Refresh with `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`.
  - Token response: `{"token_type": "Bearer", "expires_in": 604800, "access_token": "eyJ…",
    "refresh_token": "def502…"}`. The error body is `{error, error_description, hint, message}`,
    the `league/oauth2-server` format.
  - Endpoints: `GET https://api-v2.ekool.eu/generaldata`, `/user/me/basic`, `/user/me/roles`,
    `/user/me/schools`, `/user/me/children`. Scopes seen in the wild: `users-basic`,
    `users-schools`, `users-roles`, `users-children`, `opiq`. **No homework or timetable.**
    Clients are issued by e-mail to help@ekool.eu (the eKool help page "eKool API").
- **Login options now include 2FA** (e-mail/SMS, authenticator app), passkeys, and Google,
  Microsoft or Eliis sign-in (eKool help centre navigation, Wayback copy 2025-11-13). A 2FA-enabled
  account defeats any unattended password login.

## 5. Endpoint checks run (3 of the allowed 3)

Unauthenticated `POST` with an empty body and no credentials, redirects not followed. Run on
2026-09-24 01:24 EEST (`curl -s -o /dev/null -X POST -w '%{http_code}'`):

| Endpoint | Status | Reading |
|---|---|---|
| `https://ekool.eu/auth/oauth/token` | **404** | the legacy token endpoint is not served at this path any more; a live Spring OAuth endpoint gives 401 |
| `https://postikana.ekool.eu/rest/json/person` | **404** | the legacy data API is not routed; a live one would give 401 without a token |
| `https://login.ekool.eu/oauth/token` | **400** | the new OAuth server exists; 400 = missing `grant_type`, as `league/oauth2-server` answers |

These strongly suggest the legacy API is gone, but they do not prove it: a server could return 404
for requests without credentials. The live S3 settles it with one real login attempt.

## 6. Verified vs not

| Claim | Status |
|---|---|
| Legacy token request, refresh, query-base checksum, `/person`, `/todolist`, `/feed` shapes | **confirmed from code** (2020–2022). Not run by us |
| Legacy endpoints still work in 2026 | **contradicted** by two 404s (§5). Not confirmed with credentials |
| `login.ekool.eu/oauth/token` exists | **confirmed** (400) |
| Web login + `EKOOLSESSION` + `family.ekool.eu` DWR works in 2026 | **confirmed from code** (QoLC, May 2026). Not run by us |
| DWR method for homework (`todoManager.?`) and its reply shape | **unknown** |
| New app flow (`scope=mkool-api`) and its data endpoints | 2024 README only. Endpoints **unknown**. Needs the app's secret |
| Timetable source | **unknown**. Candidates: `roles[].timetableUrl`, `/timetable/…` (legacy), DWR timetable |
| The user's account has 2FA / uses password login | **unknown**. Ask the user |
| Access-token lifetime, refresh-token rotation (legacy) | unknown. The new server issues 7-day access tokens |

## 7. Adapter design

The design follows PLAN-IMPLEMENTATION.md WP-E1: `Adapter.fetch(cursor) -> (items, new_cursor)`
and `test_connection() -> {ok, summary, error}`. Credentials come only through `app/secrets.py`.

### 7.1 Auth, per transport

- **Legacy (only if S3 finds it alive)**:
  - Secrets `ekool.username`, `ekool.password`, `ekool.tokens` (JSON with both tokens).
  - On a 401 from a data call: refresh once. If the refresh fails: log in with the password once.
    If that fails with 400/401: status **Viga** "eKool ei võtnud kasutajanime/parooli vastu",
    and stop scheduled retries until the user saves new credentials (repeated failed logins may
    lock the account).
- **Web session (A)**:
  - Log in with Playwright (§8) using the stored username and password.
  - Store the `EKOOLSESSION` cookie (or the whole Playwright `storage_state`) encrypted as
    `ekool.session`. Reuse it until a DWR call says the session is invalid, then log in again.
  - Reusing the session matters: every fresh login may trigger an eKool "new login" e-mail or a
    2FA prompt, and 4 logins a day would spam the user.
  - If a 2FA prompt appears: status **Viga** "eKool küsib kinnituskoodi". The Ühendused card then
    offers a field for the code, and the worker enters it into the waiting login.
- **New app OAuth (B)**:
  - The same paste-the-redirect flow as Google (gmail.md §3): open the authorize URL, log in
    (2FA, Google and Smart-ID all work there), and paste the `https://login.ekool.eu/callback?code=…`
    address. The app exchanges the code at `login.ekool.eu/oauth/token`.
  - Access tokens last 7 days. Refresh tokens are league-style, so **always store the new
    refresh token**.
  - This needs the app's client secret from the APK. That is a ToS and ethics call for the user,
    not something to do silently.

Plan impact: the overview's decision "eKool login: username + password" holds for (A) and legacy.
(B) would turn the eKool card into a paste flow. The lead updates §15 after S3.

### 7.2 Polling

- Schedule 07:00, 13:00, 17:00, 21:00 Europe/Tallinn, plus "Värskenda" (overview §8), with ±5 min
  jitter. One eKool run at a time (the worker lock), never parallel requests.
- Window: Monday of last week to +5 weeks, fetched **one Monday–Sunday week per request**, because
  only windows of up to one week are known to work. That is 7 requests per run.
- Cursor: eKool has no change feed, so the cursor holds only `last_window_start`. Every run
  re-reads the window.
- Backoff on 5xx or network errors: 2 retries with 30 s/120 s delays, then give up until the next
  slot and record `last_error`.

### 7.3 Normalisation (same for every transport)

The transport yields `EkoolTodo(id, subject, deadline: date, title, content_html, is_test,
is_done, author, added, attachments)`. Then:

| events column | value |
|---|---|
| `uid` | `ekool:todo:{id}` (the eKool id is stable across fetches) |
| `source_items.external_id` | `todo:{id}`, with the raw JSON stored; a changed hash means update |
| `kind` | `kontrolltöö` if `is_test` else `kodutöö` |
| `title` | `"{subject}: {title}"`, whitespace collapsed, cut at 200 characters |
| `start_date` / `end_date` | `deadline` (parsed with `%d.%m.%Y`), `all_day=1`, `tzid=Europe/Tallinn` |
| `status`, `source`, `source_ref` | `kinnitatud`, `ekool`, `{id}` |
| `description` | `content_html` → sanitised text (nh3, then strip tags) + attachment list as absolute links |
| `note_id` | the subject note (below) |
| done | `is_done` truthy → an `event_done` row for that date; cleared when eKool clears it |

- Re-syncs never overwrite `user_edited` events (overview §8).
- An item that was inside the fetched window before and is missing now becomes `eemaldatud`,
  unless the user edited it. Nothing outside the window is touched.
- Optional fallback: some teachers enter tests as ordinary tasks. If `is_test` is false but the
  title matches `\b(kontrolltöö|KT|arvestuslik|test)\b` (case-insensitive), use `kontrolltöö`.
  Decide in S3 from real data. Do not build it before real data shows the need.

**Subject notes**:

- One note per distinct `subjectName`, titled exactly as eKool writes it ("A-võõrkeel (inglise
  keel)"), created through the `notes.create` contract when missing.
- Store the parenthesised part as an alias ("inglise keel") so that autolinking finds it.
- The mapping `subjectName → note_id` is looked up by title or alias, so renaming a note does
  not create a second one.

**Lessons (`tund`)**: only if S3 finds a timetable source (§6). Otherwise v1 has none.

**Feed**: grades are not calendar items (possibly a Täna widget later). Notices are free text and
could go through the e-mail extraction pipeline as untrusted input. That is out of v1.

### 7.4 `test_connection()`

It logs in (or reuses the session), reads the student's name and counts the todos in the next 14
days. It returns `{ok: true, summary: "Mari Näidis — 6 eelseisvat ülesannet/kontrolltööd"}`.

## 8. Fallback plan: Playwright on the web UI

Used for transport (A) and as the last resort (C). It runs in the worker with Playwright for
Python and `/usr/bin/chromium --no-sandbox`, headless, at the same schedule and at most 4 runs a
day.

1. **Login**: open `https://login.ekool.eu/#/et`.
   - Fill the e-mail and password fields by `type`/label. Vuetify's generated ids (`#input-45`,
     `#input-48` in [2023 code](https://github.com/SoggyDoggy/EkoolNavigator/blob/737d5c67bb034d65b168e3e5ea74d9aa35ea30d7/EkoolNavigatorStudent.py#L223-L232))
     are not stable.
   - Submit, then wait for navigation to `family.ekool.eu`.
   - Detect a 2FA or captcha screen and report it (§7.1).
2. **Session**: save `storage_state` (encrypted). Later runs start from it and skip the login while
   it is valid.
3. **Data**: navigate to the homework ("Ülesanded"/"Kodutööd") view for the current and coming
   weeks. Capture the DWR replies with `page.on("response")` for `/dwr/call/plaincall/todoManager.*`
   and parse them. The replies are plain JS assignments; QoLC's `DWRParser.kt` shows the approach.
   Parse into `EkoolTodo`. DOM scraping is only a fallback if the replies turn out to be
   unparseable.
4. **Direct DWR** (cheaper): once S3 has recorded the exact `todoManager` call (method name,
   `c0-param*`, `scriptSessionId` handling), the worker can POST it with the session cookie and
   skip the browser, as QoLC does for the dashboard. It re-logs in with Playwright only when the
   session expires.
5. Timetable: capture the DWR timetable reply the same way (its model classes are visible in the
   Fellespe dump).

Risks:

- The login form sends a `recaptcha` field. If eKool starts enforcing it, headless login fails and
  the user would have to supply a session some other way.
- The UI and DWR internals change without notice.
- eKool's terms: automated access to one's own data at a low rate, and no load (overview finding
  3). Keep the rate at 4 runs a day.

## 9. S3 checklist (with the user present)

1. Ask: does the eKool account use a password or Google/Smart-ID? Is 2FA on? Does eKool e-mail on
   every new login?
2. One password-grant POST to `https://ekool.eu/auth/oauth/token` with the real credentials. A 404
   again → drop the legacy route for good. (Only if alive: `/person`, `/todolist` for this week,
   `/feed`.)
3. Playwright login with HAR recording (`record_har_path` under `data/`, which is gitignored).
   Open the homework view (this and next week), the timetable, and the feed. List every XHR/DWR
   call and its reply format.
4. Decide the transport (§1 order). Write down the exact homework call and timetable call.
5. Measure how long `EKOOLSESSION` stays valid and whether reuse avoids 2FA and notification mails.
6. Anonymise one real reply per call into `tests/fixtures/ekool/`, replacing the synthetic files,
   and update the README there.
7. Update this file with what was verified (with output). The lead updates `CONTRACTS.md` and the
   Ühendused card text.
