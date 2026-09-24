# Spike S1: gbrain

2026-09-24, workstation VM. gbrain **0.54.1.0** from `github:garrytan/gbrain#6040075` (not the npm
package named `gbrain`). All data lived under `/tmp/gbrain-spike`, with a copy of `~/Projects/brain`
plus 20 sample notes under `notes/` and `notes/kool/`. `~/Projects/brain` and the Claude config were
not touched (`grep -c gbrain ~/.claude.json` → 0 after `init`).

## Verified

**Install.** Run `bun install -g github:garrytan/gbrain`. The binary is
`~/.cache/.bun/bin/gbrain` → `~/.cache/.bun/install/global/node_modules/gbrain/src/cli.ts`. It
lives under `~/.cache` because `XDG_CACHE_HOME` is set; it is not in `~/.bun/bin`. Bun blocked the
postinstall, which only runs `apply-migrations` on an existing brain, so a fresh `init` does not
need it.

**Data dir.** Set `GBRAIN_HOME=/x` and everything goes to `/x/.gbrain/` (`config.json`,
`brain.pglite`). Nothing was created in `~/.gbrain`. `init --path` exists too, but it sets only the
DB file.

**Pointing gbrain at a repo.** A plain `init --pglite --no-embedding` creates an *owned* content dir
(`.gbrain/content/<uuid>/default`). After that, `sync --repo <brain>` still used the owned dir and
failed with "not a git repository". `sources set-path default <brain>` was refused with
`writer_manifest_mismatch`. What works:
```
gbrain init --pglite --no-embedding --non-interactive --db-only
gbrain sources set-path default /tmp/gbrain-spike/brain
  Set source "default" local_path (was NULL) -> /tmp/gbrain-spike/brain
```
Adding a second source (`sources add brain --path …`) also works, but that source must be
federated, and it breaks legacy tokens (see Auth). Use the single `default` source.

**Slugs.** The slug is the repo-relative path without `.md`. It is slugified: lowercased,
diacritics folded, spaces turned into `-`. `notes/kool/Füüsika Töö.md` becomes
`notes/kool/fuusika-too`. The title comes from the frontmatter `title`.

**Link forms.** Each form sat in its own note with its own target in `notes/kool/`. The edges were
read with `graph-query <slug> --direction out`. `global_basename` means
`gbrain config set link_resolution.global_basename true` (off by default, stored in the DB config).

| Form in `notes/…` | Flag off | Flag on |
|---|---|---|
| `[[matemaatika]]` (target in another folder) | no edge | edge (`wikilink_basename`) |
| `[[eesti-keel\|Eesti keel]]` | no edge | edge (`wikilink_basename`) |
| `[[notes/kool/fyysika\|Füüsika]]` | edge (`mentions`) | edge |
| `[[notes/kool/keemia]]` | edge | edge |
| `[[kool/inglise-keel]]` (path suffix) | not run | edge |
| `[Ajalugu](ajalugu.md)` from `notes/kool/` (sibling) | edge | edge |
| `[Bioloogia](kool/bioloogia.md)` from `notes/` | **no edge**: `dir/x.md` resolves from the repo root | – |
| `[Geograafia](notes/kool/geograafia.md)` | edge | edge |
| `[Inglise keel](/m/inglise-keel)` (app form) | no edge | no edge |
| existing brain pages: `[[design-preferences]]`, `[[markmik]]` | no edge | edge |

```
Extract --stale: 6 link(s) … Skipped 17 candidate(s) whose target page doesn't exist   # flag off
Links: created 12 from 20 pages (db source)                                             # flag on
  notes/a-bare --wikilink_basename-> notes/kool/matemaatika
  notes/paevik --wikilink_basename-> pages/markmik
```
Incremental sync also resolves a bare `[[x]]` to a sibling or ancestor-folder page by walking up
the folders. `[[fyysika|Füüsika]]` from `notes/kool/fuusika-too` became a `mentions` edge that way.

**Moving a note.** `git mv notes/kool/ajalugu.md notes/ajalugu.md` was synced as `R1 renamed`.
The existing inbound edge from `d-mdlink-same` followed the page to `notes/ajalugu`, even though
that file's `ajalugu.md` link now points at nothing.

