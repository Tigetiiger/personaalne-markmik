# Kodu: a chat window on the first page (plan, 2026-10-02)

**The user's request:** put a chat window on the first page that can do all kinds of things: make
a note out of a picture, put reminders in the calendar, and so on. Plan only for now.

## What exists

- **Abiline already is that chat**, but only inside a note: `app/ai/chat.py` runs one thread per
  note, replays its history from `ai_threads`/`ai_messages`, and gives the model eight app tools
  plus WebSearch (`app/ai/tools.py`). It creates notes (`loo_markme`), calendar events
  (`loo_sundmus`), reads notes, events, tasks and the brain, and proposes edits to the open note
  as a diff. The browser side (`site/ai/index.js`, `mountPanel`) renders the thread, streams the
  answer over SSE and shows cards for tools, created events and proposals.
- **A thread outside any note is already in the schema:** `ai_threads.note_id` is nullable and
  `chat.thread_id(conn, None)` returns "the thread outside any note". Nothing shows it yet.
- **The first page is empty.** `/` renders `note.html` with no slug: the sidebar on the left and
  `#note-empty` ("Vali vasakult märge või loo uus.") in the centre. The phone app (`start_url`)
  opens on `/tana`.
- **Pictures** are stored once by content hash under `data/pildid` (`images.store`, 10 MB cap,
  png/jpg/gif/webp/svg) and shown in notes as `![](/pildid/<sha>.<ext>)`. The owner uploads with
  `POST /pildid`.
- **Reminders are rules, not items** (`app/reminders.py`): a timed event that is not a lesson gets
  one ntfy push `minutes` before its start (default 30), tests the evening before, homework and
  dated tasks the day before. So "a reminder in the calendar" is an event, and the push follows.
- **The Agent SDK (0.2.159) takes a prompt as a string or as an `AsyncIterable` of user messages.**
  A string becomes `{"role":"user","content":"..."}`; the iterable is written to the CLI as
  `stream-json` lines as given, so a content list with an `image` block (base64, `media_type`)
  can go in. `runner._bridge` passes the prompt straight to `query()`, so this is a small change
  there. Unverified: that the CLI accepts an image block on `--input-format stream-json`, and that
  nothing echoes the image back through the SDK's 1 MB read buffer. **Spike first.**
- **Quick capture** (`app/capture.py`) already turns "mate kontrolltöö reedel 10.15" into an event
  and "ülesanne: …" into a task in today's daily note. The chat is the AI-shaped sibling of it:
  it understands anything, costs a model call, and can look things up before acting.

## Lead's calls

