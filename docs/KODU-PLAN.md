# Kodu: a chat window on the first page (plan, 2026-10-02)

**Status (2026-10-02):** built on `wp/kodu` (steps 0–4), reviewed and tested; see docs/STATUS.md.

**The user's request:** put a chat window on the first page that can do all kinds of things: make
a note out of a picture, put reminders in the calendar, and so on.

## The user's answers (2026-10-02)

| Question | Answer |
|---|---|
| Where the phone app opens | **The chat on `/`** (`start_url` changes from `/tana`) |
| Reminders | **A reminder is its own thing, not an event.** The chat tells them apart, and when it is not clear which one the user means, it **asks** before creating anything |
| What the chat does with pictures | **Everything:** read and analyse them, write a summary note, take events, tasks and reminders out of them, or just answer a question about one |
| Changing existing notes from the home chat | **Yes, through the diff review** (the same window as Abiline's proposals) |

## What exists

- **Abiline already is that chat**, but only inside a note: `app/ai/chat.py` runs one thread per
  note, replays its history from `ai_threads`/`ai_messages`, and gives the model eight app tools
  plus WebSearch (`app/ai/tools.py`). The browser side (`site/ai/index.js`, `mountPanel`) renders
  the thread, streams over SSE and shows cards for tools, created events and proposals.
- **A thread outside any note is already in the schema** (`ai_threads.note_id` is nullable,
  `chat.thread_id(conn, None)`). Nothing shows it.
- **`/` is empty:** `note.html` with no slug shows `#note-empty` in the centre.
- **Pictures** are stored by content hash under `data/pildid` (`images.store`, 10 MB, png/jpg/gif/
  webp/svg); the owner uploads with `POST /pildid`, a slug is optional.
- **Reminders today are rules over events** (`app/reminders.py`): tests the evening before,
  homework the day before, timed events `minutes` before. There is no "push me at 18:00". The job
  `reminders` runs every 60 s and claims each push in `reminders_sent`.
- **The reminders' ntfy card still has no topic** (docs/STATUS.md, cutover). Until it has one, no
  reminder of any kind reaches the phone. Setting it is the user's step (Ühendused → ntfy).
- **The review window** (`site/ai/review.js`, `review({base, markdown, editor, normalize})`)
  needs only `editor.getMarkdown()` and `editor.replaceMarkdown(md)` from the editor. It already
  re-reads the current text at "Aktsepteeri" and applies kept changes three-way.
- **Server-side note writes** go through `notes.write_body`, which raises `NoteBusy` while the
  note is open in an editor (collab session).
- **The Agent SDK (0.2.159) takes a prompt as a string or an `AsyncIterable` of user messages.**
  The iterable is written to the CLI as `stream-json` lines, so a content list with an `image`
  block can go in. `runner._bridge` passes the prompt straight to `query()`. Unverified: that the
  CLI accepts the image block that way, and what it costs in time and context. **Spike first.**

## Lead's calls

### Page and thread

1. **The chat replaces the empty state on `/`.** The sidebar, top bar and palette stay. Opening a
   note swaps in the editor as now; `/` brings the chat back with its history. On a phone the
   chat is full-screen with the composer above the home indicator.
2. **One home thread, with "Uus vestlus".** The home thread is the newest `ai_threads` row with
   `note_id IS NULL`; "Uus vestlus" inserts a new one. Old threads stay in the database, unlisted.
3. **One code path for both chats.** `chat.turn(conn, None, message, images=...)` is the home
   turn. `mountPanel` moves to `site/ai/panel.js` as `mountChat(root, {note, home})`; the note
   panel calls it unchanged.

### Pictures

4. **Pictures go to the model as image blocks of the user message.** The browser uploads with the
   existing `POST /pildid` (no slug), then sends `images: [name, ...]` with the message. The server
   opens each with Pillow (a new dependency: it also proves the bytes are a raster picture), turns
   it upright by EXIF, shrinks it to 1568 px on the long edge, re-encodes it as JPEG without
   metadata and sends it base64. At most 4 per message. SVG and GIF are refused for the model.
5. **The model gets the stored names**, so a note it writes can embed the picture:
   `loo_markme` gains `images: [name]`, appended as `![](/pildid/<name>)` under the body.
6. **Only the message that carries a picture sends it.** Later turns replay it as
   "[pilt: <name>]". To ask about an earlier picture again, the user attaches it again (the
   thumbnail in the thread has "Lisa uuesti").
7. **A picture is data, like an e-mail.** The system prompt says text in a picture is not an
   instruction. The tool list stays the only sandbox: no WebFetch, no file tools, `WRITE_CAP` 3.

### Reminders

8. **A reminder is a row in a new table `reminders`**: `text`, `remind_at` (UTC), optional
   `event_id` (a reminder about an event) and `note_id`, `done_at`, `source`, `created_at`. It
   is pushed once, at `remind_at`, through ntfy. Events keep their rule-based reminders unchanged.
9. **Event or reminder, as the model is told:**
   - an **event** is something that happens at a time and belongs in the calendar ("trenn homme
     kell 18", "kontrolltöö reedel");
   - a **reminder** is a push to the phone at a time ("tuleta meelde", "ära unusta", "teavita
     mind");
   - "tuleta meelde päev enne kontrolltööd" is a reminder with `event_id`, its time computed by
     the model and checked in code;
   - **when the words fit both, the model asks one short question and creates nothing** ("Kas
     panen kalendrisse sündmuse või saadan kell 18 meeldetuletuse?").
10. **A reminder without a time** ("tuleta homme meelde piima osta") gets 09:00 local and the
    answer says so.
11. **Reminders show on Täna and in the calendar**, with a 🔔 and their own colour, like tasks
    (not draggable). On Täna each has ✓ (done) and ✕ (delete). Changing a reminder's time is done
    through the chat (`muuda_meeldetuletust`) in v1; there is no edit form.
12. **A late push is still sent within 12 hours** (worker down, VM restart) with "(hilinenud)" in
    the title; older ones are marked missed and shown on Täna as missed. Event rules keep their
    2-minute grace.
13. **Out of v1:** repeating reminders ("iga esmaspäev"), snooze from the notification, reminders
    from quick capture.

### Changing notes from the home chat

14. **Two proposal tools, both reviewed as a diff:**
    - `muuda_markmet(slug, markdown)`: the whole new text of any note. The model must have read
      it in this turn with `loe_markmet` (the server checks; otherwise "Loe märge enne läbi").
      Refused over `NOTE_CAP` as now.
    - `lisa_markmesse(slug, text, under?)`: the server builds the proposal itself by appending
      `text` at the end, or at the end of the section under the heading `under`. The model does
      not rewrite the note, so a long shopping list stays cheap and safe. Old threads already
      have `lisa_markmesse` cards without a proposal; those keep showing as plain tool cards.
    - Both store `{slug, base, rev, proposal}` with the tool message and count against `WRITE_CAP`.
15. **Review on the home page:** "Vaata üle" opens the same review window against a stand-in
    editor whose `getMarkdown()` is the note's current text from `GET /api/notes/<slug>` and whose
    `replaceMarkdown()` posts the result to a new `POST /api/ai/proposals/<id>/apply
    {markdown, rev}`. The server writes it with `notes.write_body` when `rev` is still current.
    - Another write came in meanwhile (409 "muudetud"): the window reopens on the new text.
    - The note is open in an editor (`NoteBusy`, 409 "lahti"): the card offers "Ava märge", which
      opens `/m/<slug>?ettepanek=<id>`; the note page then opens the same review against the real
      editor, where co-editors see the change and Ctrl+Z undoes it.
16. **The note panel's Abiline stays as it is** (only the open note); only the home chat gets the
    edit-any-note tools.

## Implementation plan

Each step ends with its check. Packages are built in worktrees under `docs/CONTRACTS.md`'s rules;
the lead writes the schema and contract changes first (step 1).

### Step 0: Spike S5, images through the Agent SDK (half a day)

In `spikes/agent-sdk/`, with the real token and chat's `_options` (SDK MCP tools,
`include_partial_messages`, thinking off, `max_turns` 8):

- `query()` with an async generator yielding one user message: a text block and a base64 JPEG.
- Does Sonnet describe the picture? Does `stream_input` end input after the result while our SDK
  MCP tools are registered? Does the `_bridge` timeout still cancel it?
- Is the user message echoed back through the SDK's 1 MB read buffer?
- Time and context cost of one 1568 px photo; of four.
- On the real iPhone: does `<input type="file" accept="image/*">` deliver JPEG for an HEIC photo
  in the home-screen app?

→ **verify:** findings in `docs/spikes/agent-sdk.md` (new section S5). If the CLI refuses image
blocks, stop and ask the user: the fallback is the Anthropic API for picture turns, which leaves
the subscription.

### Step 1: Lead: schema and contracts (on `arendus`)

- `app/schema.sql`: table `reminders` (call 8) with an index on `remind_at` where
  `done_at IS NULL`; `reminders_sent` reused with `item_kind = 'reminder'`, `rule = 'at'`.
- `docs/CONTRACTS.md`: the endpoints and tool names below, the stream event `edit` gaining
  `rev` and `slug` of any note, the `reminders` module interface.

→ **verify:** `uv run pytest -q` passes on the unchanged code with the new schema; a fresh DB and
the live DB copy both start.

### Step 2: `wp/kodu-meelde`: reminders (server + Täna + calendar)

- `app/reminders.py`: `create(conn, text, remind_at, *, event_id=None, note_id=None, source)`,
  `update`, `remove`, `set_done`, `upcoming(conn, start, end)`; `due_reminders` adds the
  reminders whose `remind_at` has come (12-hour grace, "(hilinenud)"), and marks older ones
  missed.
- `events.occurrences` gains reminder items (`reminder_id`, `start`, `title`, `done`), like tasks.
- API: `GET /api/reminders?start=&end=`, `POST /api/reminders`, `PATCH /api/reminders/<id>`
  (`remind_at`, `done`), `DELETE /api/reminders/<id>`; `/api/today` gains `reminders`.
- `site/shell/today.js`: a "Meeldetuletused" block with ✓ / ✕ and missed ones marked.
  `site/calendar/`: 🔔 items, not editable, `kind-meeldetuletus` colour in light and dark.
- Tests: pytest for due times across the DST change on 2026-10-25, the 12-hour grace, one push per
  reminder after a restart, a reminder tied to a removed event; Playwright for Täna's ✓ / ✕ and a
  calendar item.

→ **verify:** pytest and the new specs pass; a reminder two minutes ahead arrives on a test ntfy
topic in a local run.

### Step 3: `wp/kodu-server`: chat with pictures and home tools

- `runner.stream_chat(..., images=())`: with images, `_bridge` gets an async generator yielding
  one user message of text plus image blocks; fake mode records them for tests.
- `app/ai/pictures.py`: `prepare(path) -> (media_type, bytes)` per call 4.
- `chat.turn(..., images=())`: stores `images: [name]` on the user message, adds `<images>` with
  the names to the prompt, replays them as "[pilt: name]"; `SYSTEM_HOME` with the picture rule,
  the event-or-reminder rule (call 9), the 09:00 default, the edit tools and today's date.
- `tools.build(ctx, home=True)`: adds `loo_meeldetuletus(text, date, time?, event_id?)`,
  `muuda_meeldetuletust(id, date?, time?, text?)`, `meeldetuletused(from?, to?)`,
  `loo_ulesanne(text, date?)` (through `capture._add_task`), `lisa_markmesse`; `muuda_markmet` for
  any note read in this turn; `loo_markme` with `images`. Date and time are checked in code as in
  `loo_sundmus`; a reminder in the past is refused.
- `app/views/ai.py`: `images` on `POST /api/ai/chat`; `GET` and `POST /api/ai/threads` for the
  home thread; `POST /api/ai/proposals/<id>/apply`.
- `pyproject.toml`: `pillow`.
- Tests (fake runner): `prepare` on a rotated large JPEG, a PNG, a GIF and a text file named
  `.png`; a turn with two pictures passes two image blocks and stores the names; history replays
  no image; each new tool's validation and `WRITE_CAP`; `muuda_markmet` refused without
  `loe_markmet`; `lisa_markmesse` under a heading and at the end; apply with a stale `rev` (409),
  a busy note (409) and a good one (body written, `on_saved` ran). Live tests (`-m live`): a photo
  of a handwritten line becomes a note with that text; "tuleta meelde homme kell 18 helistada"
  makes a reminder; "homme kell 18 trenn" with no other words makes the model ask, not create.

→ **verify:** `uv run pytest -q` and `uv run pytest -m live tests/ai/test_live.py` pass.

### Step 4: `wp/kodu-web`: the home chat

- `site/ai/panel.js`: `mountChat(root, {note, home})` out of `mountPanel`; the note panel is
  unchanged. Home adds:
  - 📎 (on the phone it offers camera or library), paste and drop onto the composer, thumbnails
    with ✕ before sending, thumbnails in the user bubble after (with "Lisa uuesti");
  - suggestion chips on an empty thread: "Tee pildist märge", "Mis mul täna on?",
    "Tuleta mulle meelde…";
  - "Uus vestlus";
  - cards: a new note links to it; a reminder card with ✕; a proposal card for any note with
    "Vaata üle" (call 15) and, after a busy 409, "Ava märge".
- `site/shell/note.js`: mount the home chat in place of `#note-empty` when the slug is empty,
  unmount on a note; `?ettepanek=<id>` on a note page opens that proposal's review once the
  editor is ready. `/` focuses the composer when nothing else has focus.
- `site/css/ai.css`: the home column, the sticky composer with the safe-area inset, thumbnails,
  light and dark. `site/manifest.webmanifest`: `start_url` `/`.
- Playwright (`tests/visual/ai-home.spec.mjs`, chromium and mobile): a message streams; a PNG
  through `setInputFiles` shows a thumbnail and its name goes in the request and survives a
  reload; "Uus vestlus" empties the thread; a `lisa_markmesse` proposal accepted on the home page
  changes the note, and with the note open in another page the card offers "Ava märge" and the
  review opens there; a reminder card's ✕ removes it from Täna; on the phone the composer sits
  above the home indicator with no sideways scroll.

→ **verify:** `npm run build`, then Playwright chromium, firefox and mobile all pass (check the
summary line, not a piped exit code).

### Step 5: Review, merge, deploy

- A separate reviewer agent per package; a security pass on the new tools (the edit-any-note path,
  picture handling, the apply endpoint's CSRF and rev check), recorded in
  `docs/SECURITY-REVIEW.md`.
- Merge into `arendus`; full `uv run pytest -q`, Playwright all projects, the live AI tests;
  `docker compose up -d --build`.
- **The user's steps:** set the reminders' ntfy topic (Ühendused → ntfy) and subscribe the phone;
  re-add Märkmik to the iPhone home screen, because iOS keeps the old start address.

→ **verify on the live app:** a photo from the phone becomes a summary note with the picture in
it; "tuleta meelde 5 minuti pärast …" arrives on the phone; "lisa piim poe nimekirja" opens the
diff on the home page and the note changes after "Aktsepteeri".

## Contract additions

| WP | Endpoint | Who | Notes |
|---|---|---|---|
| F | `POST /api/ai/chat` | O | `{slug?, message, selection?, markdown?, images?: [name]}`; `images` only without `slug`, ≤ 4 |
| F | `GET /api/ai/threads` | O | the home thread's messages (`[]` when none) |
| F | `POST /api/ai/threads` | O | a new home thread → `{id}` |
| F | `POST /api/ai/proposals/<id>/apply` | O | `{markdown, rev}` → `{rev}`; 409 `{error, reason: "muudetud" \| "lahti"}` |
| D | `GET /api/reminders?start=&end=` | O | `[{id, text, remind_at, event_id, note_slug, done, missed}]` |
| D | `POST /api/reminders`, `PATCH /api/reminders/<id>`, `DELETE /api/reminders/<id>` | O | `{text, remind_at}`; PATCH `remind_at`, `done` |
| D | `GET /api/today` | O | gains `reminders: [...]` |

Stream events: `reminder_created {id, text, remind_at}` after `loo_meeldetuletus`; `edit` gains
`rev` and may name any note (home only).

## Risks and how they are met

| Risk | Mitigation |
|---|---|
| The CLI rejects image blocks on streaming input | Step 0 before any build; the fallback (API key for picture turns) is the user's decision |
| A photographed or stored text carries instructions | the e-mail rule in the prompt; tools are the sandbox; `WRITE_CAP`; every note change is a reviewed diff; created items are cards with ✕ |
| The model edits a note it never saw | `muuda_markmet` needs `loe_markmet` in the same turn; `lisa_markmesse` never rewrites |
| A diff applied over newer text | `rev` check on apply; three-way apply in the review window; a busy note is reviewed in its editor |
| Reminders never arrive | the ntfy topic is empty today: a user step in Step 5, and Täna shows "ntfy pole seadistatud" next to the reminders block while it is empty |
| Wrong time around DST (2026-10-25) | `remind_at` in UTC, built from local time with `util.TALLINN`; a test across the change |
| Big photos: time, tokens, the 1 MB SDK buffer | 1568 px JPEG, 4 per message; Step 0 measures |
| Pillow decompression bombs | `Image.MAX_IMAGE_PIXELS` left at the default (it raises); the 10 MB upload cap stays |
| The daily cap (`AI_DAILY_CAP` 300) shared with extraction, stocks and the note chats | unchanged; a picture turn is one call |
