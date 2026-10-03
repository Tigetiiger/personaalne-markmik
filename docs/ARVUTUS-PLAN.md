# Templates, Python cells and the Mouser order (plan, 2026-10-03)

**The user's request:** the home chat made a plain "Mouser order template" note. The user wants a
real template: values that compute themselves, part details taken from links, a templates folder,
and a button that makes a note from a template. The computing should work like inline code or a
code block that runs, similar to a Jupyter notebook.

## The user's answers (2026-10-03)

| Question | Answer |
|---|---|
| Where Mouser part details come from | **The Mouser Search API** with the user's own key (the product pages answer "Access denied", Akamai) |
| Where computed values go | **Into the note** (Markdown, git, gbrain see them) |
| What the Mouser order computes | **Unit price by quantity** (from the price breaks) and **stock and warnings** (not enough in stock, below the minimum, not a multiple). No VAT or shipping |
| Templates | **A templates folder ("Mallid")**; Mouser order is the first template |
| The "make a note from this template" button | **On the template note** only |
| Links | **A link column that keeps the link**; details are filled in automatically **only for Mouser links** for now |
| The old note "Mouser orderi template" | Stays as it is; a new template goes in `Mallid/`, orders in `tellimused/` |
| Language of the cells | **Real Python in a sandbox** |
| When cells run | **Computing runs by itself** after every change; a **Mouser lookup** runs on ▶ / "Käivita kõik" or for a link never looked up |
| How results look | **Code visible by default, as in Jupyter, with the output under it**; a cell can be **hidden so only its output shows** |
| Where cells work | **In every note** |

## What exists

- **No templates.** A note is Markdown in `notes.body`; unknown frontmatter keys live in
  `notes.frontmatter_extra` and survive git sync.
- **The editor** (Muya, `tools/editor/`) exposes `getMarkdown()`, `applyBlockEdit(path, text)`
  (false when the block changed meanwhile) and `replaceMarkdown(md)`; both go out as collab ops, so
  co-editors see them and Ctrl+Z undoes them. Muya renders fenced code blocks with their info string.
- **Guests can edit shared notes** (`/j/<token>` edit links). Anything in a note may have been
  written by someone else: this is the main constraint on running code.
- **Docker is on the VM;** the app's services are in `docker-compose.yml` (web, worker, gbrain,
  ntfy, cloudflared). Containers already run hardened (read-only root, no new privileges).
- **Mouser:** `https://api.mouser.com/api/v1/search/partnumber?apiKey=…` answers (an invalid key
  gives `{"Errors":[{"Code":"Invalid","PropertyName":"API Key"}]}`); a product page fetched by code
  gets an Akamai "Access to this page has been denied" page.
- **Vault + connection cards** hold keys (Fernet); the Turuandmed card is the pattern for API keys.

## How it looks in a note

````markdown
# Mouser tellimus 2026-10-03

## Komponendid

| Link | Mouser nr | Tootja nr | Tootja | Kirjeldus | Kogus | Ühikuhind | Summa | Laos | Hoiatus |
|---|---|---|---|---|---|---|---|---|---|
| https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=… | 595-NE555P | NE555P | Texas Instruments | Timers DIP-8 | 25 | 0,342 € | 8,55 € | 41 230 | |

Kokku: `= kokku ⇒ 8,55 €`

```python arvuta peida
t = tabel("Komponendid")
m = mouser(t["Link"], t["Kogus"])
t[["Mouser nr", "Tootja nr", "Tootja", "Kirjeldus", "Ühikuhind", "Laos", "Hoiatus"]] = m[["nr", "tootja_nr", "tootja", "kirjeldus", "hind", "laos", "hoiatus"]]
t["Summa"] = t["Kogus"] * t["Ühikuhind"]
kokku = t["Summa"].sum()
```
```väljund
(the cell's printed output and the value of its last line, as in Jupyter)
```
````

- A **cell** is a fenced block with the info string `python arvuta`; `peida` in the info string
  hides its code (only the output shows; a click shows the code). Plain `python` blocks stay plain
  code and never run.
