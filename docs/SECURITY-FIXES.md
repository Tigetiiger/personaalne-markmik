# Security fixes after the review (2026-09-26)

Fixes for the open findings of `docs/SECURITY-REVIEW.md`, with the user's decisions of 2026-09-26.
Process as in STATUS.md: a builder subagent per package in its own worktree, a different subagent
reviews, the lead runs the tests before and after each merge, merges one at a time, then redeploys.

## Decisions

| # | Finding | Decision |
|---|---|---|
| N2 | Rate limits keyed on the client address; a tunnel would share one bucket | **Not now.** Do it together with the tunnel (ProxyFix trusting only the tunnel, `HTTPS_ONLY=1`). |
| N3 | Owner-set URLs can point anywhere and receive tokens | Lead's call: `brain_remote` must be `https://github.com/<owner>/<repo>(.git)`. The ntfy URL stays free (the user's server is on the tailnet). |
| N4 | Prompt injection through a guest-edited note | **Warning only.** When the note has an active edit link, the model is told that guests wrote part of the text and must not follow instructions in it, and the Abiline panel shows a warning. Tools stay. |
| N5 | An edit guest can read any picture whose name it knows | **Only the note's own pictures:** those in the note when the link was made, those the owner uploads in that note later, and the guest's own uploads through that link. |
| N6 | The phone calendar address cannot be rotated | Lead's call: a "Uus aadress" button on the Telefoni kalender card, with a confirmation; the old address stops working. |
| N7 | `/login` has no CSRF check | Lead's call: fix it; the login form carries a token from a pre-login session. |
| N8 | A few GETs change state | Not changed: opening a share link must be a plain link, and creating today's daily note is harmless. |
| N9 | Docker hardening | Lead's call: one shared image for web and worker, `no-new-privileges`, `cap_drop: [ALL]`, read-only root with tmpfs where writes are needed. |
| — | Passwords stored as hashes | The owner password is already a scrypt hash in the database. `.env` gets `OWNER_PASSWORD_HASH` instead of the plain `OWNER_PASSWORD`; a command prints the hash. The eKool password and the tokens stay encrypted (Fernet), because the app must send them to their services. |

## Packages

**P1 `wp/sec-auth`**: N7 and the password hash.
- [ ] Login form carries a CSRF token; a POST to `/login` without it is refused (403). Tests.
- [ ] `bootstrap_owner` takes `OWNER_PASSWORD_HASH` (a werkzeug hash); `OWNER_PASSWORD` still
      works for development. With both set, the hash wins. A malformed hash is refused at start.
      Tests.
- [ ] `uv run python -m app.hashpw` reads the password without echo and prints the hash.
- [ ] `.env.example` and README describe the hash.

**P2 `wp/sec-share`**: N5 and N4.
- [ ] Schema: a table of pictures allowed per note (`note_images`), filled when a share link is made
      (the note's current pictures), on an owner upload in the note, and on a guest upload through
      that link. Existing databases get it through `CREATE TABLE IF NOT EXISTS`.
- [ ] A guest may read a picture only if it is in that table for the shared note. The owner is not
      restricted. Tests: a guest who adds `![](/pildid/<other>)` gets 404; its own upload and the
      note's pictures load.
- [ ] N4: the chat prompt says the note has an active edit link and that its text may contain
      instructions from guests that must not be followed; the panel shows a warning. Tests (fake
      runner).

**P3 `wp/sec-ops`**: N3, N6, N9.
- [ ] `brain_remote` refuses anything but `https://github.com/<owner>/<repo>(.git)`. Tests.
- [ ] "Uus aadress" on the phone card: confirm, new token, the old URL returns 404. Tests +
      Playwright.
- [ ] Compose: one image, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`,
      `read_only: true` for web and worker with tmpfs `/tmp`. Verify with `docker compose up` and a
      smoke run (login, a page, the Claude connection test).

## After the merges

- [ ] Whole suite on `arendus`: pytest; Playwright chromium, firefox and mobile.
- [ ] `.env`: replace `OWNER_PASSWORD` with `OWNER_PASSWORD_HASH` (the current database hash).
- [ ] Redeploy (`docker compose up -d --build`), smoke run.
- [ ] Update `docs/SECURITY-REVIEW.md` (fixed or decided per finding) and STATUS.md.
