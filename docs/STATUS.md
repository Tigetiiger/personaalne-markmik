# Status — paused 2026-09-24 16:49 (second pause; scheduled to resume about 20:50)

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

"Uncommitted" means the agent was stopped mid-work. The changes are still in the worktree (git
status).

**WP-A editor + collab** (`editor`)
- State: fixing review round 1. Last commit ccd4908; 7 files uncommitted. It was checking the
  style/id sanitising patch on a scratch build.
- Next: finish the list, report the build-editor patch text, then a verification review.

**WP-D calendar** (`calendar`)
- State: fixing verification round 2. Last commit d9dce8a; 6 files uncommitted. It was writing
  the regression tests for the all_day flip, the empty occurrence and bad legacy rows.
- Next: finish the list, then a verification review. D merges first.

**WP-E1 sources** (`sources`)
- State: approved (MERGE, 8b3e728).
- Next: merge right after D.

**WP-E2 Postkast + Ühendused** (`inbox`)
- State: approved (MERGE, a433293).
- Next: merge after D, because it needs the real events.update.

**WP-H git sync + gbrain** (`sync`)
- State: fixing verification round 4. Last commit 32c1165; 2 files uncommitted. It was checking
  that its new tests fail on the old code.
- Next: finish the list, then a verification review. After merge, add the gbrain compose block.

**WP-C app shell** (`shell`)
- State: building. Last commit 8a33f5a; 9 files uncommitted. It was writing its browser specs.
- Next: finish, then a review.

**WP-F AI assistant** (`ai`)
- State: building. Last commit 94b37c3 (backend done); 6 files uncommitted. It was on the
  browser side.
- Next: finish, then a review.

**WP-G sharing, WP-Q QA, deploy**
- State: not started. On the user's instruction, only finish the steps already in progress; do not
  start new ones.

**Merge order:** D → E1 → E2 → A → H → C → F.

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

### WP-D, verification 2 (probes in /tmp/wpd/v2/)

1. **HIGH** — Stacked `gzip, gzip` bypasses the 5 MB cap. Allow only one coding (gzip, deflate
   or identity), or inflate yourself with max_length.
2. **MED** — The deadline is not enforced during header or TLS trickle. Pass the deadline into
   `_CheckedBackend` and cap every read, write and start_tls.
3. Try every checked address in getaddrinfo order.
4. Flipping all_day on a series with exdates: convert or drop the exdates.
5. An empty `?occurrence=` returns 400.
6. Judge ::ffff:0:0:0/96 by its embedded IPv4.
7. Estonian 400s on bad rows, and no 500 when patching an all-day row with a NULL end_date.

Also raise the occurrence cap to 20000.

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

### WP-H, verification 4 (probes in /tmp/mkprobe/test_probe7.py)

1. **MEDIUM** — Refuse path parts that start with `.git` after stripping Cf characters and trailing
   dots, match `^\w{1,6}~\d`, or contain Cf characters.
2. `_give_up`: undo the new file when removing the old one fails.
3. On import, a frontmatter id wins over the recorded path owner (a crash between push and record
   swapped bodies).
5. Check ids with `isdecimal()`.

Optional:
- 4: abort the push rebase when incoming commits touch handed-over paths.
- 6: retry blocked notes so swaps settle.

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