1. **The chat replaces the empty state on `/`.** No new route, no new nav item: the sidebar,
   the palette and the top bar stay. The centre pane of `/` becomes the home chat ("Kodu" is only
   this plan's name; the page title stays "Märkmik"). Opening a note from the sidebar switches to
   the editor as now; going back to `/` shows the chat again, with its history. On a phone the
   centre pane is the whole screen, so the chat is full-screen.
2. **One shared thread, with "Uus vestlus".** The home thread is `ai_threads` with
   `note_id IS NULL`. "Uus vestlus" inserts a new row; `thread_id` already picks the newest.
   Old threads stay in the database (nothing lists them yet).
3. **Pictures go in as image blocks of the user message.** The browser uploads them with the
   existing `POST /pildid` (no slug: stored, tied to no note), then sends the chat message with
   `images: [name, ...]`. The server reads the files, shrinks each to at most 1568 px on the long
   edge, re-encodes as JPEG without EXIF (Pillow, a new dependency: it also proves the bytes are a
   picture, which `images.store` does not check), base64-encodes and sends them as `image` blocks.
   The model sees the original name under `/pildid/`, so a note it writes embeds the stored
   picture: `loo_markme` gets an `images` argument that is appended as `![](/pildid/<name>)` lines
   under the body. At most 4 pictures per message; GIF and SVG are not sent to the model (SVG is
   text and could carry instructions; GIFs are rarely notes).
4. **A picture is data, like an e-mail.** The system prompt says what is in the picture is not an
   instruction (a photographed poster can say "delete all notes"). The tool set stays the only
   sandbox: the same narrow write tools, `WRITE_CAP` 3 per turn, no WebFetch, no file tools.
5. **Reminders are events** through the existing `loo_sundmus`; the prompt tells the model to use
   `kind: isiklik` for the user's own reminders and that a push comes 30 minutes before a timed
   event (the "before" rule, with its real setting read from `reminders.rules`). A reminder at an
   exact time ("push me at 18:00") needs a per-event offset: a column on `events` and a change in
   `due_reminders`. Left for later unless the user wants it now (Q2).
6. **A task tool.** `loo_ulesanne(text, date?)` writes `- [ ] text @date` into today's daily
   note through the same code as quick capture (`capture._add_task`), so "pane homseks ülesanne
   osta kinke" lands where the palette would put it. Counts against `WRITE_CAP`.
7. **No editing of other notes from the home chat in v1.** `muuda_markmet` is left out of the
   home tool set (its review window lives on the note page). The model says "ava märge X" and
   the card links to it; the user continues in that note's Abiline. Appending to arbitrary notes
   from the home chat is a later step, and it needs the same diff review.
8. **One code path for both chats.** `chat.turn(conn, None, message, images=...)` is the home
   turn; `build_prompt` already works without a note. `site/ai/index.js`'s `mountPanel` moves to
   `site/ai/panel.js` and takes options (`home: true` adds the attach button, the suggestion
   chips and "Uus vestlus"; the note panel is unchanged). The phone app's `start_url` becomes `/`
   so the camera-to-note path is one tap (Q1).

## Open questions for the user

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should the phone app open on the chat (`/`) or stay on Täna? | `/`: the chat is the quickest way in from the phone; Täna is one tap in the top bar |
| Q2 | Reminders: is "30 minutes before the event" enough, or must a reminder fire at the time the user names? | Start with events and the existing rule; add a per-event offset only if it is missing in use |
| Q3 | Which pictures: photos of notes and whiteboards only, or also screenshots, letters, timetables (events out of a picture)? | All: the model decides; events out of a picture go through `loo_sundmus` and show as cards to remove with one click |

## Spike S5: images through the Agent SDK (do first, half a day)

In `spikes/agent-sdk/` with the real token: `query()` with an `AsyncIterable` yielding one user
message whose content is a text block plus a base64 JPEG block, the same `_options` as chat
(SDK MCP tools, `include_partial_messages`, thinking off), `max_turns` 8.

- Does the CLI accept the image block on `stream-json` input and does Sonnet describe it?
- Does `stream_input`'s "wait for result, then end input" work with our SDK MCP tools (the
  bidirectional control channel) and the `_bridge` timeout?
- Is the user message echoed back (the 1 MB `max_buffer_size`)? If so, set `max_buffer_size` or
  shrink harder.
- How long does a 1568 px photo take end to end, and roughly how much context does it cost?
- Does an HEIC from an iPhone arrive as JPEG through `<input type="file" accept="image/*">` in
  Safari and the home-screen app? (iOS converts by default; check on the real phone.) If not,
  refuse `.heic` with "Salvesta JPEG-na" rather than add `pillow-heif`.

Write the findings to `docs/spikes/agent-sdk.md` (a new section) and adjust calls 3 and 8 if
needed.

## Packages

### `wp/kodu-server` (app/ai, images, views)

- `runner.stream_chat(conn, prompt, ..., images=())`: `images` is a list of `(media_type, bytes)`;
  with any, `_bridge` gets an async generator that yields one user message with the text and the
  image blocks, else the string as now. Fake mode records `len(images)` so tests can see them.
- `app/ai/pictures.py`: `prepare(path) -> (media_type, bytes)`: open with Pillow, refuse what is
  not a raster picture, EXIF-rotate, shrink to 1568 px, JPEG quality 85, no metadata. Caps:
  `IMAGES_PER_MESSAGE = 4`, output under 1 MB each.
- `chat.turn(conn, note, message, selection="", markdown=None, images=())`: the user message
  stores `images: [name, ...]`; the prompt adds `<images>` with the names so the model can embed
  them; `history()` replays a user turn with pictures as text plus "[pilt: name]" (no re-sending
  of old pictures). The home system prompt (`SYSTEM_HOME`) replaces the note sentences with the
  picture rules and the reminder rule's minutes.
