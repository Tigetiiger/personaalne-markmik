# Märkmik

A self-hosted personal calendar and notes app. Notes open straight into a Markdown editor (Muya,
reused from the Seljakotirobot project), link to each other, sync as Markdown files into the brain
repo and are indexed by gbrain. The calendar is filled from eKool, Gmail and ICS feeds with Claude's
help. Notes can be shared by link for viewing or live co-editing. The UI is in Estonian.

The first page (`/`) is a chat with the assistant (since 2026-10-02, docs/KODU-PLAN.md): it reads
pictures (a note or summary out of a photo, dates, tasks), sets reminders that ntfy pushes to the
phone at their time, adds tasks and events, and proposes changes to any note, which are reviewed as
a diff before they apply. Reminders reach the phone only once the ntfy card in Ühendused has a
topic.

Notes can hold **Python cells** (docs/ARVUTUS-PLAN.md): a ```` ```arvuta ```` block runs in the
`arvutus` sandbox container (no network, no secrets), its output is written under it as in Jupyter,
`tabel("…")` gives a table of the note as a pandas DataFrame and writes changes back, and
`` `= x` `` shows a value in the text (`euro(x)` writes money as `0,40 €`); cells are coloured as
Python, Shift+Enter runs and moves on, "+ Kood · + Tekst" adds blocks, a DataFrame shows as a table
and a matplotlib plot as a picture (docs/JUPYTER-PLAN.md); ```` ```arvuta-peida ```` shows only the output. Notes in the
folder `Mallid` are templates ("Tee märge sellest mallist"). The Mouser order template
(`docker compose exec web python -m app.arvuta.mall`, once) fills part details from Mouser links
with the key on the Ühendused card "Mouser".

It also holds **Aktsiad** (since 2026-09-29, moved in from stock-oracle): a stock watchlist with
charts and trend, holdings with the Estonian investment account, news and filings, and a nightly
AI pass that scores the news, writes cited research and a would buy / hold / sell call. See
[docs/AKTSIAD-PLAN.md](docs/AKTSIAD-PLAN.md).

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
   `mkdir -p data/brain data/gbrain data/ntfy data/meedia && sudo chown -R 1000:1000 data`
3. `docker compose up -d --build`
4. Open the address, log in with that password and follow the "Alustame" wizard. The wizard
   connects eKool, Google, the brain repo, ntfy and ICS feeds; the same steps are in
   [docs/CONNECTING.md](docs/CONNECTING.md).

Services: `web` (gunicorn, one worker with 16 threads), `arvutus` (the sandbox for Python cells), `worker` (scheduled jobs: sources, AI
extraction, git sync, reminders, nightly backup, the stocks night at 01:30 and its daytime quotes,
filings and 07:00 digest), `gbrain` (search over the brain clone, on an internal network with no
outside access) and `ntfy` (push notifications on `100.122.97.7:8090`; phones subscribe there).
All state is in `data/`.

**Aktsiad** needs its keys on the Ühendused card "Turuandmed" (Finnhub, Alpha Vantage, SEC
User-Agent; prices come from Yahoo without a key) and its settings in Seaded → Aktsiad. The night
runs by itself; `python -m app.stocks.night --date YYYY-MM-DD` runs one by hand inside the worker
container. Stock alerts go to the ntfy topic set there (default `stock-oracle`).

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

## Meedia

`/meedia` stores pictures and videos with tags and search, and shares them by link on a host of
their own (docs/MEEDIA-PLAN.md).

- **The folder is the truth.** Every file sits flat in the media folder with a companion file
  beside it (`IMG_1.jpg` + `IMG_1.jpg.json`): tags, title, description, the text read from the
  picture, date, place, camera. The database is only an index;
  `docker compose exec worker python -m app.meedia.reindex` rebuilds it from the folder.
- **Where:** `./data/meedia`, or `MEDIA_HOST_DIR` in `.env` (mounted at `/meedia`). To move it to
  a bigger disk: stop web and worker, copy the folder, set `MEDIA_HOST_DIR`, start again.
- **Backup:** copy that one folder (`rsync -a`, restic). `.prügi/` inside it holds deleted files
  for 30 days; `.saabumas/` holds unfinished uploads and can be left out. Thumbnails are in
  `data/meedia-cache` and are rebuilt on demand, so they need no backup. Share links are in the
  database, which the nightly backup already covers.
