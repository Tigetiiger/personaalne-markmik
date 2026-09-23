# Märkmik — implementation and testing plan (parallel subagents)

Companion: [PLAN-OVERVIEW.md](PLAN-OVERVIEW.md) (what and why). Section numbers such as §6 refer to the overview.

## Rules for the parallel work

- **Waves.** Wave 0 and 0.5 are the lead's (main thread). Waves 1–3 run up to 5 subagents at once,
  each with `isolation: "worktree"` on its own branch `wp/<id>` from `arendus`. The lead merges in
  the listed order and runs the whole suite after every merge before the next wave starts.
- **Ownership.** Each work package (WP) owns disjoint files. Frozen in Wave 0 and edited only by the
  lead: `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `schema.sql`,
  `docs/CONTRACTS.md`, `app/__init__.py`, `templates/base.html`. Each WP has its own blueprint file,
  its own CSS file (already linked from `base.html`), its own `tests/<wp>/` directory with its own
  `conftest.py`.
- **Contracts.** `docs/CONTRACTS.md` defines schema, Python function signatures, HTTP endpoints
  (method, path, auth, JSON in/out), JS exports, SSE event shapes, slot APIs. A WP needing another
  WP's code uses the contract and a stub, never the other branch. A needed contract change → report
  to the lead; the lead changes it on `arendus` and the WP rebases.
- **Each subagent prompt says**: read `~/.claude/CLAUDE.md`, tier `workstation`; owned files;
  contract sections; commit only on your branch, never push; Node PATH prefix
  `export PATH="$HOME/.local/share/fnm/aliases/default/bin:$PATH"`; Playwright uses
  `/usr/bin/chromium --no-sandbox` with fonts/CDN blocked via `--host-resolver-rules` (not
  `page.route`); your own `VT_PORT`; done = your tests pass and you saw the output.
- **Commands**: `uv run pytest -q`; `npm run build:editor && npm run build:vendor`;
  `VT_PORT=87xx npx playwright test --project=chromium` (Firefox + mobile in the QA wave).

## Wave 0 — lead: foundation

| # | Step | Verify |
|---|---|---|
| 0.1 | Project dir, git on `arendus`, plan docs | files render; commit |
| 0.2 | **All** dependencies + lockfiles: Python (flask, gunicorn, markdown, pymdown-extensions, nh3, pyyaml, python-dateutil, icalendar, recurring-ical-events, dateparser, httpx, google-api-python-client, google-auth-oauthlib, claude-agent-sdk, cryptography — Fernet for secrets at rest, nothing else in the list does it; dev pytest), JS (`@muyajs/core@0.2.0` exact, esbuild, @playwright/test, FullCalendar v6 core/daygrid/timegrid/list/interaction, force-graph, dompurify, mermaid 11.15, katex 0.16.47) | `uv sync`, `npm ci` |
| 0.3 | App factory, `config.py`, `db.py` (WAL, busy_timeout, FKs), **complete `schema.sql`** (below) | `tests/core/test_db.py`: applies twice |
| 0.4 | `auth.py`: owner login (bootstrap `OWNER_PASSWORD`), throttle, CSRF (port Steam 58-80); **default-deny `before_request`** with guest allow-list; working `share_or_owner(mode)` over `shares` | pytest login, CSRF, deny-by-default |
| 0.5 | `security.py`: CSP (`script-src 'self'`, `style-src 'self' 'unsafe-inline'` for Muya inline styles), headers, rate limiter; theme boot moved to an external blocking script | pytest headers |
| 0.6 | `base.html` 3-pane shell; `site/style.css` tokens/top bar/dark mode/grid; self-hosted fonts; `site/app.js` registries: `registerRoute`, `registerPanel(id,{title,mount})`, `registerMetaAction(id,{mount})` (metadata row), `registerSettingsSection` | shell loads, theme toggles |
| 0.7 | Stub blueprint + CSS file + `tests/<wp>/` per WP, all registered | `flask routes` |
| 0.8 | `app/jobs.py` registry (`@job(name, schedule)`) + `app/worker.py` runner (lock, jitter, graceful stop) | scheduler test with fake clock |
| 0.8b | `app/secrets.py`: `get(name)`, `set(name, value)`, `delete(name)`, `masked(name)`; Fernet with `MARKMIK_KEY`; `CLAUDE_CODE_OAUTH_TOKEN` falls back to the environment. Contract: every connection exposes `test_connection() -> {ok, summary, error}` | pytest: round trip, wrong key fails, value never in logs |
| 0.9 | `app/ai/runner.py` interface: `run_structured(prompt, schema, model)`, `stream_chat(...)`; `AI_FAKE=1` canned mode only | pytest fake mode |
| 0.10 | `tools/build-editor.mjs` (copied, patches changed as §3), `tools/build-vendor.mjs` | `site/muya/VERSION`, `site/vendor/*` built |
| 0.11 | `playwright.config.mjs` (VT_PORT, temp DB, temp bare brain repo, `timezoneId: 'Europe/Tallinn'`), shared fixtures (CsrfClient, owner, `brain_remote`, `fake_ai`) | `smoke.spec.mjs` green |
| 0.12 | `docs/CONTRACTS.md`; Dockerfile (node build → python + `claude` CLI); compose web/worker/gbrain on `100.122.97.7:8082`; `.env.example` with only `SECRET_KEY`, `OWNER_PASSWORD`, `MARKMIK_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (the real `.env` already holds the token) | `docker compose config` |
| 0.13 | Commit | `git log` |

### Schema (frozen in 0.3)

- `owner(id=1, password_hash)` · `settings(key PK, value)`
- `notes(id, slug UNIQUE, title, body, folder, tags_json, aliases_json, frontmatter_extra, pinned, is_daily, daily_date, created_at, updated_at, deleted_at, rev, exported_rev, file_path)`
- `notes_fts` (FTS5, external content, `unicode61 remove_diacritics 2`) + sync triggers
- `links(src_id, dst_slug, dst_id, anchor)`, index on `dst_slug`
- `tasks(id, note_id, line_key, text, done, due_date, due_time)`
- `collab_sessions(id, note_id, base, note_rev, saved_seq, closed, last_active_at, created_at)`
- `collab_ops(session_id, seq, op, client, op_id, author, created_at, PK(session_id, seq))`, index `(session_id, client, op_id)`
- `images(name PK, original_name, author, created_at)`
- `events(id, uid UNIQUE, title, start_at, end_at, start_date, end_date, all_day, tzid, kind, status, source, source_ref, source_quote, confidence, location, description, note_id, rrule, exdates_json, series_id, recurrence_id, user_edited, created_at, updated_at)`
- `event_done(event_id, occurrence)` · `reminders_sent(item_kind, item_id, occurrence, rule)`
- `sources(id, kind, name, config_json, cursor, last_run_at, last_ok_at, last_error)`
- `source_items(id, source_id, external_id, fetched_at, raw_json, status, UNIQUE(source_id, external_id))`
- `rejected_uids(uid PK)` · `ai_usage(day PK, calls)`
- `shares(id, token_hash UNIQUE, note_id, mode, created_at, expires_at, revoked_at, last_used_at)`
- `ai_threads(id, note_id, created_at)` · `ai_messages(id, thread_id, role, content_json, created_at)`
- `secrets(name PK, ciphertext, updated_at)` · `onboarding(step PK, status)` (done / skipped)

## Wave 0.5 — lead with the user present: spikes

Each writes `docs/spikes/<name>.md` stating what was verified (with output) and what was not.

| Spike | Work | Decides |
|---|---|---|
| S1 gbrain | install (bun), `init --pglite --no-embedding`, import a copy of brain + sample notes; which wikilink form makes edges (`[[slug]]` vs `[[notes/x/slug]]`); `serve --http` + auth; `search --json`; two clients at once | link format in files; compose service; client code |
| S2 Agent SDK | token is already in `.env` (copied from stock-oracle); build the CLI into the image the way `~/Projects/stock-oracle/Dockerfile` lines 14-24 do (pinned version via install.sh, own empty `CLAUDE_CONFIG_DIR`); in the image: `tools=[]` exposes no built-ins, `output_format` works, in-process tool call, partial streaming, cold-start latency | real `runner.py` mode |
| S3 eKool | with creds temporarily in `.env` (the app has no Ühendused page yet): password grant, `/person`, `/todolist`, `/feed`; search for timetable/grades; else Playwright on ekool.eu. **Anonymised** fixtures → `tests/fixtures/ekool/` | eKool adapter endpoints; whether lessons exist |
| S4 Gmail | user's OAuth client JSON in `data/` for now; paste-redirect consent for both accounts; `history.list` from a stored historyId; school account allowed or not. Anonymised fixtures → `tests/fixtures/gmail/` | Gmail adapter; forward fallback |

Then the lead updates `CONTRACTS.md` and freezes it.

## Wave 1 — 5 parallel agents

### WP-A Editor + collab — `wp/editor` (VT_PORT 8791)
Owns `tools/{editor,note-editor,collab,wikilink}.js`, `site/editor.css`, `app/{collab,images,svg,render}.py`, `app/views/{collab,images}.py`, `tests/editor/`.
1. Port `editor.js` minus `mountHtmlEdit`, `mountHidden`, `describeActivity`. Export `openNote(el, {slug | shareToken, readOnly})` → `{getMarkdown, applyBlockEdit, replaceMarkdown, toggleTask, detach}`; one Muya, lazy `core.css`. → spec: mounts, typing changes markdown, switching notes keeps no stale listeners.
2. Sanitised HTML preview (Muya default restored). → spec: `<img src=x onerror=…>` in an HTML block does not run.
3. Link following: Ctrl/Cmd+click on `span.mu-link` with `/m/` → `onOpenNote(slug)`; extend `jumpClick` for `/m/`. → spec.
4. `[[` picker: `input` listener + anchor block text; handles auto-paired `[[]]`; `GET /api/notes/suggest` (stub); replaces `[[query]]` with `[T](/m/slug)`; "Loo uus". → spec.
5. Round trip: `[T](/m/slug)`, `- [ ] x @reede`, `/pildid/…` survive `getMarkdown` exactly; typing `- [ ] @reede` + Enter does not open the block menu. → spec.
6. `collab.py` from `koostoo.py`: `note_rev` validity, `BEGIN IMMEDIATE` save with `seq > saved_seq`, `is_busy(note)`, throttled `last_active_at`, op size cap, view-only guests rejected, calls `notes.on_saved` (contract). → port all of `test_koostoo.py`; new: out-of-order saves keep newest, equal seq is a no-op, concurrent saves from threads, save after rev bump → reset.
7. `collab.js` from `koostoo.js`: `detach()`, jittered autosave, `visibilitychange` leave, try/catch around remote apply → report. → port `koostoo.spec.mjs` (two contexts converge, cursors, reload shows saved text).
8. **OT fuzz harness**: one page, 3 Muya instances + a JS copy of the sequencer, seeded random delays; Enter/Backspace at block edges, list/task toggles, table cells, multi-block paste, undo; assert identical `getMarkdown`, late joiner converges, saved body = converged doc. → runs in CI with fixed seeds.
9. `/pildid` upload/serve + `svg.py`; guests only images referenced by their note; no list for guests. `render.py` trimmed + nh3. → port upload/SVG tests and `test_render.py`; XSS cases.

### WP-B Notes core — `wp/notes` (VT_PORT 8792)
Owns `app/{notes,links,tasks,search,etdates}.py`, `app/views/notes_api.py`, `tests/notes/`.
1. `notes.py`: create (Steam `slugify`), get, tree, move, pin, soft delete, daily get-or-create, `on_saved` (title from first `# `; rebuild links/tasks). Server writes check `collab.is_busy` (contract) and bump `rev`. → pytest.
2. `etdates.py`: Estonian relative/absolute dates and times against a reference date. → table tests incl. year rollover, "järgmisel reedel", "3. oktoobril", "kell 14.15", weekday/date mismatch detection.
3. `tasks.py`: parse `- [ ]`/`- [x]` with `@date [time]` after text (daily notes use their own date as reference); `toggle(task)` server write. → tests.
4. `links.py`: parse `/m/` links; outgoing; backlinks; unlinked mentions (FTS phrase, exclude already linked, skip code); `link_mention()`. → tests.
5. `search.py`: FTS5 prefix + diacritic folding, snippets. → "too" finds "töö".
6. API: `GET /api/notes/tree|suggest|<slug>|<slug>/links|<slug>/mentions`, `POST /api/notes`, `PATCH /api/notes/<slug>` (folder, tags, pinned), `DELETE`, `POST /api/notes/<slug>/link-mention`, `POST /api/tasks/<id>/toggle` (409 when busy), `GET /api/search`. → API tests.

### WP-D Calendar — `wp/calendar` (VT_PORT 8793)
Owns `app/{events,ics,reminders}.py`, `app/views/calendar.py`, `site/calendar.js`, `templates/calendar.html`, `tests/calendar/`.
1. `events.py`: CRUD, `upsert_by_uid` (skips `user_edited`), range query expanding RRULE in local time with `exdates` and overridden occurrences, tasks as all-day items, per-occurrence done. → tests: 08:00 lesson stays 08:00 on 26.10.2026, all-day, exceptions.
2. `ics.py`: export `/ics/<token>.ics` with `TZID` + `VTIMEZONE`; import helper for URLs. → round-trip test.
3. `reminders.py`: rules → due reminders → ntfy POST (URL/topic/token from `secrets`, `test_connection()` sends a test push); idempotent via `reminders_sent`; registered as a job. → tests with fake clock/HTTP; reminder in the repeated 03:00 hour fires once.
4. UI: FullCalendar month/week/day/list, drag/resize → `PATCH /api/events/<id>`, click → Estonian modal; `pakutud` dashed, lessons muted, tests in the accent colour, "AI" badge with Eemalda. → Playwright: create, move, resize, switch view, dark mode.
5. `GET /api/today`. → pytest.

### WP-E1 Sources + extraction backend — `wp/sources` (VT_PORT 8794)
Owns `app/sources/{base,ekool,gmail,icsfeed}.py`, `app/extract.py`, `tests/sources/` + fixtures.
1. `base.py`: `Adapter.fetch(cursor) -> (items, new_cursor)` and `test_connection()`; credentials only via `app/secrets.py`; runner stores `source_items` idempotently; health fields; jobs registered via `app/jobs.py`. → fake-adapter tests.
2. `ekool.py` (from S3): token refresh; todolist → homework/tests, uid from eKool id, auto-confirmed; subject notes via `notes.create` (contract); lessons only if S3 found a source. → fixture tests.
3. `gmail.py` (from S4): OAuth paste flow helpers, two accounts, history sync, 404 → full resync, prefilter. → fixture tests.
4. `icsfeed.py`. → fixture test.
5. `extract.py`: relevance + extraction via `ai.runner` (fake in tests), schema (kind, title, start, end, all_day, date_text, source_quote, confidence, updates_uid), `etdates` checks, auto-add vs Postkast rule (§8), dedupe/merge, rejected uids, daily cap. → unit tests with fake AI; **eval** `pytest -m live`: ~20 anonymised Estonian emails incl. newsletters and injection attempts, each with its Date header; score precision/recall against a threshold; code (not the model) resolves `date_text`.

### WP-H Git sync + gbrain — `wp/sync` (VT_PORT 8795)
Owns `app/{linkfmt,gitsync,gbrain,backup}.py`, `app/views/related.py`, `site/panels/related.js`, compose `gbrain` service files, `tests/sync/`.
1. `linkfmt.py` (format from S1): frontmatter split/join incl. unknown keys; `/m/slug` ↔ wikilink; images; ignores code spans/fences. → table round-trip tests incl. `|`, `]`, fences, nested folders.
2. `gitsync.py`: clone/pull `data/brain` (token never logged); export (changed, idle ≥ 2 min, not busy) incl. images to `notes/_pildid/`, deletions, batch commit, `pull --rebase`, push with retry; import with deferral and "(konflikt)" copies; jobs every 5 min. → tests against `brain_remote` bare repo with a simulated laptop clone.
3. `gbrain.py`: HTTP MCP client (from S1), `related(note)`, `sync()` after push; failures degrade ("gbrain pole kättesaadav"). → fake-server tests + one `live` test.
4. `backup.py`: nightly `.backup`, keep 14. → test.
5. `test_connection()` for the brain repo (token from `secrets`, `git ls-remote`, first clone). → test with `brain_remote`.

**Merge order**: B → A → D → H → E1.

## Wave 2 — 4 parallel agents

### WP-C App shell — `wp/shell` (VT_PORT 8796) — merged first
Owns `site/{sidebar,palette}.js`, `site/panels/{outline,links}.js`, `templates/{note,today}.html`, `app/views/pages.py`, `site/manifest.webmanifest`, `tests/shell/`.
1. Sidebar (Täna, pinned, recent, folder tree with drag, tags, "+ Uus"/Ctrl+N). → Playwright.
2. Note page via `openNote`: switch without reload, metadata row with slots, tag chips, save state, word count. → spec: type → switch → back → text there; reload → saved.
3. Palette Ctrl+K: notes, commands, quick capture → `POST /api/capture` (uses `etdates`). → spec.
4. Viited (outgoing, backlinks, mentions + Seo) and Sisukord panels. → spec.
5. Täna page + daily notes. → spec.
6. Phone layout (drawer, bottom sheet); manifest. → mobile-project spec.

### WP-E2 Postkast + Ühendused + first-run wizard — `wp/inbox` (VT_PORT 8797)
Owns `app/views/{inbox,settings,connections}.py`, `templates/{inbox,settings,connections,wizard}.html`, `site/{inbox,connections}.js`, `tests/inbox/`.
1. Postkast: proposed events with source quote; Kinnita / Muuda / Loobu; bulk confirm; count badge. → Playwright.
2. Ühendused page: one card per connection (overview §15) with status, last error, Estonian step-by-step instructions, fields, Testi (calls the owning WP's `test_connection()`), Katkesta. → Playwright with every `test_connection` faked ok/error.
3. Google card: upload OAuth client JSON; "Lisa Google'i konto" builds the consent URL; paste-the-redirect-URL box exchanges the code; repeatable for the second account; account list with remove. → pytest against a fake Google token endpoint; Playwright for the paste flow.
4. First-run wizard: after first login, cards in order Claude → eKool → Google → brain → ntfy → ICS → phone calendar; skip/continue; state in `onboarding`; Täna banner "N ühendust tegemata". → Playwright: fresh DB walks the wizard with fakes; skipped steps show on Täna.
5. Other settings sections: reminder rules, private ICS address + QR code, share-link list. → Playwright.
6. **Secrets never reach the browser**: pytest renders every settings/connections response after storing known secret values and asserts none appears (only the masked form). → pytest.

### WP-F AI assistant — `wp/ai` (VT_PORT 8798)
Owns `app/ai/{runner,tools,chat}.py` (real mode on the Wave 0 interface), `app/views/ai.py`, `site/panels/assistant.js`, `tools/inline-ai.js`, `tests/ai/`.
1. Real runner mode from S2; token from `secrets` passed to the CLI through the SDK's `env`; `test_connection()` = tiny haiku call; options in one place. → pytest: no built-in tools except WebSearch; fake mode unchanged.
2. Tools (§9) as in-process MCP; writes to busy notes return an error to the model. → per-tool tests.
3. `POST /api/ai/chat` SSE: `delta`, `tool`, `event_created`, `edit`, `done`, `error`. → pytest with fake runner.
4. Abiline panel: thread per note, context chips, event cards with Eemalda. → Playwright with `AI_FAKE`.
5. Inline: ✦ slot + Ctrl+J, `@` menu "AI", streamed preview, Asenda / Lisa alla / Loobu via `applyBlockEdit`/`replaceMarkdown`; refuses if the block changed meanwhile. → Playwright: accept, reject, collaborator sees it, Ctrl+Z undoes.

### WP-G Sharing — `wp/share` (VT_PORT 8799)
Owns `app/shares.py`, `app/views/share.py`, `templates/share_{view,edit}.html`, `site/share-dialog.js`, `tests/share/`.
1. `shares.py`: create, revoke, expiry, lookup by hash. → pytest.
2. View page (sanitised, refresh on `rev`) and edit page (`openNote({shareToken})`, name prompt). → Playwright: owner + guest in separate contexts edit together.
3. **Permission matrix generated from `app.url_map`** × {anon, guest-view, guest-edit, revoked, expired, owner}; plus guest using another note's session id, share revoked mid-session, view guest posting an op. → pytest.
4. Guest XSS: script in an HTML block written by a guest does not run in the owner's editor. → Playwright.
5. Jaga dialog in the metadata-row slot. → spec.

**Merge order**: C → F → G → E2.

## Wave 3 — 2 parallel agents

- **WP-I Graph** (`wp/graph`, VT_PORT 8791): `app/graph.py` (nodes/edges from `links`, filters,
  orphans), `/graaf` page with force-graph. Tests: API; click node opens note; 1 000-note fixture
  renders in < 2 s.
- **WP-Q QA** (`wp/qa`, VT_PORT 8792): full Playwright in chromium + firefox + mobile (screenshot
  baselines chromium only); perf fixture (1 000 notes, 5 000 events); `docker compose up` on the VM
  with a smoke run; DST checks (worker 07:00 job across 25.10.2026); `/security-review` over the
  branch; findings list for the lead.

**Merge order**: I → Q fixes.

## Wave 4 — lead

1. Fix QA findings; full suite green (pytest + Playwright, 3 projects) with output seen.
2. `docker compose up -d` on the VM; connect real sources; first eKool + Gmail sync; review
   Postkast/auto-add quality with the user and tune the threshold.
3. README (run, backup/restore, adding the tunnel later) and `docs/CONNECTING.md` — the Ühendused instructions in Estonian, with screenshots of each card.
4. Update brain page `markmik.md`, project memory; ask the user to push.

## Acceptance (end to end)

1. `http://100.122.97.7:8082` → log in → "+ Uus" → caret in title → type → "Salvestatud ✓" → reload → text there.
2. `[[Mat` → "Matemaatika" → link; Ctrl+click opens it; its Viited shows the backlink; `/graaf` shows the edge.
3. Share as "muuda" → open in a private window on another tailnet device → both type at once → texts converge, cursors labelled.
4. A school email with a clear date → within ~5 min an "AI" event is on the calendar (or in Postkast if unsure) with its quote.
5. eKool "Värskenda" → homework and tests appear; subject notes exist and link back.
6. Select a paragraph → Ctrl+J "lühemaks" → Asenda → collaborator sees it → Ctrl+Z undoes.
7. Within ~10 min the note is in `Tigetiiger/brain` under `notes/` with wikilinks; `gbrain search` finds it; Seotud shows related brain pages.
8. Evening before a test → ntfy push on the phone.
9. Fresh install with only the four `.env` keys → first login → wizard connects Claude, eKool, both Google accounts, brain repo and ntfy without editing any file; every card says Ühendatud.
