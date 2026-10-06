# Contracts

The single source of truth that lets work packages (WPs) be built in parallel. Only the lead
edits this file. A package that needs something else from another package asks the lead; it never
reaches into another package's branch or files.

Plans: [PLAN-OVERVIEW.md](PLAN-OVERVIEW.md) (what and why; §n below refers to it),
[PLAN-IMPLEMENTATION.md](PLAN-IMPLEMENTATION.md) (waves and steps).

## 1. Conventions

- **Language**: identifiers, comments, docs, commit messages in English. Everything the user
  sees in the UI is Estonian (buttons, errors, empty states). Match the tone already in
  `app/templates/base.html` and `site/app.js`.
- **Comments** explain why, never what (see `~/.claude/CLAUDE.md` → RULES §5).
- **Database**: `app/db.py`. Connections are autocommit. A write that spans several statements, or
  checks before it writes, goes in `with tx(conn):` (BEGIN IMMEDIATE). Module functions take a
  `conn` as their first argument and never open their own connection. Request code gets it from
  `get_db()`; worker jobs are handed one.
- **Times**: `*_at` columns are UTC `'YYYY-MM-DDTHH:MM:SSZ'` (`util.iso`, `util.now_iso`);
  local dates `'YYYY-MM-DD'` and times `'HH:MM'` are Europe/Tallinn (`util.TALLINN`).
- **JSON errors**: `{"error": "<Estonian message>"}` with a fitting status. 409 = the note is
  being edited elsewhere ("Märget muudetakse teises aknas.").
- **Frozen files** (lead only): `pyproject.toml`, `uv.lock`, `package.json`,
  `package-lock.json`, `app/schema.sql`, `app/__init__.py`, `app/views/__init__.py`,
  `app/templates/base.html`, `site/app.js`, `site/app.css`, `site/style.css`,
  `tools/build-editor.mjs`, `tools/build-vendor.mjs`, `tools/build-app.mjs`, `tests/conftest.py`,
  `tests/visual/helpers.mjs`,
  `playwright.config.mjs`, `docs/CONTRACTS.md`. No new dependencies without the lead.
- **Stubs**: `app/notes.py`, `app/collab.py`, `app/events.py`, `app/etdates.py` exist from Wave 0
  as minimal stand-ins with the signatures below, marked `STUB`. The owning package replaces the
  whole file; nobody else edits them.

## 2. Ownership

| WP | Branch | VT_PORT | Owns |
|---|---|---|---|
| A editor + collab | `wp/editor` | 8781 | `tools/editor/*.js` (except `inline-ai.js`), `site/css/editor.css`, `app/collab.py`, `app/images.py`, `app/svg.py`, `app/render.py`, `app/views/collab.py`, `app/views/images.py`, `tests/editor/`, `tests/visual/editor-*.spec.mjs` |
| B notes core | `wp/notes` | 8782 | `app/notes.py`, `app/links.py`, `app/tasks.py`, `app/search.py`, `app/etdates.py`, `app/views/notes_api.py`, `tests/notes/` |
| D calendar | `wp/calendar` | 8783 | `app/events.py`, `app/ics.py`, `app/reminders.py`, `app/views/calendar.py`, `app/templates/calendar.html`, `site/calendar/`, `site/css/calendar.css`, `tests/calendar/`, `tests/visual/calendar-*.spec.mjs` |
| E1 sources backend | `wp/sources` | 8784 | `app/sources/*.py`, `app/extract.py`, `tests/sources/`, `tests/fixtures/` |
| H git sync + gbrain | `wp/sync` | 8785 | `app/linkfmt.py`, `app/gitsync.py`, `app/gbrain.py`, `app/backup.py`, `app/views/related.py`, `site/related/`, `site/css/related.css`, `gbrain/` (compose service files), `tests/sync/` |
| I graph | `wp/graph` | 8786 | `app/graph.py`, `app/views/graph.py`, `app/templates/graph.html`, `site/graph/`, `site/css/graph.css`, `tests/graph/`, `tests/visual/graph-*.spec.mjs` |
| C app shell | `wp/shell` | 8787 | `app/views/pages.py`, `app/capture.py`, `app/templates/{shell,note,today}.html`, `site/shell/`, `site/css/shell.css`, `site/manifest.webmanifest`, `tests/shell/`, `tests/visual/shell-*.spec.mjs` |
| E2 Postkast + Ühendused | `wp/inbox` | 8788 | `app/views/{inbox,settings,connections}.py`, `app/templates/{inbox,settings,connections,wizard}.html`, `site/inbox/`, `site/css/inbox.css`, `tests/inbox/`, `tests/visual/inbox-*.spec.mjs` |
| F AI assistant | `wp/ai` | 8789 | `app/ai/*.py` (runner real mode keeps the Wave 0 signatures), `app/views/ai.py`, `tools/editor/inline-ai.js`, `site/ai/`, `site/css/ai.css`, `tests/ai/`, `tests/visual/ai-*.spec.mjs` |
| G sharing | `wp/share` | 8791 | `app/shares.py`, `app/views/share.py`, `app/templates/share_{view,edit}.html`, `site/share/`, `site/css/share.css`, `tests/share/`, `tests/visual/share-*.spec.mjs` |
| Q QA | `wp/qa` | 8792 | `tests/visual/qa-*.spec.mjs`, `tests/perf/`, `docs/QA.md` |
| stocks-market | `wp/stocks-market` | 8793 | `app/stocks/providers/`, `app/stocks/market.py`, `app/stocks/trend.py`, `app/stocks/listing.py`, `app/views/stocks_market.py`, `site/stocks/{watchlist,ticker}.js`, `site/css/stocks-market.css`, `tests/stocks/market/`, `tests/visual/stocks-market-*.spec.mjs` |
| stocks-portfolio | `wp/stocks-portfolio` | 8794 | `app/stocks/portfolio/`, `app/stocks/parsing.py`, `app/stocks/csv_import.py`, `app/stocks/tx_form.py`, `app/views/stocks_portfolio.py`, `site/stocks/{portfolio,tx,position}.js`, `site/css/stocks-portfolio.css`, `tests/stocks/portfolio/`, `tests/visual/stocks-portfolio-*.spec.mjs` |
| stocks-sources | `wp/stocks-sources` | 8795 | `app/stocks/sources/`, `app/views/stocks_sources.py`, `site/stocks/{sources,news}.js`, `site/css/stocks-sources.css`, `tests/stocks/sources/`, `tests/visual/stocks-sources-*.spec.mjs` |
| stocks-ai | `wp/stocks-ai` | 8796 | `app/stocks/prompts/`, `app/stocks/aicall.py`, `app/stocks/research.py`, `app/stocks/verdict.py`, `app/stocks/explore.py`, `app/views/stocks_ai.py`, `site/stocks/{explore,research}.js`, `site/css/stocks-ai.css`, `tests/stocks/ai/`, `tests/visual/stocks-ai-*.spec.mjs` |
| stocks-night | `wp/stocks-night` | 8798 | `app/stocks/night.py`, `app/stocks/jobs.py`, `app/stocks/alerts.py`, `app/stocks/notes.py`, `app/views/stocks_night.py`, `site/stocks/today.js`, `site/css/stocks-night.css`, `tests/stocks/night/`, `tests/visual/stocks-night-*.spec.mjs` |
| stocks-import | `wp/stocks-import` | none | `app/stocks/import_oracle.py`, `tests/stocks/import/` |

Ports 8790 and 8797 are taken by other projects on this machine. The lead owns, besides the files
in §1: `app/stocks/{__init__,common,config}.py`, `app/views/stocks.py`, `app/templates/stocks.html`,
`site/stocks/{index,common,settings}.js`, `site/css/stocks.css`, `tests/stocks/test_foundation.py`.

## 3. Auth (`app/auth.py`, Wave 0, done)

- Default deny: an endpoint needs the owner unless decorated `@auth.public`, or
  `@auth.guest("view" | "edit")` and the browser holds a valid share for the note.