- The **output** is the ` ```väljund ` block right after the cell. The app owns it: it is rewritten
  on every run, and an edit to it by hand is overwritten.
- **Cells share variables** in note order, like one Jupyter kernel per note.
- **`tabel("Komponendid")`** gives the table under that heading (or `tabel(1)`, the first table) as
  a pandas DataFrame, with numbers and "0,342 €" read as numbers. When a cell changes a DataFrame
  it got from `tabel()`, the table in the note is rewritten (column order and other columns kept,
  numbers written the Estonian way).
- **Inline values:** `` `= kokku` `` in the text shows a variable after the run; the file keeps
  `` `= kokku ⇒ 8,55 €` `` so the value is in Markdown and git.
- **`mouser(links, kogused)`** gives a DataFrame with the part details per row (below).

## Lead's calls

### Running code safely

1. **Code runs in its own container, `arvutus`, never in web or worker.** Image: Python 3.12 slim
   plus pandas and numpy. `network_mode: none`, read-only root, a small `tmpfs` for `/tmp`, a
   non-root user, `cap_drop: [ALL]`, `no-new-privileges`, limits on memory (512 MB), CPU (1) and
   processes (64). No secrets in its environment, no volumes except one socket directory.
2. **The app talks to it over a Unix socket** on a shared volume (no network needed). The
   container runs a small server; **every run is a fresh Python process** with rlimits (CPU time,
   address space, file size, open files) and a wall-clock timeout (5 s automatic, 30 s on ▶).
   Nothing survives a run: "one kernel per note" means the note's cells re-run in order each time,
   which is cheap at notebook-of-a-few-cells scale and leaks no state between notes.
3. **The sandbox gets only the note's own cells and tables.** It cannot reach the database, other
   notes, files, the network or the app. `mouser()` inside the sandbox is a request back to the app
   over the same socket; the app checks it (Mouser links only, rate limited) and answers with data.
4. **Code written by guests does not run by itself.** While a note has an active edit link,
   automatic runs are off for it; the owner presses ▶ and sees "Koodi võisid muuta lingiga
   külalised". Even then the code sees only that note, which the guest can already read.
5. **Only the owner runs code** (`POST /api/arvuta` is owner-only, rate limited). Guests and the
   shared view show the outputs that are in the Markdown.
6. **Output is data, not HTML.** It goes into a fenced block and table cells as text (pipes and
   backticks escaped), so it renders through the existing sanitising (nh3, DOMPurify, CSP).
   Capped at 20,000 characters per cell.

### When cells run

7. **Automatic runs** start 800 ms after the note's cells, tables or inline expressions change,
   from the owner's open editor. A hash of those inputs (outputs excluded) is kept, so writing the
   output back never starts another run, and `datetime.now()` in a cell cannot loop.
8. **▶ on a cell** runs the note up to and including that cell; **"Käivita kõik"** in the crumb row
   runs every cell. Both also refresh Mouser data (prices, stock).
9. **Results go in as ordinary edits** (`applyBlockEdit` per output block, table and inline value),
   so co-editors see them and Ctrl+Z undoes them; a block the user is typing in is skipped and
   picked up by the next run. If a block edit cannot add a missing output block, the editor's
   `replaceMarkdown` does it (spike S7 decides).

### Mouser

10. **`app/mouser.py` calls the Search API** with the key from a new connection card "Mouser" (vault
    `mouser_key`, Testi button). The key is a query parameter, so it is masked in every stored
    error and log line.
11. **Link → part:** the last path segment of a `mouser.*` `/ProductDetail/` link is the Mouser or
    manufacturer part number; when the search returns several parts, the one whose
    `ProductDetailUrl` has the link's `qs` wins. A bare Mouser number in the Link cell works too.
    Other shops' links stay in the Link column and are not looked up.
12. **Cache:** a table `mouser_parts` (part number → response, fetched_at). Automatic runs use only
    the cache plus lookups of parts never seen; ▶ refreshes parts older than an hour. The API's
    limits (about 30 calls a minute, 1,000 a day) are enforced in code.
13. **Unit price** is the price break for the row's quantity; **Hoiatus** says "laos ainult N",
    "miinimum N", "kordne N" or "ei leitud". The row total and the order total are plain cell code
    in the template, not Mouser logic.

### Templates

14. **A template is any note in the folder `Mallid`** (and its subfolders). Its crumb row gets
    "Tee märge sellest mallist", which asks for the title (default: the template's title plus
    today's date) and the folder (default: the template's frontmatter key `mall_kaust`, else the
    root), then opens the new note.
15. **Placeholders** in the template's body and title are filled in when the note is made:
    `{{kuupäev}}` (2026-10-03), `{{kuupäev_pikk}}` (3. oktoober 2026), `{{nädalapäev}}`, `{{kell}}`.
16. **The Mouser template** (`Mallid/Mouser tellimus`, `mall_kaust: tellimused`) is created once
    after deploy through the API, not seeded on every start, so the user can change or delete it.

## Open questions for the user

None block the start. Defaults the user can change:

| # | Default | Change if… |
|---|---|---|
| D1 | Sandbox libraries: standard library, numpy, pandas | you want more (matplotlib for plots as pictures is a natural next step, not in v1) |
| D2 | Automatic runs time out after 5 s, ▶ after 30 s | your notebooks are heavier |
| D3 | Prices in the currency Mouser returns for your key (EUR expected for mouser.ee) | — |

**The user's step:** create a Mouser Search API key (mouser.ee → My Account → APIs → Search API)
and paste it into Ühendused → Mouser.

## Spikes (first, about a day)

- **S6 sandbox:** build `arvutus`; check that a run cannot reach the network, write outside `/tmp`,
  see the app's environment, fork-bomb, eat memory or loop forever (each ends with a clean error
  and the next run works); measure a run with `import pandas` (cold process) to size the automatic
  debounce.
- **S7 editor:** in Muya, a toolbar over ` ```python arvuta ` blocks (▶, peida/näita, status),
  hiding the code of a `peida` block, `applyBlockEdit` on an output block and a table while the
  caret is elsewhere in the note and while it is in the table, adding a missing output block, and
  updating an inline code span.
