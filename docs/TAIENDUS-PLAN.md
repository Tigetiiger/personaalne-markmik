# A real code editor with completion in Python cells (plan, 2026-10-04)

**The user's request:** autocomplete in the Python cells, like PyCharm.

## The user's answers (2026-10-04)

| Question | Answer |
|---|---|
| How | **A real code editor (CodeMirror) in each cell**, as long as it stays inside the same note |
| When suggestions appear | **While typing**, after `.` or two letters; Ctrl+Space always; Esc hides |
| Beyond names | **All four:** the function's signature after `(`, the docstring of the chosen suggestion, column names after `t["`, and errors marked while typing |
| Phone | **Desktop only**; the phone keeps today's cells |

## What exists and what was measured

- Cells are Muya code blocks (`arvuta`, `arvuta-peida`) in the note; Muya renders them as one
  contenteditable and re-renders a block's DOM on every change, so another editor cannot live
  *inside* that DOM. The note page's editor handle has `blocks()`, `blockNodes()`, `focusBlock`,
  and collab.js changes blocks as json1 ops (docs/JUPYTER-PLAN.md).
- **Jedi** (the completion engine of IPython) on this machine: `t = tabel("Andmed")` then `t.sort_v`
  completes `sort_values` with its signature and docstring once `tabel()` is described as returning
  a DataFrame. **3.8 s cold, 0.09 s warm**: completion needs one long-lived, warmed-up process.
- The `arvutus` sandbox kills every process between runs and runs untrusted code: completion cannot
  live there.

## Lead's calls

### The editor in the note

1. **CodeMirror 6 over each cell.** On a desktop, every cell's Muya block gets a CodeMirror editor
   laid exactly over its code (same font, line height and width). The Muya block stays underneath
   as the stored text: CodeMirror shows and edits it, Muya keeps the Markdown, git and co-editing.
   The overlay follows layout changes (MutationObserver and ResizeObserver, as the toolbars do).
   If CodeMirror fails to load, the cells stay editable as today.
2. **Syncing:** a change in CodeMirror goes into the Muya block as one text edit through a new
   `setCodeText(index, text, expected)` in collab.js (refused when the block changed meanwhile).
   A change that arrives from a co-editor while the cell is focused is merged into CodeMirror by
   its common prefix and suffix; two people typing in the same cell at the same moment can still
   lose a few characters, other cells and the rest of the note are unaffected.
3. **Editing like an IDE:** syntax colours (CodeMirror's Python), auto-indent after `:`, Tab and
   Shift+Tab indent by 4 spaces, bracket matching, auto-closed brackets and quotes, Ctrl+/ comments
   a line, CodeMirror's own undo inside the cell.
4. **Keys stay notebook keys:** Shift+Enter runs and moves on, Ctrl+Enter runs; ↑ on the first line
   and ↓ on the last move the caret into the block above or below (Muya); Esc leaves the cell.

### Completion, signatures, docs, errors

5. **A second small container, `taiendus`:** Jedi and pyflakes with the same numpy, pandas and
   matplotlib versions as `arvutus`; `network_mode: none`, read-only root, no capabilities, uid
   1002, no secrets; its own Unix socket volume shared only with web. It is long-lived and warmed up
   at start (one pandas completion). It never runs the note's code: Jedi and pyflakes only read it.
6. **What it is asked:** the code of the cells *before* this one, then this cell, with a short stub
   in front (`tabel(...) -> pandas.DataFrame`, `mouser(...) -> pandas.DataFrame`, `euro(x) -> str`,
   with docstrings in Estonian). Kinds: `complete` (names with type and docstring), `signature`
   (inside a call), `lint` (syntax errors and pyflakes: undefined names, unused imports, redefined
   names). Positions are mapped back to the cell. One request at a time, 3 s cap; the browser
   sends only its latest request (debounced 120 ms while typing).
7. **Column names** after `t["` or `t['` come from the note's own tables (Muya's block state), in the
   browser, merged first into the list.
8. **`POST /api/arvuta/taienda`** (owner only, rate limited for typing): `{kind, before, code, line,
   col}` → `{items: [{label, type, detail, doc}]}` | `{signature: {label, params, active, doc}}` |
   `{diagnostics: [{from, to, severity, message}]}`. Code is capped (200 kB).
9. **CodeMirror is lazy:** its bundle (`@codemirror/*`, about 200 kB minified) loads only on a note
   page with cells, on a desktop.

## Defaults

| # | Default | Change if… |
|---|---|---|
| D1 | Indent 4 spaces | you prefer 2 |
| D2 | Errors shown 600 ms after typing stops | sooner or later |
| D3 | At most 50 suggestions in the list | — |

## Spike S10 (first, half a day)

- A CodeMirror editor laid over a Muya code block: positions stay right while text above changes,
  the block grows with the code, a remote change arrives, the note is opened by a guest.
- `setCodeText` through collab: Ctrl+Z in Muya after editing in CodeMirror, and co-editors' view.
- Jedi and pyflakes in a container like `taiendus`: memory warmed up, answer time for `t.`, `plt.`
  and `pd.read_` completions, signature inside `t.sort_values(`.

Findings go to `docs/spikes/arvutus.md` (section S10).

## Packages and steps

1. **`wp/taiendus-server`**: `sandbox/taiendus/` (Dockerfile, `server.py` with Jedi and pyflakes,
   the stub), the compose service and volume, `app/arvuta/taiendus.py` (socket client), the
   endpoint.
   → check: pytest with Jedi and pyflakes locally (dev group): completions for `t.`, a signature,
   an undefined name and a syntax error mapped to the right cell line, the stub's names known,
   names from later cells unknown, owner only, size cap; `pytest -m docker` against the container.
2. **`wp/taiendus-editor`**: npm `@codemirror/*`; `site/arvuta/koodiredaktor.js` (the overlay, the
   completion source with column names, signature tooltip, docs panel, lint, keymaps, theme in the
   app's light and dark tokens); `setCodeText` in collab.js and the handle.
   → check: Playwright on chromium and firefox: typing `t.sor` lists `sort_values` with its doc and
   Tab inserts it; `t["` lists the table's columns; `(` shows the signature; an undefined name is
   underlined with its message; Shift+Enter still runs and moves on; a second page's edit to the
   cell shows up in the first; text typed is saved in the note's Markdown; the phone keeps Muya.
3. **Review, deploy:** a reviewer; full pytest and Playwright; `docker compose up -d --build`;
   check live on a notebook note.

## Risks

| Risk | Mitigation |
|---|---|
| The overlay drifts from the Muya block | one positioning routine for toolbars and editors, observers on both; spike S10 first |
| Same-cell co-editing loses characters | prefix/suffix merge; stated limit; cells are usually one person's |
| Jedi slow or heavy | warmed at start, one process, 3 s cap, latest request only; container memory limit |
| Hostile code exploits the parser | read only, no execution, own container with no network or secrets |
| A big new dependency | CodeMirror only, lazy-loaded; nothing else in the app uses it |
