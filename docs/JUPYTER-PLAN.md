# Notes as notebooks: Jupyter-like Python cells (plan, 2026-10-04)

**The user's request:** make the Python cells (docs/ARVUTUS-PLAN.md) more like a Jupyter notebook:
colours in the code, and more of what Jupyter has, such as adding another block, if it can be done
properly.

## The user's answers (2026-10-04)

| Question | Answer |
|---|---|
| Approach | **Better cells inside notes.** A notebook stays a normal note: Markdown, git, live co-editing, templates, gbrain |
| Kernel | **Re-run the whole note each time** (as now): no long-lived process, same result every time |
| Features | **All four:** "+ Kood / + Tekst" (and moving, deleting cells); **Shift+Enter and [n] numbers**; **plots** (matplotlib as pictures); **rich outputs** (a DataFrame as a real table, errors in red, long outputs folded) |
| Automatic runs | **Stay, with a switch per note**: a notebook can go Jupyter-style, running only on ▶ / Shift+Enter |

## What exists

- Cells are fenced blocks with the language `arvuta` or `arvuta-peida`; the ```` ```väljund ````
  block under a cell is its output. `app/arvuta/cells.py` finds and rewrites them;
  `sandbox/kernel.py` runs them; `site/arvuta/index.js` draws a toolbar over each cell and applies
  the server's Markdown with the editor's `updateMarkdown` (only changed blocks).
- **Colours:** Muya highlights a code block with Prism when `fa([lang])` resolves the language to a
  loaded Prism language (`update()` in `@muyajs/core`, `ua.has(i)`). `arvuta` resolves to nothing,
  so cells are plain text. `tools/build-editor.mjs` already patches Muya's bundle at build time
  (each patch throws if its anchor is gone), and Muya sets `window.Prism`.
- Outputs today are one text fence; a DataFrame prints as `to_string()`; errors are text in it.
- Pictures are stored by content hash in `data/pildid` (`images.store`) and never deleted.

## Lead's calls

### Colours and keys

1. **`arvuta` and `arvuta-peida` highlight as Python** through one more build patch: Muya's
   language resolver (`fa`) maps both names to `python`, and the editor loads Prism's Python
   grammar at start. Nothing in the Markdown changes.
2. **Keys in a cell:** **Shift+Enter** runs the note up to this cell and puts the caret in the next
   cell, making a new empty cell when it was the last (as Jupyter); **Ctrl+Enter** runs and stays.
   Plain Enter is a new line as now.
3. **[n] numbers:** each cell's toolbar shows `[n]` (the run number in this page visit, as
   Jupyter's `In [n]`) and the time it took. They are not written into the note: a re-run of every
   cell would rewrite every number on every keystroke.

### Adding, moving, deleting

4. **Under each cell's output a thin row "+ Kood · + Tekst"** (shown on hover and when the caret is
   in the cell) inserts an empty ```` ```arvuta ```` cell or an empty paragraph there and puts the
   caret in it. The same row sits at the end of a note that has cells.
5. **↑ ↓ ✕ in the toolbar** move the cell (with its outputs) above the block before it or below the
   block after it, or delete it. Each is one edit, so Ctrl+Z undoes it, and co-editors see it.

### Outputs

6. **An output region** is what follows a cell, made of these blocks only, in this order:
   - ```` ```väljund ```` — printed text and the last line's value, as now;
   - ```` ```viga ```` — the error, shown red;
   - `*Tabel: <name> · N rida × M veergu*` followed by a Markdown table — a DataFrame shown as a
     real table (at most 50 rows; the caption says when rows were left out);
   - `![väljund](/pildid/<sha>.png)` — a plot.

   The app owns the region and rewrites it on every run. Tables inside a region are outputs, never
   inputs: `tabel()` and the Mouser link scan skip them.
7. **Plots:** the sandbox image gets matplotlib with the `Agg` backend and a font cache built into
   the image (the run's `/tmp` is emptied every time, so a cache there would be rebuilt on every
   run). After each cell the kernel saves every open figure as PNG (at most 4 per cell, 100 dpi,
   2 MB each) and closes it. The app re-encodes each PNG with Pillow before storing it (the bytes
   come from untrusted code) and stores it with `images.store` under the note, so share-link
   guests of the note see it too.
8. **Plot files do not pile up:** a plot is stored with author `arvutus`; a nightly job deletes
   `arvutus` pictures no live note shows and older than a day. Pictures uploaded by people are never
   touched.
