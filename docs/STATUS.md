# Status — build done, deploy next (2026-09-25)

All feature packages (A–I) and both QA parts are merged into `arendus`. Nothing is pushed since
the plans. The remote is `Tigetiiger/personaalne-markmik` (private).

## Merged into `arendus`

- Wave 0 foundation, spikes S1–S3.
- WP-B notes core, WP-I graph, WP-H git sync + gbrain, WP-D calendar, WP-E1 sources + extraction,
  WP-E2 Postkast + Ühendused, WP-A editor + live co-editing, WP-C app shell, WP-F AI assistant,
  WP-G sharing, the html-block-preview fix.
- QA part 2: security review (docs/SECURITY-REVIEW.md), DST tests, Docker smoke, fixes.
- QA part 1: chromium, firefox and mobile runs, the perf fixture, UI fixes (docs/QA.md).
- Wave 4 docs: README.md, docs/CONNECTING.md (Estonian).
- jobs.next_run takes the earliest slot in UTC (the spring-forward night skipped a 04:00 slot).

## Open

- Deploy with `docker compose` on 100.122.97.7:8082 (README "Run on the workstation").
- Firefox: the OT fuzz (seeds 1 and 2026) fails inside Muya's `getSelection` after Enter at a
  block start; marked `test.fixme` on Firefox (docs/QA.md).
- `/api/today` costs as much as a month of events (~170 ms with 500 recurring series); probably it
  expands every series. Not profiled.
- Security open items in docs/SECURITY-REVIEW.md: rate limits by client address (a tunnel needs
  ProxyFix and `HTTPS_ONLY=1`), owner-set URLs can point anywhere, an edit guest can read a picture
  whose name it knows, the ICS token cannot be rotated, prompt injection through a guest-edited
  note.
- Smaller notes from reviews: ICS single events get the uid `ics:<id>:<UID>:None`; `kt reedel
  3.10` becomes Saturday 3.10 with no weekday-conflict warning in quick capture; a dateutil rule
  that never occurs, starting in year 1, still costs seconds in fetch_ics.

## Still needed from the user

- eKool username + password, and whether two-factor login is on. The old API is gone and the web
  route is unverified (docs/spikes/ekool.md).
- A Google Cloud OAuth "Desktop app" client published to Production (docs/CONNECTING.md).
- A fine-grained GitHub token with write access to `Tigetiiger/brain` only.
- Whether to push `arendus` to `Tigetiiger/personaalne-markmik`.

## Process used (for further work)

- Every larger feature is built by a subagent in its own worktree (`~/Projects/markmik-wt/<name>`,
  created by `~/Projects/markmik-wt/setup.sh <dir> wp/<name>`; delete its `node_modules` symlink
  before removing it), following docs/CONTRACTS.md.
- A different subagent reviews the branch before the merge; findings are fixed and verified.
- The lead runs the branch's tests, merges into `arendus` one branch at a time (`--no-ff`), then
  runs `uv run pytest -q` and
  `VT_PORT=8780 npx playwright test --project=chromium` (Node from fnm:
  `export PATH="$HOME/.local/share/fnm/aliases/default/bin:$PATH"`). Pipe test output only with
  `set -o pipefail`.
- At most six agents at once. `/tmp` is a shared 5.9 GB tmpfs; point pytest `--basetemp` at disk.
