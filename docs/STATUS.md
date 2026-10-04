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

## Aktsiad: done and cut over (2026-09-29)

stock-oracle moved into Märkmik (docs/AKTSIAD-PLAN.md). All packages are merged and deployed:
foundation, stocks-market, stocks-portfolio, stocks-sources, stocks-ai, stocks-night, stocks-import.
Tests at the cutover: `uv run pytest` 2085 passed; Playwright chromium 197 passed; the four live AI
tests (`pytest -m live tests/stocks/ai/test_ai_live.py`) passed against real Claude.

Cutover, 2026-09-29 ~20:20:
- stock-oracle's worker stopped, a fresh read-only export imported with `--replace`
  (`data/import/oracle/`; backup before it: `data/backups/markmik-pre-cutover-2026-09-29.db`);
- stock-oracle's ntfy stopped and Märkmik's own `ntfy` service started on the same address and port;
- `docker compose up -d --build` (every service now; the `web worker` restriction is gone);
- the rest of stock-oracle stopped with `docker compose stop` (not `down -v`): its `pgdata` volume,
  `data/backups/*.dump` and the old vault stay as an archive. Deleting them is the user's call.
- Found at the cutover and fixed: the reminders' ntfy card never had a topic, so stock alerts now go
  to the VM's ntfy with the stocks topic even when that card is empty.

To check on 2026-09-30: the first night (01:30, AI from 02:00) in Aktsiad → Allikad, the 07:00
digest on the phone (topic `stock-oracle`), the notes under `aktsiad/` in the brain repo.
Laptop: remove stock-oracle's MCP entry there if it exists (`claude mcp remove oracle`).

## Kodu: the home chat on / (2026-10-02)

docs/KODU-PLAN.md, branch `wp/kodu`. The first page is a chat that reads pictures, sets reminders
(own table, one ntfy push at their time), adds tasks and events and proposes changes to any note,
reviewed as a diff. The phone app opens on `/`. Spike S5 (pictures through streaming input) is in
docs/spikes/agent-sdk.md.

Tests: `uv run pytest` 2121 passed before the last additions, then the touched folders (443)
again; the live AI tests (`pytest -m live tests/ai/test_live.py`, 5) passed three runs in a row;
Playwright for the home chat, shell, AI and calendar specs passed on chromium, firefox and mobile.
The full Playwright run also showed failures that `arendus` has too (Aktsiad chart specs on
firefox and mobile, `voistlused-otsija` on mobile) and `voistlused-otsija` on firefox, which passes
alone: it shares the Postkast with specs running beside it.

The user's steps: give the reminders' ntfy card a topic (Ühendused → ntfy) and subscribe the phone;
re-add Märkmik to the iPhone home screen so it opens on `/`.

## Arvutus: Python cells, templates, the Mouser order (2026-10-03)

docs/ARVUTUS-PLAN.md, branch `wp/arvutus`, merged and deployed. Notes run ```` ```arvuta ```` cells
in the `arvutus` container (no network, no secrets, a fresh process per run); results go into the
note. Notes in `Mallid/` are templates; the Mouser order template is `Mallid/Mouser tellimus`
(made with `python -m app.arvuta.mall`). Spikes: docs/spikes/arvutus.md.

Tests: `uv run pytest` 2289 passed before the last fixes (3 failures were lists that did not yet
name the new service and card; fixed and rerun); `pytest -m docker tests/arvuta/test_arv_sandbox.py`
10 passed against the container; Playwright for the cells, the editor, Ühendused, AI review, the
home chat, shell and share specs passed on chromium, firefox and mobile. Live: the sandbox answers
web, refuses the network and web itself, has no secrets in its environment and stops a busy loop.

The Mouser key is on the card since 2026-10-04 and the real API answers as app/mouser.py expects
(spike S8 in docs/spikes/arvutus.md). Links with a language in the path (`/en/ProductDetail/`) are
read since the same day.

**Hidden notes (2026-10-04):** "Peida nimekirjast" on a note sets the frontmatter key `peidetud:
true` (so it reaches git); the sidebar leaves such notes out until "Näita peidetuid" at its bottom.

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

## Meedia (built 2026-10-02, branch `wp/meedia`)

The picture and video store (docs/MEEDIA-PLAN.md): the media folder with companion files, intake
with HEIC → JPEG and video → H.264, chunked upload, import, Haiku tags, the `/meedia` page, and
share links on their own host. Not deployed yet. To go live, the user adds the public hostname
`meedia.tigetiiger.ee` (HTTP `web:8000`) to the tunnel and sets `MEEDIA_PUBLIC_URL` in `.env`.
Unverified: how Discord unfurls a real link, and conversion of real iPhone HEVC/HDR footage
(tested on ffmpeg-made samples only).

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
