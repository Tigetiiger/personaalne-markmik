# Security review — whole app (QA wave, 2026-09-25)

Scope: `arendus` with every package merged (branch `wp/qa-ops`). The packages were reviewed one by
one before merging; this pass looks at the seams: auth over every route, CSRF, CSP, secrets in
responses and logs, SSRF, guest (share link) paths, the AI tool surface and the worker's untrusted
input. The DST and scheduler findings from the same wave are listed here too, because they changed
app code.

Severity: **High** breaks a promise of the plan, **Medium** is worth fixing before going public,
**Low** needs an unlikely precondition or the owner's own action, **Info** is a note.

## Fixed in this branch (each with a regression test)

| # | Sev | Finding | Fix | Test |
|---|---|---|---|---|
| F1 | High | Jobs with `at=` never ran on a fresh database: `run_due` recomputed the due time from `now` on every tick until the first run, so at the slot itself it rolled on to the next day. The nightly backup (03:30), `extract-retry` (05:00) and `ekool-sync` never ran; `every=` jobs were fine. Plan §13 lists the nightly backup as the data-loss mitigation. | `app/worker.py`: store the computed due time on the first tick. | `tests/qa/test_qa_dst.py::test_a_0700_job_runs_at_0700_local_on_both_sides_of_the_change`, `::test_a_0330_job_runs_once_a_night_through_the_repeated_hour` |
| F2 | Low | `jobs.next_run` compared two Europe/Tallinn datetimes, which Python does by wall clock. A worker (re)started during the repeated hour on 25.10 (01:00–01:30Z) re-ran the 03:30 backup about once a second until 01:30Z, about 1200 times. | `app/jobs.py`: compare the candidate slot in UTC. | `::test_a_worker_starting_in_the_repeated_hour_does_not_rerun_a_0330_job` |
| F3 | Low | Open redirect after login: `/login?next=/%09/evil.example` redirected to `//evil.example`, because browsers drop tabs and newlines and read `\` as `/`. | `app/views/core.py` `_safe_next`: refuse control characters and backslashes. | `tests/qa/test_qa_security.py::test_login_never_redirects_off_the_site` |
| F4 | Low | ICS export: a time in the second pass of the repeated hour (25.10 01:00–01:59Z) went out as a local `TZID=Europe/Tallinn` time, which RFC 5545 §3.3.5 reads as the *first* pass. The phone showed such an event an hour early, and an event spanning the change ended before it began. | `app/ics.py` `_timed`: write those instants in UTC. Series keep local time, because their rule repeats it. | `tests/qa/test_qa_dst.py::test_a_25_10_event_in_the_feed_is_the_same_instant` |
| F5 | Medium | A password change did not end other sessions: sessions are signed cookies (30 days) with no server-side record. | `app/auth.py`: an `owner_epoch` setting, bumped by `set_password`, is stored in the session at login (`app/views/core.py`) and on a password change (`app/views/settings.py`); `load_actor` logs out a session whose epoch differs. | `tests/qa/test_qa_security.py::test_a_password_change_logs_out_every_other_session` |

## Open findings (not fixed here)

| # | Sev | Finding | Suggestion |
|---|---|---|---|
| N2 | Medium (when public) | Rate limits and the login lockout are keyed on `request.remote_addr`. On the tailnet this is the client address. Behind the planned `cloudflared` tunnel every request would come from the tunnel: every visitor would share one bucket, and anyone could lock the owner out for 15 minutes with 10 wrong passwords. | Before going public, add `ProxyFix` (trusting only the tunnel), set `HTTPS_ONLY=1`, and consider a lockout that a correct password still passes. |
| N3 | Low | Owner-set URLs can point anywhere. `brain_remote` accepts any https host and the GitHub token goes to that host as an `extraHeader`. The ntfy URL accepts any http/https host, including the tailnet and Docker networks, and gets the ntfy bearer token. `gbrain_url` has no HTTP setter (database only) and gets the gbrain token. All three are owner-only, so this is SSRF by the owner against themself. | Optionally pin `brain_remote` to `github.com`, or warn when its host changes. |
| N4 | Low | Prompt injection through a guest-edited note. When the owner opens the AI chat on a shared note, the model reads text the guest wrote. It can read other notes (`loe_markmet`, `otsi_markmeid`, `aju_otsing`), make up to 3 notes or events per turn, and propose text for the open note (`lisa_markmesse`). If the owner accepts that proposal, the guest sees it. WebSearch queries are a weak one-way channel. | The owner's review of a proposal is the control. Consider telling the model when the open note has an active edit link, or leaving `lisa_markmesse` out then. |
| N5 | Low | An edit guest can read any stored picture whose name it knows by adding `![](/pildid/<name>)` to the shared note (`images.referenced`). Names are the SHA-1 of the content: they are unguessable, but they are not secret (they appear in every note and git export that shows the picture), and a guest holding a picture can confirm the owner has it. | Accept, or check references against the note's text as it was when the link was created plus the guest's own uploads. **Fixed (2026-09-26, `wp/sec-share`):** a guest loads only pictures in `note_images` for its note: those the note showed at its first link (or at the first start with the table, for notes that already had links), the owner's uploads and gallery picks while the note is open, and guests' uploads through its links; anything else is 404. Residual: a picture reference the owner pastes in as Markdown text (or that comes in through git sync) stays hidden from guests until uploaded or picked from the gallery; and pictures a guest typed in before that first start were adopted with the rest. |
| N6 | Low | The private calendar feed `/ics/<token>.ics` cannot be rotated from the UI. A leaked phone URL stays valid until the settings row is edited by hand. | Add "Uus link" on the phone card (delete `ics_feed_token`; the next `feed_url` makes a new one). |
| N7 | Info | `/login` is exempt from CSRF (login CSRF). Without the owner's password nothing is gained. | — |
| N8 | Info | A few GETs change state: `/j/<token>` records the link's use and puts the share in the session, and `/tana` and `/api/daily/<date>` create that day's note. With `SameSite=Lax` a cross-site navigation can trigger them; the effect is harmless. | — |
| N9 | Info | Docker hardening, not needed for the smoke: the uv cache (398 MB) is baked into the image (now fixed with `uv sync --no-cache`: 1.6 GB → 1.05 GB); web and worker build two identical images (a shared `image:` name would build once); the containers could use `no-new-privileges`, `cap_drop: [ALL]` and a read-only root. | — |

## Checked and fine

- **Default deny.** `auth.enforce` runs before every view. `tests/share/test_share_access.py`
  walks `app.url_map`, so it covers all 74 routes (static included), with and without
  `MARKMIK_TEST_PAGES=1`. It checks each route against anonymous, view guest, edit guest, revoked
  link and expired link, and the owner passes every gate. Only the listed public routes (login,
  editor files, manifest, `/j/<token>`, the ICS feed) and the listed guest routes are open. Test
  routes are registered only with `MARKMIK_TEST_PAGES=1`, which neither compose nor
  `.env.example` sets.
- **CSRF.** `auth.check_csrf` runs on every non-GET request except `/login`, and accepts only the
  header, the form field (multipart included) or the JSON body field, all compared in constant
  time. The new `test_every_state_change_needs_the_csrf_token_even_from_the_owner` sends JSON,
  form and multipart requests to every state-changing route as the logged-in owner without the
  token: every one got 403. `sendBeacon` (collab leave/save) puts the token in its JSON body
  (`test_a_beacon_carries_its_token_in_the_json_body`).
- **CSP.** The strict policy is set on every response (`script-src 'self'`, no inline script). The
  only route that sets its own is `/pildid/<name>.svg`, and its policy is stricter
  (`sandbox; default-src 'none'`). `test_every_page_has_the_strict_csp_and_no_inline_script`
  renders every HTML route as the owner, plus both share pages with a note full of
  `<script>`/`onerror`, and checks the header and that no inline script or handler is present.
  `style-src 'unsafe-inline'` (needed by Muya and FullCalendar) allows CSS injection from a guest's
  HTML block, but `img/font/connect-src 'self'` block every outside fetch, so nothing can be sent
  out.
- **Secrets in responses and logs.** Gunicorn's access log is off, so the tokens in `/j/<token>`
  and `/ics/<token>.ics` are never logged. The worker sets httpx/httpcore logging to WARNING. git
  errors are scrubbed of the token and its Basic form (the token reaches git only through
  `GIT_CONFIG_*` env). ICS and source errors go through `safe_message`, never contain the URL, and
  network errors are logged without a traceback. `connections.test` results and `/api/connections`
  show masked values only. The docker smoke logs contained no `gbrain_…` token.
- **Guest paths.** `/j/<token>`: a 128-bit token stored as SHA-256, a rate limit, and one 404 page
  for unknown, revoked, expired and deleted links. The view page is rendered by nh3 with links to
  other notes on the site removed. `/api/share/current` and collab join/sync/save/leave are scoped
  to the share's note, with ops only on edit links. Uploads are limited to 1 file per request and
  50 MB per link. Pictures are served only when the note references them or this browser uploaded
  them, and guests get `no-store`. A revoked link is refused at the next request. The existing
  tests in `tests/share/` cover all of this.
- **ICS fetch (untrusted URL).** https only; every hop (redirects are followed by hand) connects to
  the address it checked, so a DNS rebind cannot switch it; private, loopback, link-local and
  100.64/10 addresses and single-label hosts are refused; caps on size (after gzip), occurrence
  count and time.
- **Email.** The extractor runs with `tools=[]`, and code validates the dates it returns.
- **Git pull from the laptop.** Only `notes/` is imported; `core.symlinks=false`; the host's git
  config, hooks and credential helpers are shut out (`GIT_CONFIG_GLOBAL=/dev/null`, `NOSYSTEM`);
  `--` comes before URLs; the remote must be https.
- **AI runner.** Built-ins are limited to `WebSearch` (no WebFetch, file or shell tools);
  `setting_sources=[]`; `strict_mcp_config`; the app secrets are blanked in the CLI environment;
  the working directory is an empty temp dir; model output is shown under the CSP, so markdown
  images pointing at outside hosts cannot load.
- **gbrain.** Runs on an internal network with no host port and no egress, and the app's token is
  read-only.
