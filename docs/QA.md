# QA wave, part 1: browsers, test quality, performance

Branch `wp/qa-browsers`, 2026-09-25, on the workstation VM (`VT_PORT=8792`).

## Results

| Run | Result |
|---|---|
| pytest (whole suite) | 1082 passed, 1 skipped (the perf fixture), 4 deselected (`live`) |
| Playwright chromium | 124 passed |
| Playwright firefox | 117 passed, 7 skipped |
| Playwright mobile (Pixel 7, 390×844, chromium) | 101 passed, 23 skipped |
| Perf fixture (`MARKMIK_PERF=1`) | 1 passed, numbers below |

Skipped on purpose: screenshot asserts off desktop chromium (baselines exist only there);
`shell-note`, `shell-panes` and `ai-panel` on mobile (desktop layouts; `shell-phone` and
`ai-shell` cover the phone); CDP listener counts off Chromium. `test.fixme`: the OT fuzz on Firefox
(see below). Firefox ran before the last `editor-collab` change (a wait, test-only); it passed
that test with the old wait.

## App fixes (each with a regression test)

- **Phone: pages with both panes closed rendered nothing.** `.app.no-left.no-right` in
  style.css outranks style.css's own phone rule, so the page landed in a 0-wide first grid column:
  the graph page on every phone, and the note page after both panes were closed on the desktop.
  Fixed in `site/css/shell.css`; test `shell-phone` "with both panes closed on the desktop".
- **Phone top bar**: "Logi välja" wrapped and the theme button was squashed. Only the page links
  shrink now (they scroll sideways); below 520 px the brand shows its mark only. `site/css/shell.css`;
  test `shell-phone` "the top bar fits one line".
- **Graph**: with two notes, fitting to the view zoomed far in (huge nodes). The fit is now capped
  at zoom 4. `site/graph/index.js`; test `graph-page` "a vault of two notes".
- **`scoped()` in `site/shell/note.js`** wrapped every handle property as a function, so
  `ctx.editor.muya` was a function, not the Muya instance. Non-function properties now pass
  through as live getters. Test: `shell-note` "other packages' meta actions and panels".

## Test fixes

- `shell-palette`: the "nothing captured" checks queried 2020–2030, which the API refuses (400),
  so they compared `undefined` to `undefined`. They now count over today ±200 days and assert 200.
- `inbox-tere`: the fakes are removed before loading `/`, not in the middle of that load.
- `shell-panes`: the Postkast count is hidden in the screenshots. `calendar-theme`: colours are
  still checked everywhere; screenshots only on desktop chromium.
- `calendar-edit`: clicks the month cell, not its frame (Firefox's frame is only as tall as its
  content). It also removes its own events first, so a second project run on the same database
  does not find two "Liigutatav".
- `share-links`: Firefox gives a script-made `ClipboardEvent` an empty `clipboardData` of its own,
  so the test sets it with `defineProperty`.
- `editor-collab` (big note): Firefox's `sendBeacon` carries more than 64 KB, so there is no kept
  copy to restore there. The saved text is checked in both browsers.
- `shell-helpers`: Firefox reports "error loading dynamically imported module" when a navigation
  cuts a lazy import short (Muya's prism languages). That one message, within 2 s of a navigation,
  is ignored.
- `editor-collab` (final save refused): a fixed 1.5 s wait for B's op lost the race once on a
  loaded VM; the test now waits until B's text is stored. Passed 4 of 4 with `--repeat-each=4`.
- Mobile: `ai-shell` opens and closes the bottom sheet; `shell-today` opens the drawer; the graph
  canvas takes the phone's full width.

## Performance (tests/perf/test_perf.py)

1000 notes (4 links and 3 dated tasks each), 5000 events (500 recurring: daily, weekly, monthly,
yearly). Median of 5 requests through the Flask test client:

| Endpoint | ms | items |
|---|---|---|
| /api/notes/tree | 7.8 | 1000 |
| /api/search (one word) | 4.9 | 20 |
| /api/search (two words) | 5.4 | 20 |
| /api/graph | 18.4 | 1000 nodes |
| /api/events, one month | 166.5 | 3095 |
| /api/today | 169.3 | 6 |

All are under the 500 ms limit. /api/today (6 items) costs as much as a whole month of events;
probably the recurring series are expanded for it (not profiled). Worth a look if series grow.

## Known issues left open

- **Firefox + Muya with three editors on one page** (`editor-fuzz`, seeds 1 and 2026): Muya's
  `getSelection` throws "c is undefined" right after an Enter at a block start. Only the fuzz
  harness puts three editors in one document; one editor on the note page did not reproduce it.
  Marked `test.fixme` on Firefox.
- `/tmp` (a 5.9 GB tmpfs) was full at the start of this wave, filled by other agents' leftovers
  (`/tmp/wpg-review`, `/tmp/mkprobe`, `/tmp/ekool-src`, `/tmp/gbrain-spike`, old
  `/tmp/pytest-of-dev` runs). pytest failed with an sqlite OperationalError until `TMPDIR` pointed
  elsewhere. Nothing was deleted.
- From STATUS, not in this wave's scope: ICS single-event uids end in `:None`; `kt reedel 3.10`
  gives no weekday-conflict warning; a never-occurring dateutil rule from year 1 costs seconds in
  fetch_ics.
