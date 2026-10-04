# Spikes S6–S8: the arvutus sandbox, the editor, Mouser (2026-10-03)

For docs/ARVUTUS-PLAN.md.

## S6: the sandbox container

`sandbox/Dockerfile` (Python 3.13 slim, numpy 2.3.4, pandas 2.3.3, uid 1001) run as compose runs it:
`network_mode: none`, read-only root, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`,
`pids_limit: 64`, 768 MB, 1 CPU. A run (fresh `python -I kernel.py` with rlimits) takes **about
0.5 s** including numpy; that fits the 800 ms debounce, so no pre-forked process is needed.

| Attempt | Result |
|---|---|
| TCP to 1.1.1.1, DNS | `Network is unreachable`, name resolution fails |
| write outside /tmp | `Read-only file system` |
| the run's environment | PATH, HOME, TMPDIR, LANG and three tuning variables only |
| `/proc/1/environ`, `/proc/1/mem` (the server) | `Permission denied` (the server is non-dumpable) |
| `while True: pass` | killed by RLIMIT_CPU |
| `time.sleep(60)` | stopped by the wall-clock limit |
| 2 GB allocation | `MemoryError` (RLIMIT_AS) |
| fork bomb | stops at the pids limit; the next run works |
| a `setsid` daemon and a file in /tmp | both gone before the next run |

Found and fixed during the spike:

- **The host's `kernel.yama.ptrace_scope` is 0**: a process of the same user could read another's
  memory or open its pipes. The server sets `PR_SET_DUMPABLE 0`, runs one note at a time, and kills
  every process but itself and empties `/tmp` before and after each run.
- **Zombies:** as the container's PID 1 the server inherits every orphan; unreaped they filled the
  pids limit and no further run could start. It now reaps them.
- **A leftover process held the output pipe open**, so `communicate()` waited 120 s and blocked
  every other run. The server waits for the kernel process, then kills the rest, then reads.
- **Killing every process is only safe as PID 1**: outside the container (tests) it would take the
  developer's session with it. The server only does it when its pid is 1.

The attacks are `tests/arvuta/test_arv_sandbox.py` (`pytest -m docker`, `ARVUTUS_SOCKET` set).

## S7: the editor

- **Muya keeps only the first word of a fence's info string**: ```` ```python arvuta peida ````
  came back as ```` ```python ````. So the cell kinds are languages of their own: `arvuta` and
  `arvuta-peida`; plain `python` blocks never run. Muya's language list has no alias hook, so cells
  are not syntax-highlighted.
- **`replaceMarkdown` replaces the whole range between the first and the last changed block**, the
  block being typed in included. `updateMarkdown` (tools/editor/collab.js) matches equal top-level
  blocks as a longest common subsequence and removes and inserts only the others.
- Muya re-pads table cells; the inputs hash is taken over parsed cells, so padding never starts a run.

## S8: Mouser

- Product pages answer scripts with Akamai's "Access to this page has been denied".
- `POST https://api.mouser.com/api/v1/search/partnumber?apiKey=…` answers without a key with
  `{"Errors":[{"Code":"Invalid","PropertyName":"API Key"}]}`.
- **Checked with the user's key (2026-10-04):** `859-LTST-C190KRKT` came back as LITEON "Single
  Colour LEDs Red Clear 631nm", 448,117 in stock, 0,084 € a piece at 10. The field names, the price
  format ("0,084 €") and the currency (EUR for mouser.ee) are as `app/mouser.py` reads them.
- **Links can carry a language:** the user's first order pasted
  `https://www.mouser.ee/en/ProductDetail/859-LTST-C190KRKT`, which the first pattern did not
  take as a Mouser link (the row stayed empty without a warning). Fixed: an optional
  `/<lang>/` before `ProductDetail`, and a subdomain such as `eu.mouser.com`.
- **Before the key was there, these were unchecked:** the field names (`MouserPartNumber`, `PriceBreaks[].Price`,
  `AvailabilityInStock`, `Min`, `Mult`, `ProductDetailUrl`) are the ones Mouser documents and
  `app/mouser.py` reads; the price string's format and the currency for an Estonian account; the
  `qs` match for a link with several results. To do once the user's key is on the Mouser card.

## S9: notebook (2026-10-04, docs/JUPYTER-PLAN.md)

- **Colours:** Muya highlights a code block when its language resolver (`fa`) gives a loaded Prism
  language; its loader (`pa`) loads grammars by the block's own name. One build patch maps
  `arvuta` and `arvuta-peida` to `python` in both; the cells then carry Prism's tokens (keyword,
  string, comment...). The server's render (share view) highlights no code at all, so nothing
  changes there.
- **Block state:** Muya's state is one entry per top-level block (`{name, meta?, text?}`; a table
  has `children`), in the same order as `scrollPage`'s children, so cells and their output regions
  are found in the state and their DOM nodes by index.
- **The caret:** a code block's first content is its language field; `focusBlock` puts the caret
  in its last content, the code. The first version put it in the language field, and typing
  renamed the cell.
- **Keys:** a capture-phase keydown on `#editor` sees Shift+Enter and Ctrl+Enter before Muya.
- **matplotlib** in the sandbox: a run with a plot takes 1.3 s (0.5 s without); a simple PNG is
  about 20 kB. Its font cache is built into the image and copied into each run's scratch space
  (emptied after every run).

## S10: the cells' code editor (2026-10-04, docs/TAIENDUS-PLAN.md)

- **The `taiendus` container** (Jedi 0.20.0, pyflakes 4.0.2, the sandbox's numpy, pandas and
  matplotlib) is ready 7 s after start (the warm-up). Then: `t.sort_v` 0.11 s, `plt.ba` 0.87 s the
  first time matplotlib is analysed, `pd.read_c` 0.30 s, a signature 0.03 s, lint under 0.01 s;
  157 MB of memory. `python -I` leaves the script's folder off `sys.path`; the server adds it.
- **CodeMirror over a Muya block:** the block's code area stays in the page (hidden, its height set
  to the editor's) and the editor lies on it in a layer, so text above and the block's own size
  move it together; a ResizeObserver on the editor relays it. Typing goes in through
  `setCodeText` (one text edit, refused when the block changed), and a co-editor's change comes
  back three-way (`site/arvuta/yhenda.js`): theirs is placed around this editor's unsent change,
  or left to the next push when both touched the same place.
- **CodeMirror ignores Enter for 75 ms after its list opens** (`interactionDelay`): a test that
  presses it at once gets a new line instead of the suggestion.
- The CodeMirror bundle is 430 kB minified (143 kB gzipped), more than the plan's guess; it loads
  only on a desktop note page that has cells.