**`search --json`.** Returns an array of *chunk* hits, and a page can appear more than once:
```
{"slug":"notes/kool/matemaatika","page_id":5,"title":"Matemaatika","type":"note",
 "chunk_text":"# Matemaatika\n\nKooliaine Matemaatika. Kontrolltöö reedel…","chunk_source":"compiled_truth",
 "chunk_id":12,"chunk_index":0,"score":0.2515…,"stale":false,"source_id":"brain",
 "effective_date":"2026-09-24","keyword_hit":true,"base_score":…,"backlink_boost":…,"evidence":"weak_semantic",…}
```
Estonian handling is weak because full-text search uses the `english` config. `füüsika` gets
2 hits and `õpik` gets 8, but `fuusika`, `kontrolltoo` and `kontrolltööd` get 0: there is no
diacritic folding and no stemming.

**`sync` with a git repo.**
- The first sync is a full filesystem import, so it includes untracked files. It does *not*
  extract links ("20 page(s) have un-extracted edges — run 'gbrain extract --stale'").
- Later syncs are incremental. They diff from the checkpoint commit to HEAD and include
  **committed changes only**:
  ```
  [sync] 1 uncommitted file(s) are invisible to commit-driven sync (… 1 modified …). Commit them, or run 'gbrain sync --working-tree'
  Synced 109b5b39..226acc3b:  +1 added, ~1 modified, -1 soft-deleted (recoverable 72h), R1 renamed
    Extracted: 2 links, 0 timeline entries
  ```
- A deleted page is soft-deleted (`get` answers `page_not_found`, recoverable for 72 h). Sync
  runs `git pull` first unless you pass `--no-pull`; with no `origin` it skips the pull.

**HTTP server.** `gbrain serve --http --port 8083 --bind 127.0.0.1` listens only on
`127.0.0.1:8083` (checked with `ss`). It serves `/health`, `/mcp`, `/token` and `/admin`.
`/health` returns `{"status":"ok","version":"0.54.1.0","engine":"pglite"}`.
- **The server is stateless.** There is no `Mcp-Session-Id`, and `tools/call` works without
  `initialize`. Replies are SSE (`event: message` / `data: {…}`), and the tool payload is JSON
  inside `result.content[0].text`.
- A read+write token sees 97 tools; a read-only token sees 69.
- `sync_brain` is unavailable over HTTP: `{"error":"unknown_operation","message":"Unknown: sync_brain"}`.

**Auth.**
- No header: `401`, `WWW-Authenticate: Bearer error="invalid_token" … resource_metadata=…`.
- Legacy bearer: `gbrain auth create markmik-app --scopes read`. `whoami` shows
  `transport: legacy` and `expires_at: null`.
- OAuth client credentials: `gbrain auth register-client markmik-cc --grant-types
  client_credentials --scopes "read write" --source brain`, then
  `POST /token grant_type=client_credentials&client_id=…&client_secret=…`. It answers
  `{'token_type':'bearer','expires_in':3600,'scope':'read write'}`, and that token works on `/mcp`.
- A read-only token calling `put_page` gets `{"error":"insufficient_scope",…,"your_scopes":["read"]}`.
- **Gotcha:** a legacy token is bound to source `default`. With the notes in a second source
  `brain`, `search` and `get_page` worked, but `get_links`, `get_backlinks` and `traverse_graph`
  returned `[]`. The OAuth client registered with `--source brain` got the edges. With the single
  `default` source, the legacy token gets everything.
- `auth create` and `auth list` fail while serve holds the DB, so tokens must be made before serve
  starts.

**Python clients.**
- The official SDK (mcp **2.2.0**) connects with `create_mcp_http_client(headers=…)` plus
  `streamable_http_client(url, http_client=…)`. 2.x attribute names are snake_case
  (`server_info`), and the 1.x `streamablehttp_client(headers=)` is gone. It listed the tools and
  called `search`.
