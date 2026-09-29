# Status — built and deployed (2026-09-25)

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
- Spec fixes so all browser projects pass in one invocation (they share one test database).
- jobs.next_run takes the earliest slot in UTC (the spring-forward night skipped a 04:00 slot).

## Done on 2026-09-26

Merged and deployed: security fixes P1–P3 (docs/SECURITY-FIXES.md), the AI diff review
(docs/AI-REVIEW-PLAN.md), the "@" menu fix (text typed after picking Image or AI was never saved),
and the Riba design (docs/KUJUNDUS-PLAN.md, deployed at 18:06). `.env` holds
`OWNER_PASSWORD_HASH` and no plain password. The owner password is still the temporary one: the
user should change it in Seaded.

## Faster page loads (deployed 2026-09-26, about 22:55)

docs/PERF-PLAN.md. Live, Chromium at 85 ms added latency: top-bar pages 238–389 ms (before
0.8–1.0 s), a note 455–522 ms (before 1.1–1.8 s). Firefox: after the first page, no static file
goes over the network (before, every one was revalidated because of the session cookie).

## In flight

Aktsiad: stock-oracle moves into Märkmik (docs/AKTSIAD-PLAN.md). The foundation (schema,
contracts, pages and tabs, Seaded → Aktsiad, the Turuandmed card, runner options, the ntfy
service) is merged into `arendus` and deployed on 2026-09-29 (`docker compose up -d --build web
worker`; backup before it: `data/backups/markmik-pre-aktsiad-2026-09-29.db`). The new `ntfy`
service is **not started yet**: stock-oracle's ntfy still holds port 8090 and serves both apps.
Until the cutover, deploy with `docker compose up -d --build web worker` (a plain `up -d` would
fail on that port). At the cutover: stop stock-oracle's `ntfy`,
`mkdir -p data/ntfy && sudo chown 1000:1000 data/ntfy`, then `docker compose up -d ntfy`.

| Package | Worktree / branch |
|---|---|
| stocks-market (plan step 2) | markmik-wt/stocks-market, wp/stocks-market |
| stocks-portfolio (step 3) | markmik-wt/stocks-portfolio, wp/stocks-portfolio |
| stocks-sources (step 4) | markmik-wt/stocks-sources, wp/stocks-sources |
| stocks-import (step 7's script) | markmik-wt/stocks-import, wp/stocks-import |

Next: stocks-ai (step 5) after sources and market merge, then stocks-night (step 6).

## Open

- Deployed on 2026-09-25: `docker compose up -d` on http://100.122.97.7:8082. Owner login, the
  pages and the Claude connection test work. gbrain waits for the app's brain clone (needs the
  GitHub token). Redeploy after changes: `docker compose up -d --build`.
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
