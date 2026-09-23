# Märkmik — overview plan

Companion: [PLAN-IMPLEMENTATION.md](PLAN-IMPLEMENTATION.md) (step-by-step build and test plan for parallel subagents).

## Context

The user wants a new self-hosted personal app (working name **Märkmik**, dir `~/Projects/markmik`)
that is both a calendar and a notes app:

- Notes reuse the Steam project's Markdown editor (Muya) and its visual style; notes open straight
  into edit; no publish/draft/version history; structured (folders, tags); autolinking between
  notes and a graph of links; notes synced to git; connected to gbrain.
- AI parses Gmail and eKool and updates the calendar.
- Inline AI agent in the app.
- Share a note by link; a not-signed-in person can view or edit — Google-Docs-like, but Markdown.
- Suggest productivity features.

### Decisions (user answers, 2026-09-24)

| Topic | Decision |
|---|---|
| Shared-link editing | Live co-editing with cursors |
| AI backend | Claude Code on the VM (`claude-agent-sdk` → `claude` CLI, user's subscription) |
| Notes git target | Folder `notes/` in the brain repo (`Tigetiiger/brain`) |
| UI language | Estonian UI; code identifiers and comments in English |
| eKool login | Username + password |
| Gmail | Personal and school Google accounts |
| Exposure | Tailscale only for now; public tunnel later |
| AI calendar updates | Auto-add when confident and validated; unsure items go to Postkast |
| App code repo | Private `Tigetiiger/personaalne-markmik` (GitHub allows no `ä` or spaces in names), work on branch `arendus` |
| Connecting accounts | Done inside the app (Seaded → Ühendused + first-run wizard), not by editing files (§15) |

### Research findings that shape the design

1. **Steam already has live co-editing** (`app/koostoo.py`, `tools/koostoo.js`, tests
   `test_koostoo.py`, `visual/koostoo.spec.mjs`): Muya emits `ot-json1` ops (`json-change`) and
   applies remote ones (`editor.updateContents`); the server only sequences ops; clients transform
   (inflight/buffer OT client); HTTP polling 250 ms / 1 s; remote cursors. Reuse → stay on
   **Flask + SQLite**.
2. **gbrain is not installed** on the VM, only documented. It is `garrytan/gbrain`
   (`bun install -g github:garrytan/gbrain`): markdown repo = source of truth, PGLite index,
   keyless keyword search, `gbrain import/sync/search --json/serve [--http]`. **Edges come from
   `[[wikilinks]]` only.** PGLite is single-process → one `gbrain serve --http` daemon, shared.
3. **eKool**: official API is organisation-only. For a password user: the mobile app's unofficial
   JSON API — OAuth password grant `client_id=mKool` at `ekool.eu/auth/oauth/token`, data at
   `postikana.ekool.eu/rest/json` (`/person`, `/todolist/{id}/{from}/{to}` homework+tests,
   `/feed/{id}`). Source is 2020 code → **spike must verify**. No known timetable endpoint.
   School-events ICS feed exists. Poll ≤ 4×/day (ToS: no overload).
4. **Gmail**: Gmail API, "Desktop app" OAuth client in the user's Google Cloud project,
   **published to Production** (Testing = 7-day refresh tokens), `gmail.readonly`, poll
   `history.list` every 5 min, full resync on 404. School Workspace may block unapproved apps for
   under-18s → fallback: auto-forward school mail to personal Gmail.
5. **Calendar out**: Google refreshes ICS subscriptions every 12–24 h → phone uses the PWA plus a
   private ICS feed (iOS / ICSx⁵ refresh faster). Google Calendar push is v2.
6. **Extraction**: Agent SDK `output_format` json_schema; extractor has **no tools** (email is
   untrusted); dates resolved and checked in code; stable IDs.
7. **Agent SDK** (`claude-agent-sdk`, needs the `claude` CLI): `tools`/`allowed_tools`,
   `setting_sources=[]`, `permission_mode="dontAsk"`, in-process tools (`@tool`,
   `create_sdk_mcp_server`), HTTP MCP servers, `include_partial_messages`, `model` aliases. Server
   auth: `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; `ANTHROPIC_API_KEY` later is config-only.
8. **Muya facts** (verified in `@muyajs/core` 0.2.0 source): links are never followed on click
   (only via the hover LinkTools "jump" icon → `jumpClick`); `autoPairBracket` turns `[[` into
   `[[]]`; `- [ ]` round-trips natively; the `@` block menu opens on a paragraph matching
   `^@\S*$`; `setContent` clears undo and emits no `json-change`; `replaceMarkdown` in
   `koostoo.js` diffs **top-level blocks only** (a whole list/table is one block).
9. Infra: ntfy on `100.122.97.7:8090`; free port **8082** on the Tailscale IP; Docker 29, uv,
   Node 24 via fnm (not on PATH in non-interactive shells), bun 1.4. Headless Chromium works via
   Playwright + `/usr/bin/chromium --no-sandbox` + `--host-resolver-rules` blocking Google
   Fonts/jsdelivr.

---

## 1. The product

A single-user web app on the Proxmox VM. **Left**: note tree. **Centre**: the note, always in edit
mode (Muya, Typora-like WYSIWYG Markdown). **Right**: context panel (links, outline, related from
gbrain, AI assistant, events). A calendar (month/week/day/agenda) and a **Täna** (Today) page share
the shell. A background worker pulls eKool, both Gmail accounts and ICS feeds; Claude extracts
events; confident, validated ones go straight onto the calendar marked "AI", unsure ones wait in
**Postkast**. Notes are Markdown files in `brain/notes/`, committed and pushed automatically and
indexed by gbrain. Any note can be shared by link as view or live edit.

## 2. Architecture

```
Browser                               VM: docker compose, bound to 100.122.97.7:8082
 ├ shell: sidebar / editor / panel    ┌─ web: Flask, gunicorn -w 1 --threads 16
 ├ Muya + collab.js (OT)         ◀──▶ │    notes · collab op log · calendar · shares · AI (SSE)
 ├ FullCalendar views                 │    SQLite WAL data/markmik.db · images data/pildid
 ├ force-graph                        ├─ worker: python -m app.worker (job registry)
 └ AI chat + inline AI (SSE)          │    eKool · Gmail ×2 · ICS · extraction · git sync
                                      │    · reminders (ntfy) · gbrain sync · nightly backup
                                      └─ gbrain: `gbrain serve --http`, PGLite volume,
                                           indexes the app's clone data/brain
`claude` CLI in web + worker images; auth via CLAUDE_CODE_OAUTH_TOKEN
```

- One gunicorn worker: collab presence is in memory, as in Steam. Threads serve polling and SSE.
- Web and worker share SQLite (WAL, `busy_timeout`). Whether a note is being edited is a DB fact
  (see §6), so the worker can see it.
- **Default deny**: every route needs the owner session except `/login`, static assets and the
  guest allow-list under `/j/<token>`.

## 3. Reuse from Steam (`~/Projects/steam-seljakotirobot`, branch `veebileht`)

| Take | From | Change |
|---|---|---|
| Muya build | `tools/build-editor.mjs` | keep patches incl. json1 export + own-only undo; **remove** the two unsanitised-HTML patches (lines ~132-145) and the "hidden part" menu patch (73-81); per-file transform, never bundle |
| Editor | `tools/editor.js` | keep toolbar, find, gallery, caret, `@` menu keys; **drop** `mountHtmlEdit` (it writes preview `innerHTML` back into the source — incompatible with sanitising) and `mountHidden`, `describeActivity`; wrap as a module |
| Editor CSS | `site/editor.css` | keep remote-cursor styles |
| Collab | `app/koostoo.py`, `tools/koostoo.js` | per-note sessions, new validity + save rules (§6), `detach()`, English names |
| Images | `/pildid` routes (`app/views.py` ~676-736), `app/svg.py` | guests only see images their note references; no image list for guests |
| Render | `app/render.py` | drop page split, drawings, statuses; sanitise with nh3 |
| Style | `site/style.css` tokens, top bar, dark mode (`data-teema`) | trim Steam page components; add 3-pane layout; self-host fonts |
| Login/CSRF | `app/views.py` 58-80, `app/kasutajad.py` | single owner account |
| Tests | `conftest.py`, `test_render.py`, `test_koostoo.py`, `playwright.config.mjs`, `koostoo.spec.mjs` | port |

Not taken: versions/history, publish/draft, documentation pages, weighings, account admin, HTML
block live editing, hidden parts.

## 4. Note layout (synthesis of Obsidian, Bear, Notion, Logseq, Apple Notes patterns)

- **Three panes**: left 16 rem (Steam's section column), centre capped at `--measure` 70ch,
  right ~20 rem; both sides collapsible (Ctrl+\\, Ctrl+Shift+\\). Phone: one column, left is a
  drawer, right is a bottom sheet.
- **Always edit**: one Muya instance mounted at shell load (preloaded on idle, as in Steam).
  Switching notes = detach collab, `setContent`, join the new note's session. No reload. Title is
  the first `# ` line; a new note puts the caret there.
- **Left**: "+ Uus" and search on top, then Täna · Kinnitatud (pinned) · Hiljutised · Kaustad
  (folder tree, drag to move) · Sildid.
- **Metadata row** (not inside the text): folder breadcrumb, tag chips, Jaga, ✦ AI, participants,
  save state ("Salvestatud ✓"), word count. YAML frontmatter is stripped before Muya and rebuilt on
  export, so it never shows as a code block.
- **Right tabs**: Viited (outgoing links, backlinks, unlinked mentions) · Sisukord · Seotud
  (gbrain) · Abiline (AI) · Sündmused (events linked to the note or the daily note's day).
- **Command palette** (Ctrl+K): jump to note, commands, quick capture. (The one high-confidence
  result in `brain/pages/design-preferences.md`.)
- **Look**: Seljakotirobot tokens — paper `#f3f5f4`, ink `#14202e`, one hi-vis accent `#f2c230`,
  Schibsted Grotesk UI + Source Serif 4 body, 2 px ink borders, square corners, 3 px yellow focus
  rings; dark mode kept.

## 5. Links, autolinking, graph

- **In the DB/editor**: ordinary links `[Pealkiri](/m/<slug>)` — Muya renders and edits them.
  **In git files**: `[[notes/<path>/<slug>|Pealkiri]]` wikilinks so gbrain makes edges (exact form
  decided by the gbrain spike). A round-trip-tested converter maps between them on export/import;
  images `/pildid/x.png` ↔ `/notes/_pildid/x.png`.
- Slugs never change after creation, so moving a note between folders never breaks a link.
- **Following links**: Ctrl/Cmd+click on a `/m/` link, the hover "jump" icon (extended beyond
  http), and the outgoing-links list in Viited (phones have no hover).
- **Autolinking**:
  1. Typing `[[` (Muya auto-pairs to `[[]]`) opens a fuzzy picker; choosing replaces `[[query]]`
     with a link; "Loo uus «…»" creates the note and links it.
  2. Backlinks from a `links` table rebuilt on every save.
  3. **Unlinked mentions**: notes that contain this note's title or aliases as plain text (FTS5
     phrase search) with a "Seo" button that turns the mention into a link.
- **Graph** `/graaf`: force-directed canvas; node size = number of links; filter by folder/tag,
  show orphans; click opens the note.

## 6. Live co-editing (shared by owner and guests)

- `collab_sessions.note_rev`: set at join and at each save by that session. A session is valid
  only while `closed = 0 AND notes.rev = note_rev`. Anything else that writes the note bumps `rev`,
  so idle sessions die and clients rejoin with fresh text.
- **Save rule**: one `BEGIN IMMEDIATE` transaction; write only if `seq > saved_seq`; equal or older
  seq → 204, nothing written. Every client autosaves when `seq > savedSeq`, nothing in flight, after
  the next animation frame (Muya reports changes one frame late), with 2–3 s jitter. Whoever is
  still connected saves — guests included. Rejoining a session that has unsaved ops (Steam
  behaviour) rescues work when the last tab died.
- **Busy** = open session and (`last_active_at` < 90 s or `last_seq > saved_seq`). Server-side
  writers (git import, task tick on Täna/calendar, AI tools, "Seo") write only when the note is not
  busy; otherwise 409 "Märget muudetakse teises aknas" or, for git import, defer.
- Edits to the note open in *this* tab (inline AI) are applied by this client as ordinary ops, so
  collaborators see them and Ctrl+Z undoes them. Inside one block → set the block's text; tasks →
  toggle the item's `checked`; only multi-block rewrites use `replaceMarkdown`. Before applying,
  the captured block path is transformed through ops received since; if the block changed, refuse.
- A tab hidden > 60 s saves and leaves. Ops are size-capped; a remote op that throws is reported
  and closes the session instead of breaking every client. View-only guests cannot post ops.

## 7. Calendar

- Month, week, day, list views — FullCalendar v6 standard plugins (MIT), `timeZone: 'local'`,
  themed with the tokens. Drag to move/resize, click to create.
- Event kinds: `tund`, `kontrolltöö`, `kodutöö`, `sündmus`, `tähtaeg`, `isiklik`. Sources:
  `käsitsi`, `ekool`, `gmail`, `ics`, `ai`. Status: `kinnitatud`, `pakutud`, `eemaldatud`.
- **Tasks in notes**: `- [ ] tee ülesanded @reede` / `@3.10` / `@2026-10-03 14.15` — `@date` only
  after other text (a bare `@…` paragraph opens Muya's block menu). Parsed on save; shown on the
  calendar and Täna; ticking there writes the note (if not busy).
- eKool homework and tests are auto-confirmed; each subject gets a subject note ("Matemaatika")
  that its homework and tests link to. **Timetable lessons only if the spike finds a source.**
- Out: private ICS feed `/ics/<token>.ics` (with `VTIMEZONE`); ntfy reminders.
- Europe/Tallinn; timed events stored UTC + `tzid`; all-day events as dates. Recurrence expands in
  local clock time (DST change 2026-10-25).

## 8. Parsing pipeline

```
adapter.fetch(cursor) ─▶ source_items (raw, stable external_id, idempotent)
  ├ structured (eKool, ICS) ─▶ normalise ─▶ events (kinnitatud, source ekool/ics)
  └ email ─▶ prefilter (label / sender list) ─▶ Claude haiku: relevant?
        ─▶ Claude extract (json_schema, no tools) ─▶ etdates resolves date_text in code
        ─▶ checks: resolved date == model date, weekday matches, not past, < 1 year,
                   uid not rejected before
        ─▶ dedupe/merge by stable uid and (date, subject) across sources
        ─▶ confident + all checks pass → events (kinnitatud, source ai, "AI" badge, one-click remove)
        ─▶ otherwise → events (pakutud) → Postkast: quote + Kinnita / Muuda / Loobu
```
- Removing or rejecting an AI item remembers its uid so it never returns. Re-syncs never overwrite
  events the user edited (`user_edited`).
- Schedule: Gmail every 5 min per account; eKool 07:00/13:00/17:00/21:00 + "Värskenda"; ICS
  hourly. Settings page shows health and last error per source.
- Budget: extraction uses `haiku`; queue + daily cap so a mail burst cannot drain the subscription.
- `etdates.py` is the one Estonian date parser (weekday names, "homme", "ülehomme", "järgmisel
  reedel", "3.10", "3. oktoobril", "kell 14.15") used by tasks, quick capture and extraction checks.

## 9. Inline AI agent

- **Abiline** (right panel): chat with the current note and selection as context, streamed over
  SSE. Tools — app-defined only: search notes, read note, create note, create event (card with
  "Eemalda"), list events/tasks, gbrain search/recall (read-only). `WebSearch` allowed. **No**
  Bash/Read/Write/WebFetch (WebFetch would be an exfiltration route).
- **Inline**: select → ✦ or Ctrl+J → prompt ("lühemaks", "tabeliks", "tõlgi inglise keelde") →
  streamed preview → Asenda / Lisa alla / Loobu, applied as in §6. Muya's `@` menu gets an "AI"
  item for generating at the caret.
- Runner: `setting_sources=[]`, `tools=[]` + allow-list, `permission_mode="dontAsk"`, empty temp
  `cwd`, `max_turns`, models `sonnet` (chat) / `haiku` (extraction). `AI_FAKE=1` gives canned
  streams for tests.
- Caveat: subscription rate limits are shared with the user's own Claude Code use.

## 10. Sharing

- "Jaga": create a **vaata** (view) or **muuda** (edit) link, optional expiry, revoke, last used.
  URL `/j/<token>` (128-bit random, stored as SHA-256).
- View: sanitised render that refreshes when `rev` changes. Edit: the same editor + collab; the
  guest types a display name (cookie) that labels their cursor.
- Guest scope: that note's session, images that note references, uploads with a size limit.
  Links to unshared notes render as plain text.
- Security: DOMPurify in the editor preview, nh3 on server render, CSP `script-src 'self'` from day
  one (no inline scripts), HttpOnly + SameSite=Lax cookies, rate limits on `/j/*` and `/login`.
- Tailscale only for now: links work on the tailnet. Going public later = a `cloudflared` service
  and a hostname (user's Cloudflare action).

## 11. Git sync + gbrain

- The app keeps its **own clone** of `Tigetiiger/brain` in `data/brain` (never touches
  `~/Projects/brain`). Push with a fine-grained token limited to that repo.
- **Export**: note changed, idle ≥ 2 min, not busy → `notes/<folder>/<slug>.md` with frontmatter
  (`title`, `type: note`, `tags`, `date`, `updated`, `source: markmik`, `id`, plus any unknown keys
  kept from the laptop) → batch commit every 5 min → `pull --rebase` → push → `gbrain sync`.
  Deleting a note deletes its file.
- **Import**: pulled changes under `notes/` update the DB; busy notes wait; true conflicts become
  "(konflikt)" copies. Nothing is lost.
- **gbrain**: one `gbrain serve --http` service indexing `data/brain`. Used by the Seotud panel and
  the AI's read tools. Later the user's Claude sessions can add the same HTTP MCP server.
- Nightly SQLite `.backup` to `data/backups` (keep 14).

## 12. Productivity features

**In v1:** Täna page (today's agenda, tasks due, tests in the next 7 days, daily note, Postkast
count) · daily notes (`notes/paevik/YYYY-MM-DD.md`) · tasks with dates in notes · quick capture
("mate kontrolltöö reedel 10.15") · full-text search (diacritic-folding: "too" finds "töö") ·
ntfy reminders (tests the evening before, homework due tomorrow, events N min before) · private
ICS feed for the phone · Postkast · installable PWA (manifest).

**Menu for later (user picks):** AI study planner (test → spaced study blocks) · morning briefing
push ("mis mul täna on") · weekly review template · test countdown widget · note templates
library · local graph in Viited · "link this?" hints while typing · AI "Leia seosed" · slug rename ·
Google Calendar push · app MCP server so any Claude session can use notes/calendar · gbrain
`remember` from chat · offline editing · public tunnel · email → note · focus timer.

## 13. Risks

| Risk | Mitigation |
|---|---|
| eKool unofficial API changed or blocked | spike first; adapter interface; Playwright fallback; low poll rate |
| Guest XSS runs in the owner's editor | DOMPurify + nh3 + CSP; payload tests |
| Prompt injection via email | extractor has no tools; assistant has no exfiltration tools; dates checked in code |
| OT divergence (lists, tables, undo) | block-level edits only where needed; 3-client fuzz test |
| Muya patches pinned to 0.2.0 | build fails loudly; exact pin |
| PGLite single-process | one gbrain HTTP server |
| Brain repo conflicts with laptop edits | rebase, conflict copies, app writes only `notes/` |
| Subscription rate limits | queue, daily cap, haiku, API-key switch |
| School account blocks OAuth app | auto-forward to personal Gmail |
| Data loss | nightly backup, git history, Proxmox snapshots |

## 14. Needs from the user

Done on 2026-09-24:
- App code repo `Tigetiiger/personaalne-markmik` (private) created by Claude at the user's request.
- Claude token: `CLAUDE_CODE_OAUTH_TOKEN` copied from `~/Projects/stock-oracle/.env` into
  `~/Projects/markmik/.env` (gitignored, mode 600). Stock-oracle's nightly runs use the same
  token, so the two apps share one subscription's rate limits.

Still needed — all entered through the app's Ühendused page (§15), not files:
1. eKool username + password.
2. Google Cloud OAuth "Desktop app" client (published to Production), then both Google accounts.
3. Fine-grained GitHub token: contents read/write on `Tigetiiger/brain` only.
4. ntfy topic for reminders (the VM's ntfy server is prefilled).
5. Be present for Wave 0.5 — the spikes run before the app exists, so they read credentials from
   `.env`; afterwards the values move into Ühendused.
6. Later: Cloudflare hostname for public share links. Optional: ask help@ekool.eu for API access.

## 15. Connecting accounts in the app ("Ühendused")

Every outside account is connected from inside the finished app. `.env` only holds what the app
needs to start: `SECRET_KEY`, `OWNER_PASSWORD` (first login only), `MARKMIK_KEY` (encrypts stored
secrets) and `CLAUDE_CODE_OAUTH_TOKEN` (already there).

- **Seaded → Ühendused**: one card per connection. Each card shows its status (Ühendatud /
  Pole ühendatud / Viga, with the last error and when), short step-by-step instructions in
  Estonian with links, the input fields, **Testi** and **Katkesta** (disconnect).
- **First-run wizard**: after the first login, "Alustame" walks through the cards in order —
  Claude → eKool → Google accounts → brain repo → ntfy → ICS feeds → phone calendar. Every step
  can be skipped; Täna shows "N ühendust tegemata" until all are done or dismissed.
- **Secrets at rest**: `secrets` table, encrypted with Fernet (`MARKMIK_KEY`). The browser only
  ever gets a masked value (`••••1234`); secrets are never logged and never written to git.

| Card | What the user does | What **Testi** shows |
|---|---|---|
| Claude | nothing (token from `.env`); to replace it, run `claude setup-token` on the VM and paste | a tiny haiku call: model and response time |
| eKool | username + password | logs in; student's name and number of upcoming homework/tests |
| Google konto (repeatable, 2×) | once: upload the OAuth client JSON (guided: Cloud Console → new project → enable Gmail API → consent screen External → **Publish to Production** → Credentials → Desktop app → download). Per account: "Lisa Google'i konto" → allow → the browser lands on an `http://127.0.0.1…` page that does not load → copy that address → paste | the account's email address, messages in the chosen label/sender filter |
| Brain repo | paste the fine-grained token (guided: GitHub → Settings → Developer settings → Fine-grained tokens → only `Tigetiiger/brain` → Contents: read and write) | `git ls-remote`, clone into `data/brain`, last commit |
| ntfy | server URL (prefilled `http://100.122.97.7:8090`), topic, token if the server needs one | sends a test notification |
| ICS feeds | paste URLs (eKool school-events feed, Moodle, anything) | number of events found |
| Telefoni kalender | copy the private ICS address or scan its QR code | — |
| Avalikud jagamislingid (later) | Cloudflare Tunnel token + hostname | — |

The same instructions ship as `docs/CONNECTING.md` (Estonian) for reading outside the app.
