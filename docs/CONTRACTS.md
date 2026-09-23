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
occurrences(conn, start: date, end: date, *, statuses=("kinnitatud","pakutud")) -> list[dict]
```
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

### vault (Wave 0, done) — `app/vault.py`
`get/set/delete/get_json/set_json/masked/names`. Secret names:
`claude_token` (falls back to env `CLAUDE_CODE_OAUTH_TOKEN`), `ekool_username`,
`ekool_password`, `ekool_tokens` (json), `google_client` (json client secret),
`google_account:<email>` (json token), `brain_token`, `ntfy` (json `{url, topic, token}`).

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
`ClaudeAgentOptions.env` and blank `SECRET_KEY`, `MARKMIK_KEY`, `OWNER_PASSWORD` there (the CLI
inherits the app's environment); extraction runs with `thinking={"type":"disabled"}` (4 s instead
of 35 s); the parsed object is `ResultMessage.structured_output`, and `date` formats are not
enforced, so code validates every date; chat history is replayed from our DB each turn (no CLI
session state); the system prompt lists the tools that exist (a tool-less model invents results).

Stream events: `{"type":"delta","text"}`, `{"type":"tool","name","input","result"}`,
`{"type":"done","session_id"}`, `{"type":"error","message"}`. In tests `AI_FAKE=1` is always set
(tests/conftest.py); set `runner.fake_structured` to shape answers.

### gbrain (H)
`search(query, limit=8) -> list[{"slug","title","snippet","score"}]`, `related(conn, note_row)`
(same shape), both return `[]` and log when gbrain is unreachable.

## 5. HTTP endpoints

Auth column: O = owner, G-v / G-e = guest with a view / edit share for that note, P = public.

| WP | Method, path | Auth | In → out |
|---|---|---|---|
| core | `GET/POST /login`, `POST /logout` | P / O | form `parool` |
| core | `GET /muya/<ver>/<path>` | P | editor build, immutable when `<ver>` is current |
| C | `GET /`, `GET /m/<slug>`, `GET /tana` | O | pages (`data-page` = `notes`, `notes`, `today`) |
| C | `POST /api/capture` | O | `{text}` → `{"kind":"event"\|"task"\|"note", ...}` |
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
| A | `POST /pildid` | O, G-e | multipart `file[]` → `{code, msg, data:{errFiles, succMap}}` (Muya's shape) |
| A | `GET /pildid/<name>` | O, G-v (only images its note references) | the file |
| A | `GET /api/images` | O | `[{name, url, original_name}]` for the gallery |
| D | `GET /kalender` | O | page `data-page="calendar"` |
| D | `GET /api/events?start=&end=` | O | occurrences (§4 events) |
| D | `POST /api/events`, `PATCH /api/events/<id>`, `DELETE /api/events/<id>` | O | event JSON; PATCH sets `user_edited`; DELETE = remove |
| D | `POST /api/events/<id>/done` | O | `{occurrence, done}` → 204 |
| D | `GET /api/today` | O | `{date, events:[...], tasks:[...], tests_soon:[...], daily:{slug,title}, postkast}` |
| D | `GET /ics/<token>.ics` | P (token) | iCalendar feed |
| E2 | `GET /postkast`, `GET /api/inbox` | O | proposed events |
| E2 | `POST /api/inbox/<id>/confirm`, `.../reject`, `POST /api/inbox/confirm-all` | O | → 204 |
| E2 | `GET /seaded`, `GET /seaded/uhendused`, `GET /tere` | O | pages (`settings`, `connections`, `wizard`) |
| E2 | `GET /api/connections` | O | `[{key, title, ok, summary, error, at, masked:{name: "••••1234"}}]` |
| E2 | `POST /api/connections/<key>` | O | fields for that card → saved in vault |
| E2 | `POST /api/connections/<key>/test`, `DELETE /api/connections/<key>` | O | result / 204 |
| F | `POST /api/ai/chat` | O | `{slug?, message, selection?}` → SSE stream (§4 events) |
| F | `POST /api/ai/inline` | O | `{slug, instruction, text}` → SSE stream |
| F | `GET /api/ai/threads/<slug>` | O | messages |
| G | `GET /j/<token>` | P | view or edit page for the shared note |
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
  `postkast:count {count}`, `theme:changed {theme}`.
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