9. **Long outputs fold:** a text output over 15 lines shows its first 15 and "Näita kõike (N rida)";
   folding is a view state, not written into the note.

### Automatic runs per note

10. **A switch in the cells' toolbar, "Automaatne arvutus: sees / väljas"**, sets the frontmatter
    key `arvutus: käsitsi` (off) or removes it (on), through a new `PATCH /api/notes/<slug>` field
    `arvutus_auto`. With it off, nothing runs until ▶, ⏩, Shift+Enter or Ctrl+Enter. The key goes
    to git with the note. The Mouser template keeps automatic runs.

## Open questions for the user

None block the start. Defaults the user can change:

| # | Default | Change if… |
|---|---|---|
| D1 | A DataFrame output shows at most 50 rows | you want more |
| D2 | Plots at 100 dpi, at most 4 per cell | you want sharper or more |
| D3 | The [n] numbers are not saved in the note | you want them in git too (every run then rewrites them) |
| D4 | The phone shows outputs and colours but no toolbars or "+" rows | you want to edit notebooks on the phone |

## Spike S9 (first, half a day)

- The `fa` patch: `arvuta` and `arvuta-peida` blocks highlight as Python in the editor, the share
  view (server render: `render.py` must map the class too) and the review window.
- Shift+Enter in a Muya code block: a capture-phase key handler stops Muya's own handling, and the
  caret can be put into another code block after `updateMarkdown`.
- matplotlib in the sandbox with a read-only root: import time with a prebuilt font cache, a PNG's
  size, memory under the 512 MB limit.
- Muya renders `*Tabel: …*` plus a table and `![väljund](…)` as ordinary blocks; `updateMarkdown`
  replaces a region of several blocks cleanly.

Findings go to `docs/spikes/arvutus.md` (section S9).

## Packages and steps

Each ends with its check.

1. **`wp/nb-outputs`** (sandbox, kernel, `app/arvuta`):
   - kernel: per-cell `{out, error, tables: [{name, header, rows, total}], images: [base64 png]}`;
     matplotlib capture; a DataFrame as the last value becomes a table output;
   - `cells.py`: output regions (parse, write, skip in `tabel()` and the Mouser scan);
   - `/api/arvuta`: PNGs re-encoded and stored; the region written;
   - the sandbox image with matplotlib and its font cache; the nightly clean-up job;
   - `PATCH` field `arvutus_auto`.
   → check: pytest for each output kind, a region rewritten in place, an output table that never
   becomes an input, a hostile PNG refused, the clean-up keeping uploaded and shown pictures; `pytest
   -m docker` for a plot from the real container.
2. **`wp/nb-editor`** (`tools/build-editor.mjs`, `site/arvuta/`, `site/css/arvuta.css`):
   colours, Shift+Enter / Ctrl+Enter, `[n]` and time, "+ Kood · + Tekst", ↑ ↓ ✕, the automatic
   switch, red errors, folding.
   → check: Playwright on chromium and firefox (mobile: colours and outputs only): highlighting
   tokens present, Shift+Enter runs and moves (and makes a cell at the end), + Kood puts the caret
   in a new cell, ↑ moves a cell with its output, Ctrl+Z undoes a delete, a plot appears, a
   DataFrame shows as a table, the switch stops automatic runs.
3. **Review, deploy.** A reviewer on both packages; a check that plot PNGs and output tables cannot
   carry markup into the note; full pytest and Playwright; `docker compose up -d --build`.
   → check on the live app: a notebook note with a plot and a DataFrame, Shift+Enter through it,
   and the Mouser order still filling itself.

Package 2 can start on the colours, keys and "+" rows while package 1 is built; the output
rendering waits for package 1's format.

## Risks

| Risk | Mitigation |
|---|---|
| Muya's update changes the resolver the patch hooks | the patch throws at build time when its anchor is gone, like the others |
| Re-running the whole note makes heavy notebooks slow | the automatic switch; 30 s on ▶; a persistent kernel is a later, separate decision |
| Plots fill the disk | author `arvutus`, nightly clean-up of unshown ones, 4 per cell, 2 MB each |
| A table or picture output smuggles markup into the note | cells escaped as now, captions built by the app, PNGs re-encoded; images only by content hash |
| Keyboard handling fights Muya | capture-phase handler only inside cell code blocks; spike S9 first |