- `spikes/gbrain/client.py` uses plain httpx and a read-only bearer token:
```
$ GBRAIN_TOKEN=… uv run --with httpx spikes/gbrain/client.py kontrolltöö
[{"slug": "notes/kool/keemia", "title": "Keemia", "score": 0.2565…, "snippet": "# Keemia Kooliaine Keemia. Kontrolltöö reedel, õpik lk 42. …"}, …]
get_page: notes/kool/keemia | Keemia | note
backlinks: ['notes/c-path', 'notes/kool/fuusika-too']
```
- `recall {"query":"Füüsika"}` returned `results[]` (`slug`, `title`, `chunk`); `facts` was empty.

**Concurrency.**
- Two clients ran at once against one server: an SDK session making 60 calls and httpx with
  4 threads making 120 calls. Result: `A(sdk): 60 calls, 0 errors` and
  `B(httpx,4 threads): 120 calls ok, 0 errors`. A heavier later run had 360 calls and 0 errors.
- **CLI while serve holds PGLite.** Reads fail fast:
  `{"error":"pglite_busy","retryable":true,"reason":"live_serve",…}` from `search`, and "already
  open through `gbrain serve`" from `backlinks`.
- **`gbrain sync` delegates to serve over IPC** (`[sync] Delegating to the registered PGLite owner.`).
  - When idle it succeeded 4 of 4 times, in about 1.6 s each.
  - **Under sustained client load it failed 5 of 5 times** with `Error [write_pending]: The sync
    acknowledgment was lost` after about 3 s and applied nothing. The next idle sync caught up
    every skipped commit, because the cursor is durable.
  - `sync --watch` is rejected while serve runs:
    `Unsupported owner-delegated sync option: --watch`.

**Container (runtime, offline stand-in).** Docker Hub DNS was down, so I built a stand-in image
from local `python:3.13-slim`. It copied in the host's bun, the gbrain install and git, and ran
the real `spikes/gbrain/docker/entrypoint.sh` as uid 1000. It sat on an `internal: true` network
with the brain mounted `:ro`.
- The container was healthy about 30 s after the first start: init, full import and
  `extract --stale`. `app_token` was written with mode 0600.
- A second container on the internal network could search and read backlinks. No host port was
  published.
- A new commit showed up in search within 25 s through the 10 s sync loop, with its bare
  `[[keemia]]` edge.
- `stop` exited 0 at once (trap forwarded SIGTERM); a restart was healthy in 11 s, same token/data.

## Not verified

- **The `oven/bun` Dockerfile was never built.** Registry DNS was down: Tailscale lost its
  upstream resolvers, as in memory note `tailscale-dns-loses-upstream`. I did not restart
  tailscaled because that is outside this spike's scope. Also unchecked in that image: the global
  bin path under `BUN_INSTALL=/home/bun/.bun`, and whether its `bun` can run the healthcheck.
- `sync` against a real `origin`. Every run had no remote or used `--no-pull`.
- Items I only read in the code and did not run:
  - A frontmatter `slug:` that differs from the path makes the file be *rejected*
    (`src/core/import-file.ts`).
  - An ambiguous bare basename gets an edge to *every* matching page.
  - `[[kool/x]]` suffix resolution with the flag off.
- `client.py` with OAuth client-credentials tokens. The token exchange was tested with curl only.
- Whether soft-deleted pages disappear from `search`. Only `get` was checked.
- `GBRAIN_FTS_LANGUAGE=simple` with `reindex-search-vector` as a fix for Estonian search.
- Embeddings and `query` (the setup is keyless on purpose), large-brain performance, and PGLite
  behaviour after `kill -9`.
- The Agent SDK connecting to gbrain as an HTTP MCP server. That belongs to S2.

## Recommendations

**Corrections to PLAN-OVERVIEW.**
- Research finding 2 says "Edges come from `[[wikilinks]]` only". That is wrong. Markdown links
  also make edges, both repo-root `dir/x.md` and sibling `x.md`.
- Bare `[[slug]]`, which is how the brain pages are written today, makes **no** edge unless the
  flag is on.
- §11's "push → `gbrain sync`" cannot be called from the app. See the sync strategy below.

**1. Link format in files: `[[<slug>|<Pealkiri>]]`, with `link_resolution.global_basename = true`.**
- It still works when a note moves between folders. gbrain slugs are path-derived, but app slugs
  never change, so the file name stays `<slug>.md`.