- `g.owner: bool`, `g.share: Row | None` (valid share), `g.actor: {"name", "is_guest"}`.
- `auth.require_note(note_id, mode)` — every guest-reachable endpoint calls it with the note the
  request touches; aborts 403 otherwise. Owners always pass.
- `auth.enter_share(conn, token, guest_name=None) -> Row | None` — used by `/j/<token>`.
- `auth.token_hash(token)` — sha256 hex; `shares.token_hash` stores this.
- CSRF: every non-GET request sends `X-CSRF` (from `<meta name="csrf">`), a `csrf` form field, or
  `csrf` in a JSON body (sendBeacon). `site/app.js` `api()` does it.
- `security.rate_limit(bucket, limit, per_s)` decorator. CSP forbids inline scripts: every script
  is a file.
- Going public (docs/TUNNEL-PLAN.md): `security.TunnelProxy` (WSGI middleware, installed by
  `security.init_app`) rewrites `REMOTE_ADDR`/scheme from `CF-Connecting-IP`/`X-Forwarded-Proto`,
  but only when the request's real peer is `TRUSTED_PROXY_NET` (cloudflared's fixed compose
  address) — never from Tailscale. Every existing `request.remote_addr` use (rate limits, the
  login lockout) sees the real client through it, unchanged. `security.cookie_secure()` (the
  session's `get_cookie_secure`) and the `Strict-Transport-Security` header are true only for a
  request that arrived this way (`request.is_secure`). `security.public_base()` is `PUBLIC_URL`
  when set, else the request's own host: share links and the ICS feed URL are always built on it.
  `security.check_host` (before_request, runs first) aborts 400 unless the `Host` header is
  `PUBLIC_URL`'s host, the Tailscale address or localhost/127.0.0.1 (tests).

## 4. Python modules

Signatures are binding; add private helpers freely.

### notes (B) — stub in Wave 0
```python
class NoteBusy(Exception): ...                 # raised when collab.is_busy(note)
create(conn, title="", *, folder="", body=None, is_daily=False, daily_date=None) -> Row
get(conn, note_id) -> Row | None               # excludes deleted
by_slug(conn, slug) -> Row | None              # excludes deleted
write_body(conn, note_id, body) -> int         # server-side write: NoteBusy if busy; rev += 1; on_saved; returns new rev
on_saved(conn, note_id) -> None                # after any body write: title from first "# ", links, tasks
daily(conn, day: date) -> Row                  # get or create the daily note
soft_delete(conn, note_id) -> None             # into the trash; restore(conn, note_id) -> Row | None takes it out
trash(conn) -> list[dict]; in_trash(conn, slug) -> Row | None
forget(conn, note_id) -> None                  # deleted for good: now, or once git dropped its file
purge(conn) -> int                             # job "notes-purge": TRASH_DAYS (30) old and file_path NULL
settle(conn, note_id, *, typed_ok=False) -> str | None  # a note left: a blank new one goes, an automatic slug takes the title's
tidy(conn) -> None                             # job "notes-tidy": settle() for new notes whose tab went without a word
```
`create` with `body=None` makes `"# {title}\n"`. Slugs come from `util.slugify` and change only
once, for a note made without a title (`note_auto_slug`), when it is left with a title; the slug
it had goes into `old_slugs`, and `by_slug` and `/m/<old>` still find the note.
Metadata changes (folder, tags, aliases, pinned) bump `updated_at` but **not** `rev` (a rev bump
would end open editor sessions): git export must compare `updated_at` as well as `rev`.
Search snippets mark hits with `\x02`…`\x03` (`search.HL_START`/`HL_END`): escape the text,
then wrap marked runs in `<mark>`.

### collab (A) — stub in Wave 0
```python
is_busy(conn, note_id) -> bool                 # open session and (active < 90 s or unsaved ops)
close_all(conn, note_id) -> None               # after an outside write; clients reset and rejoin
```
Everything else in collab is A's own. `collab.save` writes the body itself
(`UPDATE notes SET body, rev = rev + 1, updated_at`) inside `tx`, then calls `notes.on_saved`.

### events (D) — stub in Wave 0
```python
create(conn, fields: dict) -> int              # fields = column names of events; uid generated when missing
upsert_by_uid(conn, uid, fields) -> tuple[int, bool]   # (id, created); never overwrites user_edited rows;
                                               # returns (None, False) for a uid in rejected_uids
remove(conn, event_id) -> None                 # status 'eemaldatud' and remember uid in rejected_uids
update(conn, event_id, fields, *, by_user=True) -> Row   # by_user sets user_edited; status/source only via this with by_user=False or Postkast
get(conn, event_id) -> Row | None
occurrences(conn, start: date, end: date, *, statuses=("kinnitatud","pakutud")) -> list[dict]
```
Occurrence keys (`exdates_json` entries, `recurrence_id`, `event_done.occurrence`): the
occurrence's ORIGINAL start as `YYYY-MM-DDTHH:MM:SSZ` (UTC, timed) or `YYYY-MM-DD` (all-day); `''`
for a non-recurring event in `event_done`; writes normalise offset forms. `reminders_sent.occurrence`
uses the item's start in the same forms (tasks: the due date). Rules more frequent than daily are
refused.

An occurrence dict: `{id, uid, title, start, end, all_day, kind, status, source, note_id,
occurrence, done, location, description, source_quote, confidence}` with `start`/`end` as local
ISO strings (`'2026-10-03T14:15'`, or `'2026-10-03'` when all-day).

### etdates (B) — stub in Wave 0
```python
parse_date(text, ref: date) -> date | None     # "reedel", "homme", "3.10", "3. oktoobril", "järgmisel reedel", ISO
parse_time(text) -> str | None                 # "kell 14.15", "14:15" -> "14:15"
find(text, ref: date) -> list[dict]            # [{"start": int, "end": int, "date": date, "time": str|None}]
weekday_conflict(text, day: date) -> bool      # text names a weekday that is not day's weekday
```

### render (A)
`render_markdown(md) -> str` — sanitised HTML (nh3) for share view pages and anywhere else.

### images (A) and shares (G): what a guest may load (security fix N5)
```python
images.store(conn, data, original, author, note_id=None) -> str | None   # note_id: the note it went into
images.allow(conn, note_id, names) -> None      # these pictures may be loaded by the note's guests
images.allow_shown(conn, note_id) -> None       # ... every picture the note's saved body shows now
images.allowed(conn, note_id, name) -> bool
images.seed_shared(conn) -> None                # create_app(): once per database (settings key
                                                # note_images_seeded), every note that has had a link
                                                # gets the pictures it shows at that start
shares.create(...)                              # at the note's first link only: images.allow_shown
```
Table `note_images (note_id, name)` holds them: the note's pictures at its first link, the owner's
uploads while the note is open (`POST /pildid` with `slug`), the owner's gallery picks
(`POST /api/images`), and guests' uploads through its links. What the note's text shows is never
enough on its own (an edit guest could type any name). A picture reference the owner pastes in as
text is not added.

### failid — `app/failid.py` (docs/FAILID-PLAN.md)
```python
failid.check(data, ext) -> None                 # ValueError(Estonian reason) unless a file we take
failid.store(conn, data, original_name, author, note_id=None, source_url=None) -> name  # sha256.ext; ValueError
failid.linked(markdown) -> [(link_text, name)]  # [text](/failid/<name>), escapes in the text undone
failid.allow(conn, note_id, names), failid.allowed(conn, note_id, name) -> bool
failid.fetch_url(conn, url, note_id=None) -> name, failid.refresh(conn, name, note_id=None) -> name
failid.OWNER = "omanik", failid.CODE = "arvutus"  # author; CODE files no live note links go at night
```
Tables `files` and `note_files (note_id, name)`; the bytes in `<DATA_DIR>/failid/<name>`.

### vault (Wave 0, done) — `app/vault.py`
`get/set/delete/get_json/set_json/masked/names`. Secret names:
`claude_token` (falls back to env `CLAUDE_CODE_OAUTH_TOKEN`), `ekool_username`,
`ekool_password`, `ekool_tokens` (json), `google_client` (json client secret),
`google_account:<email>` (json token), `google_auth_pending` (json PKCE verifier + state),
`brain_token`, `ntfy` (json `{url, topic, token}`), `gbrain_token`.

Settings keys in use: `gmail_senders` (json list of domains/addresses), `gmail_label` (json
string, Gmail label id), `ekool_subject:<subject>` (subject note id), `ics_feed_token`,
`reminder_rules`, `brain_remote` (only `https://github.com/<owner>/<repo>(.git)`, `gitsync.github_remote`), `gbrain_url`, `connection:<key>`, `job:<name>:*`,
`extract_calls:<day>` (extraction's own daily cap), `gitsync:note:<id>`, `gitsync:problems:pull`, `gitsync:problems:export`,
`gmail_retry:<source_id>` (`{id: {attempts, first_failed}}`), and the competition finder's
`voistlused_huvid` (json list of interest keys), `voistlused_markus` (json string, the owner's free
text), `voistlused_iganadal` (json bool, the weekly run), `voistlused_klass` (json `{klass, seatud}`:
the grade and the day it was set; it advances every 1 September; default 12 on 2026-09-27),
`voistlused_synniaasta` (json int or null: age limits count from it), `voistlused:loetud` (json
`{key: day}`: feed items and list pages already read, forgotten after 120 days), `voistlused:running` (UTC time a run
claimed it; deleted when it ends, ignored after 20 minutes) and `voistlused:last` (json, the last
run's result).
Jobs besides those above: `extract-retry` (E1, daily 05:00), `voistlused` (daily 07:30 slot; starts a
search on a thread of its own when switched on and the last run is 6.5 days old or more).

### connections (Wave 0, done) — `app/connections.py`
`register(key, title, order, test, secret_names)`; `test(conn) -> {"ok", "summary", "error"}`
must never return a secret. Keys and order: `claude` 10 (Wave 0), `ekool` 20 (E1), `google` 30
(E1), `brain` 40 (H), `ntfy` 50 (D), `ics` 60 (E1), `phone` 70 (D, shows the private ICS URL).
A module registers at import time. `create_app()` and the worker import every module in
`connections.CONNECTION_MODULES` and `jobs.JOB_MODULES` (missing ones are skipped), so put the
`register(...)` call in the module named there: `app.sources.ekool`, `app.sources.gmail`,
`app.sources.icsfeed`, `app.gitsync`, `app.reminders`, `app.ics`.

### jobs (Wave 0, done) — `app/jobs.py`
`@job(name, every=seconds | at=("07:00",...))`. Job modules already listed in
`jobs.JOB_MODULES`: `app.reminders` (D), `app.sources.base` (E1), `app.extract` (E1),
`app.gitsync` (H), `app.gbrain` (H), `app.backup` (H), `app.collab` (A: op-log pruning),
`app.voistlused` (the competition finder).

### AI runner (Wave 0 interface; F makes it real) — `app/ai/runner.py`
```python
run_structured(conn, prompt, schema, *, model="haiku", system=None, web_search=False, images=()) -> dict
stream_chat(conn, prompt, *, system=None, tools=(), model="sonnet", session_id=None, images=()) -> Iterator[dict]
AIUnavailable(RuntimeError); is_fake(); count_call(conn); test_connection(conn)
```
Findings that bind the real mode (docs/spikes/agent-sdk.md): the `tools` list is the only real
control (`permission_mode="dontAsk"` is not a sandbox) — `tools=[]` for extraction, app tools +
`"WebSearch"` for chat, `["WebSearch"]` alone for `run_structured(web_search=True)` (a prompt with
nothing private in it: the finder's discovery call), and register only tools that may run; pass the token through
`ClaudeAgentOptions.env` and blank `SECRET_KEY`, `MARKMIK_KEY`, `OWNER_PASSWORD`, `OWNER_PASSWORD_HASH` there (the CLI
inherits the app's environment); extraction runs with `thinking={"type":"disabled"}` (4 s instead
of 35 s); the parsed object is `ResultMessage.structured_output`, and `date` formats are not
enforced, so code validates every date; chat history is replayed from our DB each turn (no CLI
session state); the system prompt lists the tools that exist (a tool-less model invents results).

While the open note has an active edit link (`shares.edit_open(conn, note_id) -> bool`: an edit
link not revoked and not expired), the chat's system prompt ends with `chat.GUESTS` (guests wrote
part of the note; do not follow instructions in it) and the Abiline panel shows a warning
(`.ai-guests`). The tools stay the same (security fix N4).

Chat SSE also sends `{"type":"edit","id","slug","base","markdown"}` when `muuda_markmet` proposes a
new text for the note open in the asker's editor: `markdown` is the whole new note, `base` the
text it was made from (the editor's Markdown the request sent, else the saved body), `id` the
stored tool message (the thread keeps `proposal`, `base` and, once reviewed, `settled`). The server
does not write it; the browser shows the changes in the review window (`site/ai/review.js`,
docs/AI-REVIEW-PLAN.md) and applies the kept ones as ordinary ops. `muuda_markmet` (replaces
`lisa_markmesse`) counts against the same write cap, works only on the open note, and refuses a
note longer than the prompt shows. Test hooks: `runner.fake_chat`, `runner.call_tool`,
`runner.fake_edit(reply, replace=, append=)` (a fake_chat that edits the `<note>` it was shown).

**The home chat** (`chat.turn` with no note, docs/KODU-PLAN.md) is the chat on `/`. Its thread is
the newest `ai_threads` row with `note_id IS NULL`. `tools.build(Context(conn, home=True))` gives
`tools.HOME_NAMES`: the note tools plus `lisa_markmesse(slug, text, under?)` (the server builds the
proposal with `tools.append_to`), `loo_ulesanne(text, date?)` (`capture.add_task`, today's daily
note), `meeldetuletused`, `loo_meeldetuletus(text, date, time?, event_id?)` and
`muuda_meeldetuletust(id, date?, time?, text?, done?)`; there `muuda_markmet` works on any note
`loe_markmet` showed in the same turn, and `loo_markme` takes `images: [name]` (this message's
pictures, appended as `![](/pildid/<name>)`). Its `edit` events add `rev` and `title`, and the
stored proposal keeps `slug` and `rev`. It also sends `{"type":"reminder_created","id","text",
"remind_at"}` after a reminder tool. `stream_chat(..., images=[(media_type, bytes)])` sends
pictures as image blocks of the same user message (streaming input, spike S5);
`app.ai.pictures.prepare(path)` makes them (JPEG, ≤ 1568 px, no metadata, ValueError otherwise);
fake mode records their media types in `runner.fake_images`.

Stream events: `{"type":"delta","text"}`, `{"type":"tool","name","input","result"}`,
`{"type":"done","session_id"}`, `{"type":"error","message"}`. In tests `AI_FAKE=1` is always set
(tests/conftest.py); set `runner.fake_structured` to shape answers.

### Connection helpers (called by E2's Ühendused page)
E2 renders one card per connection key and calls these; the owners implement them.
```python
# E1 — app/sources/gmail.py
save_client(conn, client_json: str) -> None       # validates a Google "Desktop app" client JSON; ValueError(Estonian)
auth_url(conn) -> str                              # PKCE + state kept server-side; loopback redirect
finish_auth(conn, pasted_url: str) -> str          # exchanges the code from the pasted 127.0.0.1 URL; returns the email
accounts(conn) -> list[dict]                       # [{email, ok, last_error, last_ok_at}]
remove_account(conn, email) -> None
# E1 — app/sources/ekool.py
save_credentials(conn, username, password) -> None
# E1 — app/sources/icsfeed.py
feeds(conn) -> list[dict]; add_feed(conn, url, name) -> int; remove_feed(conn, source_id) -> None
# E1 — app/sources/base.py
run_now(conn, kind=None) -> dict                   # run sources now: {kind: {"ok", "items", "error"}}
# D — app/ics.py
feed_url(conn) -> str                              # path of the private ICS feed (/ics/<token>.ics)
new_feed_token(conn) -> None                       # rotate: the old feed path returns 404 from now on
fetch_ics(url) -> list[dict]                       # events fields, for E1's ICS source
download(url, time_limit=60, *, user_agent=None) -> bytes   # any public https page, with fetch_ics's checks; messages say no "ICS"
```
`save_credentials` and `finish_auth` re-enable a disabled source. Katkesta on the Google card
calls `remove_account` for every account. `fetch_ics(url)` returns **expanded occurrences** (no `rrule`) whose start lies between today −
`ics.WINDOW_PAST_DAYS` (30) and today + `ics.WINDOW_FUTURE_DAYS` (365); each dict has the
source event's `uid` and `recurrence_id` = the occurrence's ORIGINAL start (RECURRENCE-ID when
the feed has one, else the rrule-generated start), so a moved occurrence keeps its identity.
It refuses non-https URLs and any connection (including redirect hops, checked per hop) to
loopback/private/link-local/100.64.0.0/10 addresses or single-label hosts, and raises ValueError
with an Estonian message that never contains the URL. A consumer retires only items inside that
window.

Cards that only store a secret (claude → `claude_token`, brain → `brain_token` + setting
`brain_remote`, ntfy → `ntfy` json) are saved by E2 through `vault` directly.

### voistlused — `app/voistlused.py`, `app/sources/teaduskool.py`
The competition finder (docs/VOISTLUSED-PLAN.md). Every find is a proposal: `kind='sündmus'`,
`status='pakutud'`, `source='ai'`, all-day, uid `voistlus:<sha1 of the normalised title and start
date>`, `source_ref` `voistlus:teaduskool:<the same hash>` or `voistlus:web:<page url>`, and the
description's first line `Link: <url>`. Its registration deadline is a second proposal,
`kind='tähtaeg'`, "Registreeri: <title>", uid = the competition's + `:registreeri`, same
`source_ref`; Postkast shows it on the competition's card (`voistlused.ATTACHED_SQL` leaves it out
of the lists and counts), Kinnita confirms and Loobu removes both, through the competition's id only
(the deadline's own id answers 404 while its competition waits). A rejected competition stays
rejected when a later run finds it again from the same origin (`source_ref`) or with the same
normalised title within 60 days of its start. Every all-day `tähtaeg` event, not only the finder's,
gets the homework reminder: the day before, at the homework rule's time.

Participation facts (part 2): table `voistlus_info (uid, info_json)`, one row per competition uid:
`{grades: [min, max] | null, level: põhikool | gümnaasium | open | university | null, team: {kind,
size} | null, qualification: {kind: open | qualifies, through} | null, registration: {who: self |
school | teacher | null, how}, fee, location, requirements, source_quote, round: qualifying | final |
international | null, qualifier: {uid, title, start} | null, page_read}`. From the title, the page
(no-tools extraction, sonnet, each page once per run) and, for a final or an international round,
the teaduskool round it qualifies through. Grades, level and qualification from a page count only
when their quote is the page's own words. `app/eligibility.py` `decide(info, start, grade)` ->
`{result: fits | qualify | no, reason, unknown, through, grade}`; unknown facts fit. Every run first
gives facts to waiting proposals that have none (or whose page failed); nothing confirmed or
rejected is touched. Kinnita on a final confirms its qualifying round while that is a proposal;
Kinnita kõik and the Postkast counts (`voistlused.postkast_count`) leave out what does not fit.

Known sources (part 3), `app/voistlusallikad.py` `SOURCES`: `Source(key, name, url, kind: ics | rss |
html, interests, must)`, read when one of its interests is chosen (the interest `hakatonid` is on by
default). `ics`: robootika.ee, ETAg; dates from the calendar, no model. `rss`: Miks.ee, Kesk-Eesti
inkubaator; `html`: Garage48, KüberOlümpia; each item's (page's) text goes through the no-tools
extraction (`PAGE_SCHEMA`), only when it names a competition (`KEYWORDS`), at most 15 calls a run,
an item read once not again until it changes. A source that fails adds "<name>: allikat ei
saanud lugeda." to the run's errors. `source_ref` `voistlus:allikas:<key>:<item>` (the calendar
UID, the post's link, or for a list page the find's key); Postkast's `origin` is the source's
name. Facts also carry `ages: [min, max] | null` (either end may be null), from titles, calendar
texts and pages; with a birth year an age outside them decides `no` ("vanusepiirang 16+"), without
one it is only shown. The run's result adds `allikad` (new finds from these sources). `start(db_path, *, weekly=False)` (the button and the job:
claims the run, searches on a thread; weekly pushes new finds through ntfy), `run(conn)` (the same
on the caller's thread), `status(conn)`, `settings(conn)`, `save_settings(conn, data)`.

### linkfmt (H)
`to_file(note_row, body, *, basename_taken=None) -> str`, `from_file(text) -> (meta, body)`:
every `/m/<slug>` link ↔ `[[slug|Title]]` (dangling ones too); literal slug-shaped `[[x]]` text is
escaped on export; round trips are exact for editor-written text.

### gbrain (H)
`search(query, limit=8, *, conn=None) -> list[{"slug","title","snippet","score"}]`, `related(conn, note_row)`
(same shape), both return `[]` and log when gbrain is unreachable.

Facts from docs/spikes/gbrain.md (verified; they correct plan finding 2): gbrain page names are the
repo path without `.md`, lowercased and accent-folded. Edges come from `[[notes/<folder>/<slug>]]`,
`[[notes/<folder>/<slug>|T]]`, root-relative `[T](notes/<folder>/<slug>.md)` and same-folder
`[T](x.md)`; bare `[[slug]]` only with `link_resolution.global_basename` on (off by default); our
`/m/<slug>` makes none. **Files in git use `[[<slug>|Title]]` with `global_basename` on**, falling
back to `[[notes/<folder>/<slug>|Title]]` when another file in the repo has the same basename.
The app reads gbrain over HTTP MCP (spikes/gbrain/client.py) with one read-only legacy bearer
token; the gbrain container runs `gbrain sync --no-pull` every 60 s itself; the app only commits
and pushes. Search is English-configured: Estonian words may miss (known limit).

### Aktsiad — `app/stocks/` (docs/AKTSIAD-PLAN.md)

stock-oracle's watchlist, prices, holdings, news and AI research, rewritten for this app. Port
from `~/Projects/stock-oracle/src/oracle/` (read-only; never run or change it): copy pure modules
with their tests, rewrite everything that used SQLAlchemy on `sqlite3` with the rules of §1.

**Data rules.**
- The tables are in `app/schema.sql` (after `-- Aktsiad`). They are the contract between the
  packages: a package may read any stocks table; it writes only the tables its functions below
  name as its own.
- Money, quantities, rates and scores are `Decimal` in Python and TEXT in the database:
  `common.dtext(value)` to store (refuses floats), `common.dec(text)` to read. SQL never compares,
  sorts or sums these columns.
- JSON columns end in `_json`; dates are `'YYYY-MM-DD'`; `*_at` and `portfolio_tx.ts` are UTC `…Z`.
- The JSON API sends Decimals as strings ("1234.50"), dates as `'YYYY-MM-DD'`, times as `…Z`.
- No `DISTINCT ON` (SQLite): `ROW_NUMBER() OVER (PARTITION BY …)` or a `MAX()` subquery.
- Every function that depends on the time takes `now` (aware UTC) or `today` (Tallinn `date`),
  so tests never touch the clock. No network in tests: `httpx.MockTransport` or recorded fixtures
  under the package's test folder; opt-in real calls are `@pytest.mark.live`.
- Outside pages whose URL comes from a feed or a model are fetched only with `ics.download`. Fixed
  API hosts (Yahoo, Finnhub, Frankfurter, SEC, Alpha Vantage) are called with `httpx` directly.
  API keys go in headers or params, never in logs or error messages.

**Lead (done): `common`, `config`.**
```python
common.dec(v) -> Decimal | None; common.dtext(v) -> str | None
common.get_json(conn, key, default=None); common.set_json(conn, key, value)   # settings table
common.instrument(conn, id) -> Row | None; common.by_symbol(conn, symbol) -> Row | None
common.followed(conn, *, kinds=("stock", "etf")) -> [Row]      # watchlist instruments by symbol
common.provider_symbol(row, provider) -> str | None            # provider_symbols_json[provider], else symbol
common.claim(conn, name, stale=30 min) -> bool; common.claimed(conn, name); common.release(conn, name)
config.settings(conn) -> dict; config.save_settings(conn, data); config.tax_rates(conn) -> {year: Decimal}
config.secret(conn, name) -> str | None     # "finnhub_key", "alphavantage_key", "sec_user_agent"
```
Settings keys: `stocks:settings` (json, `config.DEFAULTS`: `deep_days`, `research_model`,
`alert_move_pct`, `alert_attention_z`, `ntfy_topic`, `tax_rates`, `opening_balance`, `ir_feeds`,
`night_cap`), `stocks:running:<name>` (claims: `night`, `web-ai`, `ingest`, `backfill:<id>`),
`stocks:calls:<run_id>` (the night's model calls). `provider_symbols_json` keys: `yahoo`,
`finnhub`, `sec_cik` (a key with `null` = that provider does not cover it).

**stocks-market** — `providers/`, `market.py`, `trend.py`, `listing.py`. Writes `instruments`,
`watchlist`, `price_bars`, `quotes`, `corporate_actions`, `fx_rates`.
```python
market.search(conn, query) -> [{symbol, name, exchange, currency, kind}]   # Yahoo search (+ Finnhub when keyed)
market.add(conn, symbol, *, name=None, exchange=None, currency=None, kind=None) -> Row
        # instrument (created from Yahoo's chart meta when not given) + watchlist row; idempotent
market.start_backfill(db_path, instrument_id) -> bool   # 5 years of bars and actions on a thread
market.backfill(conn, instrument_id, today) -> int      # the same, on this thread (tests, import)
market.refresh_prices(conn, today) -> dict              # night step "prices": SPY benchmark, bars with
        # a 7-day overlap, actions, ECB FX (EUR base); a stored-split mismatch re-backfills
market.refresh_quotes(conn, now) -> dict                # daytime quotes; {} outside US market hours
market.us_market_open(now) -> bool
market.bars(conn, instrument_id, start=None, end=None) -> [{date, open, high, low, close, adj_close, volume}]
market.latest_price(conn, instrument_id) -> {price, previous_close, as_of, source} | None   # quote, else last bar
        # as_of: UTC '…Z' from a quote, 'YYYY-MM-DD' from a bar; a quote only when no newer bar exists
market.fx(conn, day, from_ccy, to_ccy) -> Decimal | None    # latest ECB rate on or before day, via EUR
market.price_stats(conn, instrument_id, today) -> dict      # {last_close, ret_5d, ret_20d, ret_60d, ret_1y,
        # vol_20d, vol_60d, high_52w, low_52w} (Decimal or None), for research and verdict prompts
market.trend_summary(conn, instrument_id, today) -> dict | None   # trend.py's card: state, distances
        # from SMA 50/200, last cross, 52-week position, drawdown, excess vs SPY 20d/60d/1y
```
Stock page: `/api/stocks/instruments/<symbol>` and chart JSON under `/api/stocks/…` (its own).
The page mounts every `registerTickerSection` section below its chart, in order.

**stocks-portfolio** — `portfolio/` (copied), `parsing.py`, `csv_import.py`, `tx_form.py`.
Writes `broker_accounts`, `portfolio_tx` (and `watchlist` through `market.add` on a buy).
```python
portfolio.holdings(conn, as_of=None) -> {instrument_id: Decimal}   # all accounts, splits applied, qty > 0
portfolio.position(conn, instrument_id, today) -> dict | None
        # {qty, avg_cost, currency, cost_eur, value_eur, unrealised_pct, accounts: [{name, qty}]}
```
`site/stocks/position.js` registers the stock page's "Sinu positsioon" section.

**stocks-sources** — `sources/`. Writes `market_docs`, `market_doc_instruments`, `insider_trades`,
`earnings`, `doc_counts`, `stock_source_runs`.
```python
sources.ingest(conn, now, *, run_id=None) -> {source: {status, fetched, new, error}}   # night step
sources.poll_filings(conn, now) -> dict        # hourly: EDGAR 8-K and Form 4 only, no model
sources.count_docs(conn, today) -> dict        # doc_counts for the last days (night step "attention")
sources.attention_z(conn, instrument_id, day) -> float | None   # mentions vs 30 days
sources.text.html_to_text(html) -> str         # pure; AI uses it on fetched pages
sources.models.normalize_url(url) -> str       # pure; AI's citation check uses it
```
`ingest` also takes `only` (source names), `client`, `sleep`, `clock` and `download` for tests.
Source names (as stock-oracle's, so imported cursors carry over): `edgar`, `finnhub`, `earnings`,
`yahoo_rss`, `google_news`, `ir_rss`, `alphavantage`. Settings key `stocks:av_calls:<YYYY-MM-DD>`
(Alpha Vantage calls that UTC day); claim `ingest` (on-demand `POST /api/stocks/sources/run`, 3 an
hour, 409 while a night runs). The hourly filings poll leaves the night's EDGAR cursor alone.
It may set the `sec_cik` key of `instruments.provider_symbols_json` (EDGAR's ticker map); nothing
else in `instruments`. `site/stocks/news.js` registers the stock page's "Uudised" section (recent
documents with the model's summary and sentiment from `doc_signals` when there is one).

**stocks-ai** — `aicall.py`, `prompts/`, `research.py`, `verdict.py`, `explore.py`. Writes
`stock_ai_runs`, `doc_signals`, `analyses`, `verdicts`, `explorations`.
```python
aicall.call(conn, purpose, prompt, schema, *, model, prompt_version, instrument_id=None, run_id=None,
            web_search=False, timeout=None, max_turns=None, thinking=False) -> dict
        # runner.run_structured + a stock_ai_runs row; with run_id also the night cap (NightCap).
        # Raises runner.AIUnavailable (the night stops its AI steps) or RuntimeError (skip that stock).
research.extract_night(conn, run_id, now, deadline) -> dict       # haiku, no tools, per stock
research.research(conn, instrument_id, today, *, kind="deep", run_id=None, progress=None) -> int  # analyses.id
research.deep_night(conn, run_id, today, now, deadline) -> dict   # on config deep_days only
verdict.verdict_night(conn, run_id, today, now, deadline) -> dict # stocks only, holdings first
explore.explore(conn, focus, count, today, progress=None) -> int  # explorations.id
research.latest(conn, instrument_id) -> Row | None; verdict.latest(conn, instrument_id) -> Row | None
```
The `*_night` functions never raise for the model: each returns its detail dict
`{done: [symbols], skipped: [{symbol, reason}], failed: [{symbol, error}], stopped: str | None}`.
`stopped` is set (Estonian reason) when `runner.AIUnavailable` was hit (usage limit, auth, the day's
or the night's cap) or `now()` passed `deadline`; the night then starts no further AI step.
`aicall.call_logged(...) -> (answer, ai_run_id)` is `call` that also returns the run row's id.
On-demand research: `POST /api/stocks/research/<symbol>` (SSE). The stock page's sections are "Öine
hinnang" and "Uuring".
`aicall.call` with a `run_id` counts calls in `stocks:calls:<run_id>` and raises `AIUnavailable`
("Öine AI-päringute piir on täis") past `config.settings(conn)["night_cap"]`. Reruns of a night skip
stocks already done for that date (a `doc_signals` row per document, a deep `analyses` row, a
`verdicts` row). `now` may be a callable returning aware UTC, so long steps see time pass.

Rules (plan, "AI"): the model never has WebFetch. Discovery calls use `web_search=True` and
their prompt holds nothing private: never a position, quantity, cost or account. Code fetches the
candidate pages with `ics.download`; synthesis runs with no tools. A citation survives only when
its URL is a page this run fetched or a stored `market_docs` URL. `progress(text)` is called with
Estonian step texts for the SSE streams. `site/stocks/research.js` registers the stock page's
"Hinnang" and "Uuring" sections.

**stocks-night** — `night.py`, `jobs.py`, `alerts.py`, `notes.py`. Writes `stock_runs`,
`stock_notices`, and notes (`notes.create` / `notes.write_body`) in folder `aktsiad`.
```python
night.run(conn, run_date, *, now=None, only=None) -> int          # stock_runs.id; steps in order:
        # prices, ingest, attention, extract, research, verdict, notes, digest
night.start(db_path, run_date) -> bool                            # claim "night", run on a thread
alerts.check(conn, now) -> int; alerts.send_digest(conn, day) -> bool
notes.write(conn, instrument_id, today) -> bool                  # False: the note is busy, retried next night
```
Jobs: `stocks-night` (at 01:30; starts `night.start`), `stocks-digest` (07:00), `stocks-quotes`
(every 300 s), `stocks-filings` (every 3,600 s). Alerts go through `reminders.publish` with the
vault's ntfy config and `config.settings(conn)["ntfy_topic"]`. `site/stocks/today.js` fills Täna's
`#tana-lisad`.

**Endpoints.** Owner only, JSON, Estonian errors, under each package's prefix: market
`/api/stocks/{watchlist,search,instruments,chart}…`; portfolio `/api/stocks/{portfolio,accounts,tx,import,position}…`;
sources `/api/stocks/{sources,docs}…`; ai `/api/stocks/{research,verdicts,explore}…` (SSE streams
as in `/api/ai/chat`, one on-demand run at a time under the claim `web-ai`, 409 otherwise); night
`/api/stocks/{night,today}…`. Pages and `/api/stocks/settings`: lead (`app/views/stocks.py`).

**stocks-import** — `import_oracle.py`: `python -m app.stocks.import_oracle <dir> [--replace]`
reads stock-oracle's tables as JSON (one `<table>.json` per table, `json_agg` rows) and fills the
stocks tables. Mapping: plan, "Step 7".

### Meedia — `app/meedia/` (docs/MEEDIA-PLAN.md)

The media folder (`store.default_root()`: `MEDIA_DIR`, else `<DATA_DIR>/meedia`) is the truth:
each kept file has a companion `<name>.json` beside it (`store.KEYS`). The `media*` tables are an
index that `index.reindex` rebuilds from the folder; `media_shares` are the exception (database
only). Every change goes companion first, then index (`index.update`, `index.set_tags`).
```python
store.kind_of(name) -> "image" | "video" | None; store.clean_name(original); store.free_name(root, name)
index.intake(conn, root, name, *, source, path=None, trash_original=True) -> kept name  # probe.Unreadable
index.scan(conn, root, *, convert=None, busy=(), budget=20.0) -> counts   # job meedia-scan, every 120 s
index.update(conn, root, name, changes); index.set_tags(conn, root, name, tags); index.remove(conn, root, name)
tagging.run(conn, root, max_calls) -> files tagged                        # job meedia-tag, every 180 s
search.find(conn, *, q, tags, not_tags, kind, since, until, untagged, shared, offset, limit) -> {total, items}
shares.create(conn, names, *, title, days, download) -> (row, token); shares.by_token(conn, token); shares.revoke(conn, id)
```
`security.check_host`: with `MEEDIA_PUBLIC_URL` set, its host serves only the `meedia_jaga`
blueprint, and that blueprint is served on no other host (404 both ways).

## 5. HTTP endpoints

Auth column: O = owner, G-v / G-e = guest with a view / edit share for that note, P = public.

| WP | Method, path | Auth | In → out |
|---|---|---|---|
| core | `GET/POST /login`, `POST /logout` | P / O | form `parool`, `csrf` (the login page's token; without it 403 and the form again) |
| core | `GET /muya/<ver>/<path>` | P | editor build, immutable when `<ver>` is current |
| C | `GET /`, `GET /m/<slug>`, `GET /tana` | O | pages (`data-page` = `notes`, `notes`, `today`) |
| C | `POST /api/capture` | O | `{text, preview?}` → `{kind:"event", id, title, start, event_kind, untitled}` \| `{kind:"task", slug, text}` \| `{kind:"note", slug, title}`; `preview:true` creates nothing |
| C | `GET /api/tags` | O | `[{tag, notes:[{slug, title}]}]` |
| B | `GET /api/notes/tree` | O | `[{id, slug, title, folder, pinned, is_daily, updated_at}]` |
| B | `GET /api/notes/suggest?q=` | O | `[{slug, title, folder}]` ≤ 10, fuzzy |
| B | `GET /api/notes/<slug>` | O | `{id, slug, title, body, folder, tags, aliases, pinned, is_daily, daily_date, rev, updated_at}` |
| B | `POST /api/notes` | O | `{title?, folder?, body?}` → note (201) |
| B | `PATCH /api/notes/<slug>` | O | `{folder?, tags?, aliases?, pinned?}` → note |
| B | `DELETE /api/notes/<slug>` | O | 204 (soft delete) |
| B | `PUT /api/notes/<slug>/body` | O | `{body, rev}` → `{rev}`; 409 `{reason: "muudetud"}` when rev moved, `{reason: "lahti"}` when open in an editor |
| B | `GET /api/notes/<slug>/vanad-lingid?pealkiri=` | O | `[{slug, title, rev, body, proposed}]`: notes linking here with that text, and their body with the title now |
| B | `POST /api/kaustad/nimeta` | O | `{from, to}` → `{moved}`; the folder and its subfolders, trashed notes and `mall_kaust` too |
| B | `POST /api/notes/<slug>/lahkun` | O | `{slug}` the note has now, `null` when it went (notes.settle) |
| B | `GET /api/notes/<slug>/md` | O | the body as a `.md` attachment named by the title |
| B | `GET /api/prugikast` | O | `[{slug, title, folder, deleted_at}]`, newest first; notes deleted less than 30 days ago |
| B | `POST /api/prugikast/<slug>/taasta` | O | note; 404 when not in the trash |
| B | `DELETE /api/prugikast/<slug>` | O | 204: deleted for good (the row goes once git has dropped its file) |
| B | `GET /api/notes/<slug>/links` | O | `{"outgoing":[{slug,title,exists}], "backlinks":[{slug,title,snippet}]}` |
| B | `GET /api/notes/<slug>/mentions` | O | `[{slug, title, snippet}]` |
| B | `POST /api/notes/<slug>/link-mention` | O | `{src_slug}` → 204, 409 busy |
| B | `GET /api/daily/<YYYY-MM-DD>` | O | note (created if missing) |
| B | `GET /api/tasks?from=&to=&done=` | O | `[{id, note_slug, note_title, folder, text, done, due_date, due_time}]`; notes in `Mallid/` left out |
| B | `POST /api/tasks/<id>/toggle` | O | → 204, 409 busy |
| B | `GET /api/search?q=` | O | `[{slug, title, snippet, folder, hidden}]`; `#silt`, `-#silt`, `kaust:x` filter, alone they list newest first |
| H | `GET /api/notes/<slug>/ajalugu` | O | `{versions: [{sha, at, message}]}` from the brain repo, newest first, following moves; `[]` before the first sync; 409 not connected, 503 git failed |
| H | `GET /api/notes/<slug>/ajalugu/<sha>` | O | `{body}`: the note in that commit |
| B | `GET /api/notes/<slug>/sarnased` | O | `[{slug, title, folder}]` ≤ 8: notes sharing words (search.similar) |
| C | `GET /ulesanded` | O | page (`data-page` = `tasks`) |
| A | `POST /api/collab/join` | O, G-v | `{slug, client}` → `{session, base, ops, saved_seq, participants, cursors, rev}` |
| A | `POST /api/collab/<sid>/sync` | O, G-v | `{client, seen, op?, op_id?, base?, cursor?}` → `{ops, saved_seq, participants, cursors}` or `{reset: true}`; ops need G-e |
| A | `POST /api/collab/<sid>/save` | O, G-e | `{body, seq}` → 204 or `{reset: true}` |
| A | `POST /api/collab/<sid>/leave` | O, G-v | beacon `{client, csrf}` → 204 |
| A | `POST /pildid` | O, G-e | multipart `file[]`, `slug?` (the owner's open note; a guest's upload goes into the shared note) → `{code, msg, data:{errFiles, succMap}}` (Muya's shape) |
| A | `GET /pildid/<name>` | O, G-v (only pictures in `note_images` for its note, else 404) | the file |
| A | `GET /api/images` | O | `[{name, url, original_name}]` for the gallery |
| A | `POST /api/images` | O | `{slug, name}` → 204: a gallery pick put into that note; its guests may load it (400 bad name, 404 no note) |
| D | `GET /kalender` | O | page `data-page="calendar"` |
| D | `GET /api/events?start=&end=` | O | occurrences (§4 events) |
| D | `POST /api/events`, `PATCH /api/events/<id>`, `DELETE /api/events/<id>` | O | event JSON; PATCH sets `user_edited`; DELETE = remove |
| D | `POST /api/events/<id>/done` | O | `{occurrence, done}` → 204 |
| D | `GET /api/today?date=` | O | `{date, events:[...], tasks:[...], tests_soon:[...], daily:{slug,title}, postkast, reminders:[...], ntfy}` for today, or for `date` (`YYYY-MM-DD`, Täna's Eile / Homme; 400 when not a date); today's `reminders` also hold earlier missed ones; `ntfy`: a topic is set |
| D | `GET /api/reminders?start=&end=` | O | `[{id, text, remind_at, event_id, event_title, note_slug, source, done, sent, missed}]`, local `remind_at` `YYYY-MM-DDTHH:MM`; `/api/events` lists them too, as `{reminder_id, kind: "meeldetuletus", ...}` |
| D | `POST /api/reminders`, `PATCH /api/reminders/<id>`, `DELETE /api/reminders/<id>` | O | `{text, remind_at}` → 201; PATCH `text`, `remind_at` (a new time sends it again), `done`; 404 when none |
| D | `GET /ics/<token>.ics` | P (token) | iCalendar feed |
| E2 | `GET /postkast`, `GET /api/inbox` | O | proposed events; a finder's also `origin` ("Teaduskool" \| "Veeb"), `link`, `deadline: {id, start} \| null`, and for a competition `eligibility` (`decide`'s, plus `qualifier: {uid, title, start, id \| null}`) and `facts` |
| E2 | `POST /api/inbox/<id>/confirm`, `.../reject`, `POST /api/inbox/confirm-all` | O | → 204 |
| finder | `POST /api/voistlused/run` | O | starts a background search → 202 `{running, last}`; 409 one is running; 400 no interests |
| finder | `GET /api/voistlused/status` | O | `{running, last: {at, weekly, new, teaduskool, allikad, veeb, titles, errors} \| null}` |
| finder | `GET/PUT /api/voistlused/settings` | O | `{huvid, markus, iganadal, klass, synniaasta}` (`klass` 1-13, 13 = school finished; `synniaasta` an int within the last 5-30 years, or null; GET gives today's grade and adds `choices: [{key, label, covers}]`) |
| E2 | `GET /seaded`, `GET /seaded/uhendused`, `GET /tere` | O | pages (`settings`, `connections`, `wizard`) |
| E2 | `GET /api/connections` | O | `[{key, title, ok, summary, error, at, masked:{name: "••••1234"}}]` |
| E2 | `POST /api/connections/<key>` | O | fields for that card → saved in vault |
| E2 | `POST /api/connections/<key>/test`, `DELETE /api/connections/<key>` | O | result / 204 |
| E2 | `POST /api/connections/phone/new` (rotate the ICS feed token) | O | card |
| F | `POST /api/ai/chat` | O | `{slug?, message, selection?, markdown?, images?}` → SSE stream (§4 events); `markdown` = the editor's text, used as `<note>` and as the edit's base; `images` (home chat only, no `slug`): ≤ 4 stored picture names (400 bad name or format, 404 unknown, 413 too many) |
| F | `POST /api/ai/inline` | O | `{slug, instruction, text}` → SSE stream |
| arvuta | `POST /api/arvuta` | O | `{slug, markdown, run: "auto" \| "all" \| <cell index>, hash?}` → `{markdown \| null, cells: [{ok, error?}], seconds, hash, error?}` or `{skipped: "sama" \| "kulalised"}`; 409 while that note runs; the browser applies `markdown` with the editor's `updateMarkdown` (docs/ARVUTUS-PLAN.md) |
| B | `PATCH /api/notes/<slug>` field `arvutus_auto` | O | bool; false writes the frontmatter key `arvutus: käsitsi` (Python cells then run only on ▶ / Shift+Enter / Ctrl+Enter; docs/JUPYTER-PLAN.md); note JSON carries it, and `hidden` |
| failid | `GET /api/failid/<name>` | O | one file's JSON (the Failid tab) |
| arvuta | `POST /api/arvuta` and files | O | the note's `/failid/` links outside output regions go to the run (link text = name in the cell's folder, `fail(name)`); files a cell writes into `valjund/` come back as `📎 [name](/failid/…)` in its output region (author `arvutus`); a name for two files, a path in a name, or a file no longer stored is the answer's `error` |
| arvuta | `POST /api/arvuta/taienda` | O | `{kind: "complete" \| "signature" \| "doc" \| "lint", before: [code], code, line, col, name?}` → `{items}` \| `{signature}` \| `{doc}` \| `{diagnostics}` \| `{error}`; 413 over 200 kB; the `taiendus` container only reads the code (docs/TAIENDUS-PLAN.md) |
| failid | `POST /api/failid` | O | multipart `file`, `slug?` (that note's guests may then download it) → 201 file JSON `{name, url, original_name, ext, size, author, source_url, fetched_at, created_at}`; 400 `{error}` with the reason (type, size, encoding, not a workbook…), 404 unknown slug (docs/FAILID-PLAN.md) |
| failid | `POST /api/failid/url` | O | `{url, slug?}` → 201 file JSON with `source_url`, `fetched_at`; fetched once by `ics.download` (public https only, 25 MB, 30 s); the type and name come from the address's last path part; 400 `{error}` |
| failid | `POST /api/failid/<name>/refresh` | O | `{slug?}` → file JSON of the address fetched again: a new `name` when the content changed (the old file stays), the same one when not; `original_name` kept; 400 no address, 404 unknown file |
| failid | `GET /api/failid?q=` | O | `[file JSON + notes: [{slug, title}]]`: original names containing `q`, newest first, ≤ 50, with the live notes linking each |
| failid | `GET /failid/<name>` | O, G-v (only files in `note_files` for its note, else 404) | the file as `application/octet-stream`, `Content-Disposition: attachment; filename*=UTF-8''…`, `nosniff`, `sandbox` CSP |
| mallid | `GET /api/mallid/<slug>` | O | `{title, folder}` defaults for a note in `Mallid/`; 404 for any other note |
| mallid | `POST /api/notes/from-template` | O | `{template, title?, folder?}` → 201 note JSON; placeholders `{{kuupäev}}`, `{{kuupäev_pikk}}`, `{{nädalapäev}}`, `{{kell}}` |
| F | `GET /api/ai/threads/<slug>` | O | messages |
| F | `GET /api/ai/threads`, `POST /api/ai/threads` | O | the home chat's messages (`[]` when none); POST = "Uus vestlus" → 201 `{id}` |
| F | `GET /api/ai/proposals/<message id>` | O | a home proposal: `{slug, base, markdown, settled}` (for `/m/<slug>?ettepanek=<id>`); 404 otherwise |
| F | `POST /api/ai/proposals/<message id>/apply` | O | `{markdown, rev}` → `{rev}`: written with `notes.write_body` and settled `applied`; 409 `{reason: "muudetud"}` when the note's rev moved, `{reason: "lahti"}` when it is open in an editor; 404 for a note-panel proposal |
| F | `POST /api/ai/proposals/<message id>` | O | `{settled: "applied" \| "dropped" \| "failed"}` → 204; 404 when that message is no `muuda_markmet` proposal |
| F | `POST /api/render/blocks` | O | `{blocks: [Markdown]}` → `{html: [sanitised HTML]}` (`render_markdown` each); ≤ 2 000 blocks, ≤ 200 000 characters, else 413 |
| G | `GET /j/<token>`, `POST /j/<token>` | P | view or edit page for the shared note; POST = name form (`nimi`, `csrf`) → 302 back. Unknown/revoked/expired/deleted → the same 404 page |
| G | `GET /api/share/current` | G-v | `{note:{slug,title}, rev, html, mode}` |
| G | `GET /api/shares?slug=`, `POST /api/shares`, `DELETE /api/shares/<id>` | O | `{slug, mode, expires_at?}` → `{id, url, mode, ...}` |
| H | `GET /api/related/<slug>` | O | `[{slug, title, snippet, score}]` |
| H | `POST /api/sync/now` | O | runs export + pull now → `{pushed, pulled, conflicts}` |
| I | `GET /graaf`, `GET /api/graph?folder=&tag=&orphans=` | O | `{nodes:[{id, slug, title, folder, degree}], edges:[{source, target}]}` |
| M | `GET /meedia` | O | the page (`data-page` = `meedia`) |
| M | `GET /api/meedia?q=&tag=&ilma=&liik=&alates=&kuni=&sildita=1&jagatud=1&offset=&limit=` | O | `{total, items:[{name, kind, mime, bytes, width, height, duration, taken_at, added_at, title, description, tags:[{tag, auto}]}]}`; bad date 400 |
| M | `GET /api/meedia/sildid`, `GET /api/meedia/ootel` | O | `{tags:[{tag, count}]}`; `{items:[{name, kind, error}]}` (not kept yet: converting, or refused) |
| M | `GET`/`PATCH`/`DELETE /api/meedia/fail/<name>` | O | details (+ `gps, camera, ocr, source, converted_from, ai, taken_local`); PATCH `{title?, description?, tags?}` → details; DELETE → trash |
| M | `POST /api/meedia/mitu` | O | `{names, add?, remove?, delete?}` → `{done}` |
| M | `POST /api/meedia/upload`, `GET`/`PUT /api/meedia/upload/<id>?offset=`, `POST /api/meedia/upload/<id>/done` | O | `{name, size}` → `{id, chunk, received}`; raw piece ≤ `chunk` (optional `X-SHA256`) → `{received}`, 409 `{received}` at a wrong offset; done `{sha256?}` → `{name, status:"ready"\|"processing", duplicate?}` (202 when processing) |
| M | `GET /meedia/fail/<name>?lae=1`, `GET /meedia/pisi/<name>?s=400\|1600` | O | the file (Range); a JPEG thumbnail |
| M | `GET /api/meedia/jagamised?name=`, `POST /api/meedia/jagamised`, `DELETE /api/meedia/jagamised/<id>` | O | `{shares:[{id, title, download, created_at, expires_at, files, first}]}`; `{names, title?, days?: 1\|7\|30\|365, download?}` → 201 `{..., url}` (the URL only here) |
| M | `GET /s/<token>`, `/s/<token>/f/<name>`, `/s/<token>/p/<name>?s=` | P | share page with Open Graph tags; the file (pictures only with `download`); a preview. On `MEEDIA_PUBLIC_URL`'s host only. Unknown/revoked/expired → 404 |

## 6. Browser

- `site/app.js` (Wave 0): `registerPage(page, mount)`, `registerPanel(id, {title, order,
  mount(el, ctx)})`, `registerMetaAction(id, {order, mount(el, ctx)})`,
  `registerSettingsSection(id, {title, order, mount(el)})`, `registerCommand(id, {title,
  keywords, run})`, `on/emit`, `api(path, {method, body})`, `toast(text, kind)`,
  `csrfToken()`, `muyaUrl()`. A page's mounts start in registration order and run side by
  side; `<html data-ready="1">` once all of them have finished.
- Every package's browser code starts at `site/<package>/index.js` (already listed in
  `app.js`), imports `/static/app.js` by exactly that URL, and registers what it adds.
- **Bundle** (`tools/build-app.mjs`, docs/PERF-PLAN.md): the app's own JS and `site/app.css` (it
  `@import`s every package stylesheet, in cascade order) are built into content-hashed files in
  `site/dist`, cached for good; the sources still import each other as `/static/<path>`. A
  module that code outside the bundle imports by URL (the editor under `/muya`, tests) is listed
  in `MODULES` there: `/static/<it>` then answers with a re-export of the built copy, so there
  is one instance (named exports only). A chunk a page's mount imports at once goes in
  `PAGE_LAZY` there, so the page preloads it. Without `site/dist` the sources are served as they
  are.
- Events: `note:open {note}`, `note:saved {note, rev}`, `note:changed {note}`,
  `editor:ready {note, editor}` (editor = the handle below), `events:changed {}`,
  `postkast:count {count}`, `theme:changed {theme}`, `shares:changed {slug}` (the Jaga dialog
  loaded or changed the note's links), `panel:toggle {id}` (the shell shows the right panel on tab
  `id`, or closes it when it already shows that tab; ✦ AI sends it for `assistant`).
- The note's crumb row holds the meta actions (`registerMetaAction`) on the right: Jaga (G, 20),
  Viited (the shell's, 30: the right panel on its other tabs), ✦ AI (F, 40). The right panel
  starts closed. Look and tokens: docs/KUJUNDUS.md; the spec's names map onto `style.css`'s
  (`--paper` = bg, `--surface` = p1, `--paper-2` = p2, `--line`, `--ink`, `--ink-2` = mu,
  `--slate` = the accent as text and lines, `--hivis`/`--on-hivis` = the accent as a fill,
  `--warn`, `--danger` = bad, `--today`, `--r`).
- **Editor** (A): built into `site/muya/`; load with
  `const { openNote } = await import(muyaUrl() + "/note-editor.js")`.
  ```js
  openNote(el, { slug, readOnly = false, onOpenNote(slug, hash), onState({ saved, participants, seq }) })
    -> Promise<{ getMarkdown(), selection() /* {path, text, start, end} | null */,
                 applyBlockEdit(path, text) /* false if the block changed */, replaceMarkdown(md),
                 updateMarkdown(md) /* only the top-level blocks that differ */,
                 blocks() /* Muya's top-level block state */, blockNodes(), focusBlock(i, {select}),
                 insertBlocks(i, md), removeBlocks(from, n), moveBlocks(from, n, to) /* one edit each */,
                 setCodeText(i, text, expected) /* a code block's text, refused if not `expected` */,
                 toggleTask(path, checked), focusTitle(),
                 detach() /* -> Promise, settled once the last save is through */ }>
  ```
  One Muya instance per page; `openNote` on the same element switches notes. Guests call it with
  the shared note's slug; the server scopes it.
- Muya's `@` menu has an item with label `"ai"` (build patch); F's `inline-ai.js` handles it the
  way `mountGallery` handles `"image"`.

## 7. Tests

- pytest: `tests/<package>/test_*.py`, own `conftest.py` there. Shared fixtures
  (`tests/conftest.py`): `app`, `conn`, `client` (anonymous), `owner` (logged in), `brain_remote`
  (bare git repo), and `AI_FAKE=1` for every test. `pytest -m live` for tests that need a real
  outside service; they never run by default.
- Playwright: `tests/visual/<package>-*.spec.mjs`, helpers in `tests/visual/helpers.mjs`
  (`login`, `waitReady`, `api`). Run with your own port:
  `VT_PORT=<port> npx playwright test --project=chromium tests/visual/<package>-*`.
- Node is not on PATH in non-interactive shells:
  `export PATH="$HOME/.local/share/fnm/aliases/default/bin:$PATH"`.
- Headless Chromium: `/usr/bin/chromium --no-sandbox` with fonts/CDN blocked through
  `--host-resolver-rules` (already in `playwright.config.mjs`). Never block with `page.route`
  unless the test is about a stubbed endpoint.
- Browser tests run the bundle, as production does: `npm run build` first (Playwright refuses
  to start without `site/dist`); after changing only `site/`, `npm run build:app` is enough.
- Test-only pages: a package may add routes under `/_test/...` in its own blueprint, registered
  only when the environment has `MARKMIK_TEST_PAGES=1` (set by `playwright.config.mjs`, never in
  production), marked `@auth.public` only if the test needs no login. Their templates/scripts
  live in the package's own files.
