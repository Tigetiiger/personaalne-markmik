# Status — paused 2026-09-24

The build was paused on the user's request. Everything needed to resume is here. Plans:
[PLAN-OVERVIEW.md](PLAN-OVERVIEW.md), [PLAN-IMPLEMENTATION.md](PLAN-IMPLEMENTATION.md),
contracts: [CONTRACTS.md](CONTRACTS.md). Spike reports: `docs/spikes/`.

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

- Wave 0 foundation.
- Spikes S1 (gbrain), S2 (Agent SDK) and S3 (eKool/Gmail groundwork + fixtures).
- WP-B notes core.
- The etdates range fix.
- WP-I graph.

Suite after the last merge: 301 pytest and 8 Playwright (chromium) passed.

## Work packages in flight

"Uncommitted" means the agent was stopped mid-work. Its changes are still in the worktree; run
`git -C <worktree> status`.

**WP-A editor + collab** (`editor`)
- State: fixing review round 1, stopped mid-way. Last commit ccd4908 "Collab and images: review
  fixes on the server". Uncommitted: 6 files.
- Next: finish the review-1 list below, then a verification review.
- Its report must include the style/id sanitising patch for `tools/build-editor.mjs`, which the lead
  applies.

**WP-D calendar** (`calendar`)
- State: fixing review round 1, stopped mid-way. It had merged `arendus` and was starting on
  `ics.py`. Uncommitted: 1 file.
- Next: finish the list below, then a verification review. D must merge before E1 and E2.

**WP-E2 Postkast + Ühendused** (`inbox`)
- State: fixing review round 1, only just started (merged `arendus`).
- Next: finish the list below, then a verification review. Merges after D.

**WP-H git sync + gbrain** (`sync`)
- State: fixing verification round 3 (458b1ec is the last reviewed commit). Not started.
- Next: finish the list below, then a verification review.
- After merge the lead adds the gbrain service block to `docker-compose.yml`; the block is in
  `gbrain/compose.gbrain.yml`, and `web` also joins network `brainnet`.

**WP-E1 sources + extraction** (`sources`)
- State: approved (verdict MERGE at 8b3e728).
- Next: merge right after WP-D. Two optional lows are left: `fetched_at` of a placeholder
  replaced by real mail, and the "kord päevas" wording.

**WP-C app shell** (`shell`)
- State: building, just started. The worktree has `wp/editor` merged (work in progress).
- Next: restart the build. The prompt is the WP-C section of PLAN-IMPLEMENTATION.md plus
  CONTRACTS §2/§5/§6. Port 8787.

**WP-F AI assistant** (`ai`)
- State: building, just started. The worktree has `wp/editor` merged.
- Next: restart the build. The prompt is the WP-F section of PLAN-IMPLEMENTATION.md plus
  CONTRACTS §4 AI runner (spike S2 findings), docs/spikes/agent-sdk.md, and
  spikes/agent-sdk/runner_sketch.py. Port 8789.

**WP-G sharing**
- State: not started.
- Next: start after A merges. Port 8791.

**WP-Q QA**
- State: not started.
- Next: after C, F and G. Port 8792.

**Merge order from here:** A → D → E1 → H → E2 → C → F → G → QA fixes. H is independent and may
go earlier once approved.

## Pending fix lists (as sent to the builders)

### WP-A, review 1 (probes in /tmp/wpa/)

1. **CRITICAL** — NaN/Infinity in ops is stored and echoed as invalid JSON, so the note can
   never open again. Parse JSON rejecting NaN/Infinity, `json.dumps(op, allow_nan=False)` → 400,
   ops must be lists, and catch OverflowError in `seen`/cursor.
2. **MAJOR** — Guest Markdown injects CSS into the owner's editor (inline `style`, `<STYLE>`,
   unclosed `<style>`). The builder prepares a `patchAll()` for build-editor.mjs (FORBID_TAGS
   style, drop style/id in the inline htmlTag renderer), verified on a scratch build with a spec,
   and the lead applies it.
3. **MAJOR** — Client ids are not bound to their sender, so a guest can leave the owner's client.
   Bind them to the Flask session, show opaque indices in cursors, and never replace a live session.
4. **MAJOR** — Unlimited client ids and cursors. Add caps, ignore view-only cursors, cap payloads,
   and rate-limit join/sync.
5. **MAJOR** — Rejoin retry storm. Fall back only for another slug, with one backoff timer.
6. **MAJOR** — pagehide never saves. Send a beacon/keepalive save when `seq > savedSeq`.
7. **MAJOR** — Reset silently drops local text in several windows. Keep `getMarkdown()`, rejoin,
   re-apply as ops and toast. Don't set `left` after a failed save.
8. **MAJOR** — detach()+openNote on the same element wipes the new editor. Use a per-element
   closing promise.

Minor:
- 10: picture refs only from well-formed ops.
- 11: SVG CSP and stripping.
- 12: guest upload quota.
- 13: nh3 allowed_classes.
- 14: 404 for foreign notes.
- 15: mark guests in participants.
- 16: prune stale sessions.
- 17: guest mode hides the gallery and "Loo uus".

Also: merge `arendus` first, and give fixture bodies an H1.

### WP-D, review 1 (probes in /tmp/wpd/)

1. **MAJOR** — `fetch_ics`: stream with a ~5 MB decoded cap and a ~60 s total deadline.
2. **MAJOR** — Bounded RRULE expansion (~5000) and text length caps.
3. **MAJOR** — `_validate` must reject rows the readers can't handle: bad exdates_json, multi-line
   rrule, series_id without recurrence_id. Normalise exdates and recurrence_id to the key form
   (UTC `…Z` / `YYYY-MM-DD`), make readers skip bad legacy rows, and report the key format for
   CONTRACTS.
4. **MAJOR** — The reminder key must include the start for non-recurring events (moved events get
   reminded again).

Minor:
- 5: type checks → 400.
- 6: pin the connection to the checked IP.
- 7: site-local and embedded IPv4.
- 8: stable uid without UID.
- 9: reminder grace window.
- 10: per-reminder errors.
- 11: ntfy summary without userinfo.
- 12: "only this / all occurrences" delete, and "Eemalda" on AI events.

### WP-E2, review 1

1. **MAJOR** — Confirm via `events.update(..., {"status": "kinnitatud"})`, drop the 409 path, and
   make confirm-all report its real count.

Minor:
- 2: Muuda keeps spans.
- 3: bare mask for passwords.
- 4: no query/fragment in ntfy/brain URLs.
- 5: autocomplete="new-password".
- 6: password ≤ 1024.
- 7: "Värskenda" (`run_now`) plus source health on the eKool, Google and ICS cards.
- 8: wizard survives a failed /api/connections.
- 9: strict all_day bool.
- Tests: guests get nothing, CSRF (incl. multipart), test routes absent, hostile text.

### WP-H, verification 3 (probes in /tmp/mkprobe/test_probe6.py)

1. **HIGH** — The claim step is outside per-file isolation (`id: .inf` → OverflowError → every
   pull fails). Make them per-file problems.
2. **MEDIUM** — The ownerless-file loop (foo.md re-imported as foo-3…). A path is kept only if its
   new owner writes it this run.
3. **MEDIUM** — Refuse `.gitmodules`/`gitmod~1`. Push failures must record export problems.

Low:
- 4: linkfmt alias must not contain `[`; anchors with brackets stay /m/.
- 5: don't unstage shared images.
- 6: keep a refused note's problem entry while it is busy.

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