- It matches the bare-wikilink style the laptop's brain pages already use, so those gain edges
  too.
- Obsidian resolves it the same way.

Rules for `linkfmt.py`:
- The file name is exactly `<app-slug>.md`, and the app slug is already slugify-clean. gbrain then
  maps the file to `notes/<folder>/<slug>`, and `slug.rsplit('/',1)[-1]` gives the app slug back.
- If another `.md` anywhere in the repo has the same basename, write
  `[[notes/<folder>/<slug>|Pealkiri]]` instead. That form works with the flag off, but it must be
  rewritten whenever the target moves.
- Never write `slug:` in frontmatter, and never put `/m/` links into files.
- Escape `]` and `|` in titles. This is not tested; WP-H should add table tests for it.

If the lead would rather not depend on a non-default flag, use the path form everywhere and
rewrite linking files when a note moves.

**2. How the app should call gbrain: HTTP MCP, stateless JSON-RPC over httpx (`client.py`), not
the CLI.**
- The CLI cannot open the DB while serve runs, and the app image should not carry bun and gbrain.
- Read tools: `search`, `get_page`, `get_backlinks`, `traverse_graph`.
- Use a 5–10 s timeout and treat any error as "gbrain pole kättesaadav".
- Avoid the mcp SDK in app code, because its 2.x API has already changed.
- The Agent SDK can register the same URL and bearer as an HTTP MCP server (S2 to confirm).
  Restrict the tools with `allowed_tools` there; a legacy token cannot pin a surface.
- gbrain search has no Estonian stemming or folding, so the app's own FTS5 stays the primary note
  search, and gbrain adds cross-brain results (for example `pages/`) and graph data.

**3. Auth setup.** Use one legacy bearer token with read scope. The app only writes through git,
and this token needs no refresh logic.
- The entrypoint creates it **before** `serve` starts:
  `gbrain auth create markmik-app --scopes read`.
- It writes the token to `data/gbrain/app_token` (0600). The app reads it through
  `GBRAIN_TOKEN_FILE`.
- To rotate: stop gbrain, `gbrain auth revoke markmik-app`, delete the file, start gbrain.
- OAuth client credentials also work, if expiring tokens or per-client scoping are wanted later.
  Register with `--source default`.

**4. Sync strategy.** The app never triggers a sync: `sync_brain` is unavailable over HTTP, and
the app container has no CLI.
- The gbrain container runs a loop of one-shot `gbrain sync --no-pull --no-embed` every 60 s.
  Each run delegates to serve over IPC.
- gbrain reads `data/brain` through a `:ro` mount and has no git credentials. The app remains the
  only process that pulls, commits and pushes.
- Only *commits* become visible, which fits the app's batched commits. The expected lag is at most
  one interval after the app commits.
- A `write_pending` failure under load is harmless: log it and let the next tick catch up. If
  heavy AI traffic ever starves the sync, lower the interval or have the app pause gbrain calls
  briefly.
- On first start, run a full import plus `gbrain extract --stale` before serve starts. The
  entrypoint already does this.

**5. Compose service.** The files are in `spikes/gbrain/docker/` (`Dockerfile`, `entrypoint.sh`,
`compose.yml`).
- Base image `oven/bun:1.4-debian` plus git. Install `github:garrytan/gbrain#<pinned sha>` as
  user `bun` (uid 1000).
- `GBRAIN_HOME=/data`; set `safe.directory=/brain` through `GIT_CONFIG_*`.
- Volumes: `./data/brain:/brain:ro` and `./data/gbrain:/data`.
- Serve on `0.0.0.0:8083` with `expose` only, on an `internal: true` network that the app also
  joins. The app uses `GBRAIN_URL=http://gbrain:8083/mcp`. No host port, no egress.
- `stop_grace_period: 30s` so PGLite closes cleanly. The healthcheck uses `bun -e fetch(/health)`.
- The entrypoint was tested for real, as described above. The `oven/bun` build itself still needs
  a run once DNS works: `sudo systemctl restart tailscaled` fixes it, per the memory note.