- `tools.build(ctx, home=False)`: home leaves `muuda_markmet` out and adds `loo_ulesanne`;
  `loo_markme` gains `images` (names, validated against `images.NAME` and existence; `allow` not
  needed, the owner's pictures are public to the owner). `NAMES` updated.
- Views: `POST /api/ai/chat` takes `images` (a list of names, 400 on a bad name, 404 on a missing
  file, 413 over the count); `GET /api/ai/threads` (no slug) = the home thread; `POST
  /api/ai/threads` = "Uus vestlus" (a new home thread, 201 with its id). `POST /pildid` without a
  slug already works for the owner.
- `pyproject.toml`: `pillow`. Dockerfile unchanged (wheels carry libjpeg).
- Tests (`tests/ai/`): `prepare` on a large JPEG with EXIF rotation, a PNG, a GIF (refused), a text
  file named `.png` (refused); `turn` in fake mode with two pictures stores the names and passes
  two images to the runner; the home prompt has no `<note>` and names the pictures; `loo_ulesanne`
  writes into the daily note and respects `WRITE_CAP`; `loo_markme` with `images` embeds them;
  the threads endpoints; a live test (`-m live`) that sends a photo of a handwritten line and
  asserts `loo_markme` was called with the text in it.

### `wp/kodu-web` (site/ai, site/shell, manifest)

- `site/ai/panel.js`: `mountChat(root, { note, home })` from today's `mountPanel`. The note panel
  calls it unchanged. `home` adds:
  - the attach button (📎 and, on a phone, the camera through `<input type="file"
    accept="image/*" multiple>` without `capture`, so the user picks camera or library); paste and
    drop of pictures onto the composer; thumbnails with ✕ in the composer before sending, and in
    the user bubble after (`/pildid/<name>`, so a reload shows them from the stored message);
  - suggestion chips when the thread is empty: "Tee pildist märge", "Mis mul täna on?",
    "Tuleta mulle meelde…";
  - "Uus vestlus" in the chat's header row;
  - the "ava märge" card: a tool card for `loo_markme` becomes a link to the new note.
- `site/shell/note.js`: when the slug is empty, mount the home chat into `#note-empty` (renamed
  `#note-home`; the paragraph goes) and unmount it when a note opens; the right panel stays closed
  on `/`. Keyboard: `/` focuses the composer when nothing else has focus (the palette keeps
  Ctrl+K).
- `site/css/ai.css`: the home layout (a reading column of `--measure`, the composer stuck to the
  bottom with the safe-area inset on the phone, thumbnails), light and dark.
- `site/manifest.webmanifest`: `start_url` `/` (Q1).
- Playwright (`tests/visual/ai-home.spec.mjs`, chromium and mobile): `/` shows the chat and the
  sidebar; a message streams; `setInputFiles` with a PNG shows a thumbnail, the sent request
  carries its name, the user bubble shows it after reload; "Uus vestlus" empties the thread and the
  old messages do not come back; opening a note from the sidebar swaps in the editor and `/` brings
  the chat back; the phone project: composer above the home indicator, no horizontal scroll.

### Order

S5 → `wp/kodu-server` and `wp/kodu-web` in parallel (the web package works against the fake
runner and the contract below) → review → merge → `npm run build`, pytest, Playwright all
projects → deploy → the live test and a real photo from the phone.

## Contract additions (docs/CONTRACTS.md after the merge)

| WP | Endpoint | Who | Notes |
|---|---|---|---|
| F | `POST /api/ai/chat` | O | `{slug?, message, selection?, markdown?, images?: [name]}`; `images` only without `slug` in v1; ≤ 4 |
| F | `GET /api/ai/threads` | O | the home thread's messages (`[]` when none) |
| F | `POST /api/ai/threads` | O | a new home thread → `{id}` |

Stream events unchanged; a `tool` event for `loo_markme` carries `result` `{slug, title}` as now,
which the home card links to. A stored user message may carry `images: [name]`.

## Risks and how they are met

| Risk | Mitigation |
|---|---|
| The CLI rejects image blocks on streaming input | S5 before any build; fallback is the Anthropic API for this one call, which is a decision the user must make (it leaves the subscription) |
| A photographed text carries instructions | the same rule as e-mail in the prompt; tools are the sandbox; `WRITE_CAP`; created events and notes are cards with one-click removal |
| Big photos: time, tokens, the 1 MB SDK buffer | shrink to 1568 px JPEG before sending; 4 per message; S5 measures |
| Pillow decompression bombs | `Image.MAX_IMAGE_PIXELS` kept at the default (it raises), the 10 MB upload cap stays |
| A HEIC from the phone library | S5 checks Safari's conversion; otherwise refuse with a message |
| The home thread grows for months | `history()` already caps at 20 messages / 30,000 chars; "Uus vestlus" starts fresh |
| Daily cap (`AI_DAILY_CAP` 300) shared with extraction, stocks and the note chats | unchanged; a picture is one call like any turn |
