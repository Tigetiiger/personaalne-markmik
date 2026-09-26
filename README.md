# Märkmik

A self-hosted personal calendar and notes app. Notes open straight into a Markdown editor (Muya,
reused from the Seljakotirobot project), link to each other, sync as Markdown files into the brain
repo and are indexed by gbrain. The calendar is filled from eKool, Gmail and ICS feeds with Claude's
help. Notes can be shared by link for viewing or live co-editing. The UI is in Estonian.

## Run on the workstation

The app runs with `docker compose` and listens only on the Tailscale address
`http://100.122.97.7:8082`.

1. Copy `.env.example` to `.env` and fill in the four values (the comments say how to generate
   them). Never commit `.env`. The owner password goes in as a hash: run
   `uv run python -m app.hashpw` (it asks for the password twice, without echo, and prints only
   the hash) and paste the output between the single quotes of `OWNER_PASSWORD_HASH=''`.
   `OWNER_PASSWORD` with the plain password still works, but leaves it readable in `.env`; with
   both set, the hash wins.
2. Create the data folders as uid 1000 before the first start. Otherwise Docker creates them as
   root and the app cannot clone the brain repo:
   `mkdir -p data/brain data/gbrain && sudo chown -R 1000:1000 data`
3. `docker compose up -d --build`
4. Open the address, log in with that password and follow the "Alustame" wizard. The wizard
   connects eKool, Google, the brain repo, ntfy and ICS feeds; the same steps are in
   [docs/CONNECTING.md](docs/CONNECTING.md).

Services: `web` (gunicorn, one worker with 16 threads), `worker` (scheduled jobs: sources, AI
extraction, git sync, reminders, nightly backup) and `gbrain` (search over the brain clone, on an
internal network with no outside access). All state is in `data/`.

## Backup and restore

- Every night the worker writes `data/backups/markmik-YYYY-MM-DD.db` and keeps the newest 14.
- Notes are also in the brain repo under `notes/`; images under `notes/_pildid/`.
- Connected accounts are stored encrypted with `MARKMIK_KEY`. Keep a copy of `.env`: a restored
  database without the same key loses the stored secrets (reconnect the accounts).

To restore: `docker compose stop web worker`, copy the chosen backup to `data/markmik.db`, delete
`data/markmik.db-wal` and `data/markmik.db-shm`, then `docker compose start web worker`.

## Development

Node comes from fnm and is not on the PATH of non-interactive shells:
`export PATH="$HOME/.local/share/fnm/aliases/default/bin:$PATH"`.

```sh
uv sync
npm ci
npm run build            # Muya editor build, vendor bundles, then the app's bundle (site/dist)
uv run flask --app app run --port 8780   # with the .env variables exported
```

The browser loads the app's own JS and CSS from the content-hashed bundle in `site/dist`, not
from the sources: after changing `site/`, run `npm run build:app` (esbuild, well under a second).
Without `site/dist` the sources are served as they are.

Tests:

```sh
uv run pytest -q
VT_PORT=8780 npx playwright test --project=chromium   # also: firefox, mobile
```

Playwright uses the system Chromium (`/usr/bin/chromium`) and starts its own server with a
temporary database on `VT_PORT`. It tests the bundle: run `npm run build` first. Screenshot baselines are chromium only;
`npm run test:visual:update` refreshes them.

## Documents

- [docs/PLAN-OVERVIEW.md](docs/PLAN-OVERVIEW.md): what the app does, architecture, decisions and
  risks.
- [docs/PLAN-IMPLEMENTATION.md](docs/PLAN-IMPLEMENTATION.md): the build and test plan in waves.
- [docs/CONTRACTS.md](docs/CONTRACTS.md): module, endpoint and settings interfaces.
- [docs/CONNECTING.md](docs/CONNECTING.md): connecting accounts (Estonian).
- [docs/SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md): security review and open items.
- [docs/spikes/](docs/spikes/): what was verified about gbrain, the Agent SDK, eKool and Gmail.
- [docs/STATUS.md](docs/STATUS.md): build status.

## Public share links (later)

Share links work only on the tailnet for now. Going public means a `cloudflared` service with the
user's tunnel token and hostname. Before that, set `HTTPS_ONLY=1` and make the app trust the
tunnel's client address (see the open items in `docs/SECURITY-REVIEW.md`), or the login and share
rate limits see every visitor as one client.