- **Ways in:** upload on the page (also by dragging files onto it), copy files into the folder
  (the worker takes them in within a few minutes), or import a collection:
  copy it into the media folder's `.saabumas/kogu/`, then
  `docker compose exec worker python -m app.meedia.importer /meedia/.saabumas/kogu --move`
  (`--move` empties `kogu` of what was kept). Content already kept is skipped.
- **One format per kind:** HEIC pictures become JPEG, and every video becomes H.264 `.mp4` of at
  most 1080p (HDR tone-mapped). The original goes to `.prügi/`. Videos are converted by the worker
  in the background; the page shows them as "Töötlen" until then.
- **Automatic tags:** Haiku tags new files (up to 4 pictures, or 4 frames of a video, per call),
  at most `MEEDIA_DAILY_CAP` calls a day (default 100). These calls also count against
  `AI_DAILY_CAP`. Pictures go out shrunk and without EXIF, so the place stays here.
- **Share links** are served only on `MEEDIA_PUBLIC_URL`'s host, and that host serves nothing
  else. In Cloudflare, add a second Public Hostname to the same tunnel (e.g. `meedia`, HTTP
  `web:8000`) and set `MEEDIA_PUBLIC_URL=https://meedia.<domain>` in `.env`. A link's page has
  Open Graph tags, so Discord shows the picture or plays the video. A revoked link stops working
  within 5 minutes (`max-age`). Without "Luba originaali allalaadimine", a shared picture is shown
  only as its 1600 px preview; a video is always streamed whole.

## Documents

- [docs/PLAN-OVERVIEW.md](docs/PLAN-OVERVIEW.md): what the app does, architecture, decisions and
  risks.
- [docs/PLAN-IMPLEMENTATION.md](docs/PLAN-IMPLEMENTATION.md): the build and test plan in waves.
- [docs/CONTRACTS.md](docs/CONTRACTS.md): module, endpoint and settings interfaces.
- [docs/CONNECTING.md](docs/CONNECTING.md): connecting accounts (Estonian).
- [docs/SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md): security review and open items.
- [docs/spikes/](docs/spikes/): what was verified about gbrain, the Agent SDK, eKool and Gmail.
- [docs/STATUS.md](docs/STATUS.md): build status.
- [docs/MEEDIA-PLAN.md](docs/MEEDIA-PLAN.md): the picture and video store.

## Going public

The app can also be reached through a Cloudflare Tunnel, alongside the Tailscale address, which
keeps working (docs/TUNNEL-PLAN.md). There is no Cloudflare Access in front of it: the app's own
password and rate limits are the only gate on the owner pages.

The real client address comes from `CF-Connecting-IP`, and it is believed only from cloudflared's
fixed address. That is safe because Cloudflare's edge always overwrites a `CF-Connecting-IP` a
client sends. The Tailscale address is allowed by its IP (`100.122.97.7`) only: a Tailscale
MagicDNS name gets 400 from the Host allowlist.

1. Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared → name it (e.g.
   `markmik`) → save. Nothing is installed on the host: the `cloudflared` container runs it.
2. Copy the tunnel's token. Public Hostname: a subdomain of your choice, service **HTTP**
   `web:8000`.
3. Put `TUNNEL_TOKEN=…` and `PUBLIC_URL=https://<your hostname>` in `.env` (see
   `.env.example`), and either add `--profile tunnel` to `docker compose up -d` or set
   `COMPOSE_PROFILES=tunnel` in `.env` to start it by default.
4. `docker compose up -d --build`

With `PUBLIC_URL` set, share links (`/j/<token>`) and the private ICS feed address are always
built on it, so a copied link works for anyone, even when the owner is on the Tailscale address.
Only `PUBLIC_URL`'s host, the Tailscale address and localhost (tests) are served; any other `Host`
header gets 400.

Behind the tunnel every request's TCP peer is `cloudflared`, so the app trusts the real client
address (`CF-Connecting-IP`) and scheme (`X-Forwarded-Proto`) only from cloudflared's fixed
compose address (`TRUSTED_PROXY_NET`) — never from Tailscale, so nobody can spoof one. Rate limits
and the login lockout key on that real address either way. The session cookie gets `Secure`, and
responses get `Strict-Transport-Security`, only for a request that came through the tunnel; plain
HTTP Tailscale keeps working unchanged. See `docs/SECURITY-REVIEW.md` (N2, fixed).