- **S8 Mouser** (needs the user's key): part number and manufacturer part number searches, the
  `qs` match for a link with several results, the price and currency format, the fields for
  minimum, multiple and stock, and the rate-limit answers.

Findings go to `docs/spikes/arvutus.md`; calls 2, 9 and 11 change if the spikes say so.

## Packages and steps

Each ends with its check. The lead writes the schema (`mouser_parts`) and the contract additions
first.

1. **`wp/arvuta-sandbox`**: `Dockerfile.arvutus`, the in-container server (`sandbox/server.py`:
   socket, per-run process, rlimits, timeout, the `mouser` back-channel), the compose service, and
   `app/arvuta/client.py` (send cells and tables, get outputs).
   → check: pytest runs the same server as a local process for the protocol; `pytest -m docker`
   runs the escape attempts from S6 against the real container.
2. **`wp/arvuta-core`**: `app/arvuta/cells.py` (find cells, outputs, tables and inline values in
   Markdown; write them back; the input hash), `tabel()` read/write and Estonian number format, and
   `POST /api/arvuta {slug, markdown, run: "auto" | "all" | <cell index>}` → the blocks to change.
   → check: pytest on parsing round trips (a note without cells is untouched byte for byte), table
   write-back keeping other columns, inline values, the hash skipping a run, the guest-link rule,
   owner-only.
3. **`wp/mouser`**: `app/mouser.py`, the cache table, the "Mouser" card, link parsing, price
   break choice, warnings, the call limits.
   → check: pytest with recorded API answers (from S8); one `-m live` test with the real key.
4. **`wp/arvuta-editor`**: the cell toolbar, hidden cells, "Käivita kõik", the automatic run and
   applying the results; status per cell (töötab / 0,4 s / viga).
   → check: Playwright on chromium, firefox and mobile with the sandbox replaced by a test route;
   typing in a table while a run lands keeps the caret and the text.
5. **`wp/mallid`**: the `Mallid` folder rule, placeholders, `POST /api/notes/from-template
   {template, title, folder}`, the crumb-row button and its dialog.
   → check: pytest on placeholders and the endpoint; Playwright: make a note from a template, it
   opens with today's date and the template's folder.
6. **Review, security audit, deploy.** A separate reviewer per package; an attack audit of the
   sandbox and `/api/arvuta` (a guest-written cell, an escape from the container, output that tries
   to become HTML), written into `docs/SECURITY-REVIEW.md`. Merge, full pytest and Playwright,
   `docker compose up -d --build`, then the Mouser template made through the API.
   → check on the live app: a new Mouser order from the template, a pasted Mouser link fills its
   row, changing the quantity changes the unit price and totals, a hidden cell shows only its
   output.

Packages 1, 3 and 5 can be built side by side; 2 needs 1's protocol, 4 needs 2's endpoint.

## Contract additions

| WP | Endpoint | Who | Notes |
|---|---|---|---|
| arvuta | `POST /api/arvuta` | O | `{slug, markdown, run}` → `{changes: [{path, text}], status: [{cell, ok, seconds, error?}]}`; 409 while a run of the same note is going |
| mouser | `GET /api/mouser/part?link=` | O | the cached or fetched part (for the card's Testi and debugging) |
| mallid | `POST /api/notes/from-template` | O | `{template, title?, folder?}` → note JSON |

## Risks

| Risk | Mitigation |
|---|---|
| Code from a guest or a pasted text runs on the VM | separate container with no network or secrets, fresh process per run, rlimits, timeouts; automatic runs off for notes with an edit link; attack audit before deploy |
| A sandbox escape through a kernel bug | the container is the only barrier; the VM is the blast radius (Proxmox snapshots); gVisor (`runsc`) can be added later as the runtime without changing the protocol |
| Automatic runs fight the user's typing | results go in as block edits, skipped for the block being typed in; the input hash stops loops |
| Mouser limits or a changed API | cache, call limits, clear "ei leitud" warnings; prices refresh only on ▶ |
| pandas makes automatic runs slow | S6 measures; if a cold `import pandas` is too slow, the server keeps a pre-imported process ready to fork from |
