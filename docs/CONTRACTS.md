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
  `app/templates/base.html`, `site/app.js`, `site/style.css`, `tools/build-editor.mjs`,
  `tools/build-vendor.mjs`, `tests/conftest.py`, `tests/visual/helpers.mjs`,
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

Ports 8790 and 8797 are taken by other projects on this machine.

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
```
`create` with `body=None` makes `"# {title}\n"`. Slugs come from `util.slugify` and never change.
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
`gmail_retry:<source_id>` (`{id: {attempts, first_failed}}`).
Jobs besides those above: `extract-retry` (E1, daily 05:00).

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
`app.gitsync` (H), `app.gbrain` (H), `app.backup` (H), `app.collab` (A: op-log pruning).

### AI runner (Wave 0 interface; F makes it real) — `app/ai/runner.py`
```python
run_structured(conn, prompt, schema, *, model="haiku", system=None) -> dict
stream_chat(conn, prompt, *, system=None, tools=(), model="sonnet", session_id=None) -> Iterator[dict]
AIUnavailable(RuntimeError); is_fake(); count_call(conn); test_connection(conn)
```
Findings that bind the real mode (docs/spikes/agent-sdk.md): the `tools` list is the only real
control (`permission_mode="dontAsk"` is not a sandbox) — `tools=[]` for extraction, app tools +
`"WebSearch"` for chat, and register only tools that may run; pass the token through
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
| B | `GET /api/notes/<slug>/links` | O | `{"outgoing":[{slug,title,exists}], "backlinks":[{slug,title,snippet}]}` |
| B | `GET /api/notes/<slug>/mentions` | O | `[{slug, title, snippet}]` |
| B | `POST /api/notes/<slug>/link-mention` | O | `{src_slug}` → 204, 409 busy |
| B | `GET /api/daily/<YYYY-MM-DD>` | O | note (created if missing) |
| B | `GET /api/tasks?from=&to=&done=` | O | `[{id, note_slug, note_title, text, done, due_date, due_time}]` |
| B | `POST /api/tasks/<id>/toggle` | O | → 204, 409 busy |
| B | `GET /api/search?q=` | O | `[{slug, title, snippet}]` |
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
| D | `GET /api/today?date=` | O | `{date, events:[...], tasks:[...], tests_soon:[...], daily:{slug,title}, postkast}` for today, or for `date` (`YYYY-MM-DD`, Täna's Eile / Homme; 400 when not a date) |
| D | `GET /ics/<token>.ics` | P (token) | iCalendar feed |
| E2 | `GET /postkast`, `GET /api/inbox` | O | proposed events |
| E2 | `POST /api/inbox/<id>/confirm`, `.../reject`, `POST /api/inbox/confirm-all` | O | → 204 |
| E2 | `GET /seaded`, `GET /seaded/uhendused`, `GET /tere` | O | pages (`settings`, `connections`, `wizard`) |
| E2 | `GET /api/connections` | O | `[{key, title, ok, summary, error, at, masked:{name: "••••1234"}}]` |
| E2 | `POST /api/connections/<key>` | O | fields for that card → saved in vault |
| E2 | `POST /api/connections/<key>/test`, `DELETE /api/connections/<key>` | O | result / 204 |
| E2 | `POST /api/connections/phone/new` (rotate the ICS feed token) | O | card |
| F | `POST /api/ai/chat` | O | `{slug?, message, selection?, markdown?}` → SSE stream (§4 events); `markdown` = the editor's text, used as `<note>` and as the edit's base |
| F | `POST /api/ai/inline` | O | `{slug, instruction, text}` → SSE stream |
| F | `GET /api/ai/threads/<slug>` | O | messages |
| F | `POST /api/ai/proposals/<message id>` | O | `{settled: "applied" \| "dropped" \| "failed"}` → 204; 404 when that message is no `muuda_markmet` proposal |
| F | `POST /api/render/blocks` | O | `{blocks: [Markdown]}` → `{html: [sanitised HTML]}` (`render_markdown` each); ≤ 2 000 blocks, ≤ 200 000 characters, else 413 |
| G | `GET /j/<token>`, `POST /j/<token>` | P | view or edit page for the shared note; POST = name form (`nimi`, `csrf`) → 302 back. Unknown/revoked/expired/deleted → the same 404 page |
| G | `GET /api/share/current` | G-v | `{note:{slug,title}, rev, html, mode}` |
| G | `GET /api/shares?slug=`, `POST /api/shares`, `DELETE /api/shares/<id>` | O | `{slug, mode, expires_at?}` → `{id, url, mode, ...}` |
| H | `GET /api/related/<slug>` | O | `[{slug, title, snippet, score}]` |
| H | `POST /api/sync/now` | O | runs export + pull now → `{pushed, pulled, conflicts}` |
| I | `GET /graaf`, `GET /api/graph?folder=&tag=&orphans=` | O | `{nodes:[{id, slug, title, folder, degree}], edges:[{source, target}]}` |

## 6. Browser

- `site/app.js` (Wave 0): `registerPage(page, mount)`, `registerPanel(id, {title, order,
  mount(el, ctx)})`, `registerMetaAction(id, {order, mount(el, ctx)})`,
  `registerSettingsSection(id, {title, order, mount(el)})`, `registerCommand(id, {title,
  keywords, run})`, `on/emit`, `api(path, {method, body})`, `toast(text, kind)`,
  `csrfToken()`, `muyaUrl()`. `<html data-ready="1">` once every page mount has run.
- Every package's browser code starts at `site/<package>/index.js` (already listed in
  `app.js`), imports `/static/app.js` by exactly that URL, and registers what it adds.
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
  openNote(el, { slug, readOnly = false, onOpenNote(slug), onState({ saved, participants, seq }) })
    -> Promise<{ getMarkdown(), selection() /* {path, text, start, end} | null */,
                 applyBlockEdit(path, text) /* false if the block changed */, replaceMarkdown(md),
                 toggleTask(path, checked), focusTitle(), detach() }>
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
- Build before browser tests when `tools/` changed: `npm run build`.
- Test-only pages: a package may add routes under `/_test/...` in its own blueprint, registered
  only when the environment has `MARKMIK_TEST_PAGES=1` (set by `playwright.config.mjs`, never in
  production), marked `@auth.public` only if the test needs no login. Their templates/scripts
  live in the package's own files.
