# Status — QA wave paused on the user's request (2026-09-25)

All feature packages A–I are merged. The QA wave is paused. Nothing is pushed.
- **QA2** (`~/Projects/markmik-wt/qa2`, wp/qa-ops, last commit f583bd3). Done: security review
  (docs/SECURITY-REVIEW.md), DST tests, Docker build + smoke. App fixes (worker `at=` jobs never
  ran, DST slot re-runs, open redirect, ICS repeated hour, password change ends other sessions,
  `uv --no-cache`) are waiting for a review, which was stopped mid-way (reviewer
  aa1fef2b8692f3c2d). Next: review, then merge.
- **QA1** (`~/Projects/markmik-wt/qa1`, wp/qa-browsers). Stopped mid-way (agent ad27a05a32112ddd1):
  chromium/firefox/mobile runs, flaky and vacuous tests, perf, small UI fixes. Next: resume it,
  then review and merge.
- **Wave 4** after QA: README, docs/CONNECTING.md, deploy on 8082.
- /tmp is a shared 5.9 GB tmpfs that fills up; use pytest `--basetemp` on disk if needed.

## Process the user asked for

- Every larger feature is built by a subagent in its own git worktree
  (`~/Projects/markmik-wt/<name>`, branch `wp/<name>`), following CONTRACTS.md.
- Before a merge, a **different** subagent reviews the branch. The builder fixes the findings, and
  another subagent verifies the fixes. Repeat until the verdict is MERGE.
- The lead runs the package's tests on its branch, merges into `arendus` one package at a time
  (`--no-ff`), then runs the whole suite on `arendus`:
  `uv run pytest -q` and
  `export PATH="$HOME/.local/share/fnm/aliases/default/bin:$PATH"; VT_PORT=8780 npx playwright test --project=chromium`.
- At most six agents run at once.
- New worktree: `~/Projects/markmik-wt/setup.sh <dir> wp/<name>`. It creates the worktree from
  `arendus`, symlinks `node_modules`, runs `uv sync` and builds. Before removing a worktree, delete
  its `node_modules` symlink.
- Nothing is pushed. The remote is `Tigetiiger/personaalne-markmik` (private) and the last push was
  the plans only.

## Merged into `arendus`

- Wave 0 foundation, spikes S1–S3, the etdates range fix.
- WP-B notes core, WP-I graph, WP-H git sync + gbrain (+ compose service), WP-D calendar.
- WP-E1 sources + extraction, WP-E2 Postkast + Ühendused.
- WP-A editor + live co-editing (6 review rounds).
- WP-C app shell (3 review rounds).
- Lead fixes: Muya build patches (style/id stripping, non-typing keys) and nested-JSON rejection.

Suite after the last merge (C): 978 pytest and 104 Playwright (chromium) passed.

## Work in flight (stopped mid-way)

**WP-F AI assistant** (`~/Projects/markmik-wt/ai`, branch wp/ai)
- State: approved by review (MERGE at cb3eebb). The lead then merged arendus in (f671339) and
  updated the shell-panes baselines (4259302). pytest passes (1044).
- Open: 2 chromium specs fail after the integration, `ai-inline.spec.mjs:154` (the `@` menu AI
  item, 45 s timeout) and `ai-panel.spec.mjs:59` (toBeVisible). The builder was stopped while
  fixing them; 4 files are uncommitted.
- Likely cause: the real shell's scoped editor wrapper or editor:ready context. Switch the Muya
  DOM lookup to `handle.muya`.
- Next: fix, run the full chromium suite, re-verify, then merge.

**WP-G sharing** (`~/Projects/markmik-wt/share`, branch wp/share)
- State: built (c8416c8). pytest 938 and share specs 3 passed on the branch. The branch was cut
  before WP-C merged.
- Next: a pre-merge review (stopped mid-way; it was probing render.py thread safety under
  gunicorn threads), then merge arendus in, fix, verify and merge. After merge, switch the Jaga
  spec from `/_test/share/<slug>` to `/m/<slug>`.

**fix/html-block-preview** (`~/Projects/markmik-wt/htmlfix`)
- The bug: an HTML block a co-editor TYPES stays "<Empty HTML Block>" in the other editor's
  preview (found by WP-G). Pasted blocks are fine.
- State: the WP-A builder was stopped while running the tests of its fix; 2 files uncommitted.
- Next: finish, review, merge.

**Then:**
1. The QA wave: full Playwright on chromium, firefox and mobile, the perf fixture, DST checks,
   /security-review, and docker compose build + up on 100.122.97.7:8082. See "Notes for the QA
   wave" below.
2. Wave 4: README, docs/CONNECTING.md (Estonian), brain page + memory, deploy.

The user allowed G, QA and Wave 4. No push without the user.

Agent IDs, resumable in the same Claude session:

| Agent | ID |
|---|---|
| F builder | aab14ab93fddf0b87 |
| A builder | a8facf7a58fb8a268 |
| C builder | a7228bf18dae20ac2 |
| G builder | a24f5fa05d4adc6ac |

## How to resume

**In the same Claude session**, send each paused agent a message such as "Jätka: continue where
you stopped; check git status first". The agent IDs are:

| WP | Agent ID |
|---|---|
| A | a8facf7a58fb8a268 |
| D | af8d14395a7b26044 |
| E2 | a44ea95f0bd7658f9 |
| H | ac2f279f370163f38 |
| C | a7228bf18dae20ac2 |
| F | aab14ab93fddf0b87 |

**In a new session**, spawn a fresh builder per package. Point it at:
- `~/.claude/CLAUDE.md` (tier workstation) and this file;
- its worktree, with an instruction to check `git status` and `git log`;
- CONTRACTS.md;
- its pending list above.

Then follow the process at the top.

## Still needed from the user

- eKool username + password, and whether two-factor login is on. The old API is gone and the new
  web route is unverified (docs/spikes/ekool.md).
- A Google Cloud OAuth "Desktop app" client published to Production (docs/spikes/gmail.md).
- A fine-grained GitHub token with write access to `Tigetiiger/brain` only.

## Notes for the QA wave (collected during reviews)

- `tests/visual/shell-palette.spec.mjs` lines ~71 and ~141 query /api/events for 2020–2030. The
  API caps ranges at 400 days and returns 400, so the "no event created" checks are vacuous. Use a
  valid range.
- The graph perf spec is sensitive to machine load; it failed once while other agents ran
  Playwright.
- ICS single events get the uid `ics:<id>:<UID>:None`: stable but ugly.
- `kt reedel 3.10` becomes Saturday 3.10 with no weekday-conflict warning in quick capture.
- The phone top bar is crowded ("Logi välja" wraps).
- With 2 notes the graph nodes render huge.
- A dateutil rule that can never occur, starting in year 1, still costs ~8–17 s per series in
  fetch_ics.
