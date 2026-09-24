# Spike S4 — Gmail (groundwork from Google's documentation, not yet run)

Written 2026-09-24 on branch `wp/spike-sources`. **No Google account or OAuth client was used.**
Everything below comes from Google's official documentation, read on 2026-09-24. The live part of
S4 (PLAN-IMPLEMENTATION.md, Wave 0.5) still has to run with the user's OAuth client and both
accounts. Its checklist is in §11.

Sources (all `developers.google.com` unless noted):

- [N] [OAuth 2.0 for iOS & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [W] [OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server) (for `access_type` and `prompt`)
- [O] [Using OAuth 2.0](https://developers.google.com/identity/protocols/oauth2) (refresh token expiration)
- [P] [users.getProfile](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile)
- [H] [users.history.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list)
- [S] [Synchronize clients with Gmail](https://developers.google.com/workspace/gmail/api/guides/sync)
- [G] [users.messages.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get), [Message / MessagePart](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages), [MessagePartBody](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments), [Format](https://developers.google.com/workspace/gmail/api/reference/rest/v1/Format)
- [SC] [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [U] [Unverified apps](https://support.google.com/cloud/answer/7454865) (support.google.com)
- [E18] [Access to unconfigured third-party apps for users designated as under 18](https://support.google.com/a/answer/13288950) (support.google.com)

## 1. Summary

- Flow: **Desktop app** OAuth client, authorization code + **PKCE (S256)**, loopback redirect
  `http://127.0.0.1:<port>/`. Nothing listens on that port, so the browser shows an error page.
  The user copies that address and pastes it into Märkmik, and the server swaps the code for tokens.
- The old "copy the code from Google's page" flow (OOB, `urn:ietf:wg:oauth:2.0:oob`) is **no longer
  supported** [N]. The loopback paste is the supported equivalent for a server without a public
  callback.
- Scope: `https://www.googleapis.com/auth/gmail.readonly`, a **restricted** scope [SC].
  - Publishing the consent screen to **In production** avoids the 7-day refresh-token expiry of
    "Testing" [O].
  - Without verification the user clicks through an "unverified app" warning. The unverified-app
    cap (100 new users) [U] is irrelevant for one user and two accounts.
- Sync: `users.getProfile` gives the starting `historyId`. Every 5 minutes, `history.list`
  (`historyTypes=messageAdded`) lists new message ids; each is fetched with `messages.get`
  (`format=full`). A **404** from `history.list` means the history id is too old: do a full
  resync [H][S].
- A school Workspace for Education account whose user is designated under 18 is **blocked from
  unconfigured third-party apps** unless the admin allows the app [E18]. Fallback: auto-forward
  school mail to the personal account (§10).

## 2. One-time Google Cloud setup (the user, guided by the Ühendused card)

1. Cloud Console → new project → enable **Gmail API**.
2. OAuth consent screen: user type **External**; add the scope `…/auth/gmail.readonly`; add both
   Google accounts as test users while setting up; then **Publish app → In production**.
   - Under Testing, refresh tokens for this scope expire after 7 days [O].
   - Production without verification means the unverified-app screen at consent [U]. That is
     acceptable for personal use.
3. Credentials → Create OAuth client ID → **Desktop app** → download the JSON.
   - Shape: `{"installed": {"client_id", "client_secret", "auth_uri", "token_uri",
     "redirect_uris": ["http://localhost"], …}}`.
   - It is uploaded on the Google card and stored encrypted (`google.client_json`).

## 3. Consent with the paste-the-redirect step

**Server side, "Lisa Google'i konto"**:

1. `code_verifier` = 64 random characters from `[A-Za-z0-9-._~]` (43–128 allowed) [N].
   `code_challenge = BASE64URL-NOPAD(SHA256(ASCII(code_verifier)))`, method `S256` [N].
2. `state` = 32 random bytes as URL-safe base64. `port` = a random free-looking port in
   49152–65535, used only as text; the server never listens on it.
3. Store `{state, code_verifier, redirect_uri, slot, created_at}` server-side (a row or the
   session). It expires after 15 minutes and is used once.
4. Show the link (line breaks added for reading):

```
https://accounts.google.com/o/oauth2/v2/auth
  ?client_id=<client_id>
  &redirect_uri=http%3A%2F%2F127.0.0.1%3A<port>%2F
  &response_type=code
  &scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly
  &code_challenge=<code_challenge>
  &code_challenge_method=S256
  &state=<state>
  &access_type=offline
  &prompt=consent
  &login_hint=<e-mail, optional>
```

- `client_id`, `redirect_uri`, `response_type=code` and `scope` are required. `code_challenge`,
  `code_challenge_method` and `state` are "Recommended". `login_hint` is optional [N].
- The loopback value is `http://127.0.0.1:port` or `http://[::1]:port`; the doc says to
  "substitute port with the actual port number" [N]. Desktop clients do not register loopback
  ports in the console.
- `access_type=offline` and `prompt=consent` are documented for the web-server flow [W]. The
  installed-app page does not list them, and says "refresh tokens are always returned for
  installed applications" [N]. They are sent anyway:
  - `access_type=offline` is harmless.
  - `prompt=consent` makes sure that **reconnecting an account issues a fresh refresh token**,
    instead of Google skipping consent for an already-authorised account [W].
- Incremental authorisation is not supported for installed apps [N], so request the one scope.
  To learn the account address, use `users.getProfile` rather than adding `openid email`.

**User side**: they open the link in their normal browser, choose the account, accept the
unverified-app warning and the consent screen. The browser then goes to
`http://127.0.0.1:<port>/?state=<state>&code=4/0A…&scope=https://www.googleapis.com/auth/gmail.readonly`
and shows "cannot connect". The user copies the whole address into the paste box.

**Server side, on paste**:

1. Parse the query string of the pasted text. Accept it with or without the `http://` prefix and
   ignore extra parameters (Google may add some, e.g. `scope`).
2. `error=access_denied` → show "Ligipääs keelati" [N].
3. `state` must match a stored, unexpired attempt (CSRF) [N]. Otherwise reject.
4. Take `code` and exchange it (§4). Delete the attempt row.
5. Implementation note (known library behaviour, not from Google's docs): `google-auth-oauthlib`'s
   `Flow.fetch_token(authorization_response=…)` goes through `oauthlib`, which refuses `http://`
   URLs unless `OAUTHLIB_INSECURE_TRANSPORT` is set. Parse the URL yourself and call
   `fetch_token(code=…)`, or post the form with `httpx` as in §4. Both are small.

## 4. Token exchange

```
POST https://oauth2.googleapis.com/token
Content-Type: application/x-www-form-urlencoded

client_id=<client_id>&client_secret=<client_secret>&code=<code>&code_verifier=<code_verifier>
&grant_type=authorization_code&redirect_uri=http://127.0.0.1:<port>/
```

- Fields as in [N] Step 5. `client_secret` is marked optional there, but the Desktop client JSON
  contains one. Send it (to be confirmed in S4 whether it is required).
- `redirect_uri` must be **exactly** the one in the auth URL, trailing slash included.
- Response fields [N]: `access_token`, `expires_in` (seconds), `refresh_token`, `scope`
  (space-separated), `token_type: "Bearer"`. `id_token` comes only with identity scopes, and
  `refresh_token_expires_in` only with time-based access.
  1. Check that `scope` contains `gmail.readonly`. The user can untick scopes on the granular
     consent screen [N].
  2. Call `users.getProfile` (§6) to learn which account this is.
  3. Store `{email, refresh_token, access_token, expires_at}` encrypted as
     `google.account.<email>`.
  4. If the e-mail is already connected, replace its tokens (reconnect). Do not add a duplicate.
- DPoP is optional [N]. Not used.

## 5. Refresh and revoke

```
POST https://oauth2.googleapis.com/token
Content-Type: application/x-www-form-urlencoded

client_id=<client_id>&client_secret=<client_secret>&grant_type=refresh_token&refresh_token=<refresh_token>
```

- The response has a new `access_token` and `expires_in`, and normally no new refresh token, so
  keep the old one [N]. Refresh when fewer than 60 s remain, or once after a 401.
- `invalid_grant` means the refresh token is dead [N][O]. Mark the account **Viga** "Ühenda
  uuesti" and stop polling it. Causes [O]:
  - the user revoked access;
  - the token was unused for 6 months;
  - **the user changed the password (Gmail scopes)**;
  - more than 100 refresh tokens exist per account per client (the oldest dies silently, so do
    not reconnect in a loop);
  - an admin restricted the scope (`admin_policy_enforced`);
  - Testing status (7 days).
- **Katkesta**: `POST https://oauth2.googleapis.com/revoke?token=<refresh_token>` (form content
  type), which returns 200 or 400 [N]. Then delete the stored tokens.

## 6. `users.getProfile`

`GET https://gmail.googleapis.com/gmail/v1/users/me/profile` with `Authorization: Bearer …` [P]
returns `{"emailAddress", "messagesTotal", "threadsTotal", "historyId"}`. `historyId` is a
**string**: the mailbox's current history record, used as the first sync cursor. Fixture:
`tests/fixtures/gmail/profile.json`.

## 7. Sync with `users.history.list`

`GET https://gmail.googleapis.com/gmail/v1/users/me/history?startHistoryId=<cursor>&historyTypes=messageAdded&maxResults=500[&labelId=<id>][&pageToken=…]` [H]

- The response is `{"history": [History], "nextPageToken"?, "historyId"}`. Each record is
  `{id, messages[], messagesAdded[{message}], messagesDeleted[], labelsAdded[], labelsRemoved[]}`,
  in increasing `historyId` order [H].
- Messages inside are sparse: "typically only have id and threadId" [H]. **Do not rely on
  `labelIds` from history.** They are present in the fixture, but may be absent.
- `maxResults` defaults to 100, maximum 500. `history` is missing entirely when nothing changed.
  "If you receive no nextPageToken in the response, there are no updates to retrieve and you can
  store the returned historyId" [H].
- `labelId` restricts to messages with that label. It is the cheap way to implement label mode (§9).

Algorithm, per account, every 5 minutes (worker job):

1. Page through `history.list` until there is no `nextPageToken`. Collect the ids from
   `messagesAdded[].message.id`, de-duplicated and kept in order.
2. For each id: prefilter (§9) and fetch `format=full`. Store as a `source_items` row with
   `external_id = <message id>`. This is idempotent, so a re-run is harmless.
3. **Only after every item is stored**, save the last page's `historyId` as the new cursor. A
   crash in between just re-reads the same range.
4. **HTTP 404** (body as in `tests/fixtures/gmail/history_404.json`): the `startHistoryId` is outside
   the retained range. History is "typically available for at least one week", sometimes only
   hours [H][S]. Then do a **full resync**:
   1. Take the new cursor from `getProfile.historyId` first.
   2. Call `messages.list` with `q=newer_than:7d` plus the prefilter as a Gmail search, e.g.
      `from:(gag.ee OR ekool.eu) newer_than:7d`, and page through it.
   3. Fetch and store each message. Idempotency by message id prevents duplicates.
   4. Record `last_error` = "ajalugu aegus, tehti täissünk" as info, not as an error state.
5. First connect uses the same backfill as the full resync, with `newer_than:14d`, so recent school
   mail is picked up once.
6. 401 → refresh once and retry. 403 `rateLimitExceeded`/429/5xx → exponential backoff, and the
   next run continues from the unchanged cursor.

## 8. `users.messages.get?format=full`

`GET https://gmail.googleapis.com/gmail/v1/users/me/messages/<id>?format=full` [G] returns a
**Message**:

- `id`, `threadId`, `labelIds[]`.
- `snippet`: short, **HTML-escaped**; do not use it as text.
- `historyId`.
- `internalDate`: epoch ms **as a string**. For normal SMTP mail it is the time Google accepted the
  message, "more reliable than the Date header".
- `sizeEstimate`.
- `payload` (MessagePart).

`raw` is only for `format=raw`. `metadata` returns headers without the body. `full` and `raw` are
not allowed with the `gmail.metadata` scope [G].

`MessagePart` = `{partId, mimeType, filename, headers[{name, value}], body{attachmentId?, size,
data?}, parts[]}`:

- The top-level part (`partId: ""`) carries the RFC 2822 headers: `From`, `To`, `Subject`, `Date`,
  `Message-ID`, …
- Container types (`multipart/*`) have `parts` and an empty body. Leaf parts have
  `body.data`, **base64url** [G].
- Attachments have a `filename` and a `body.attachmentId` instead of `data`. They are fetched
  separately via `messages.attachments.get`, and Märkmik skips them in v1.
- `partId` is hierarchical: `0`, `0.1`, `1`.

Body extraction (`gmail.py`):

1. Walk the tree depth-first. Skip parts with a non-empty `filename` or with `attachmentId`.
2. Prefer the first `text/plain` leaf. If there is none, take the first `text/html` leaf and turn
   it into text: sanitise with `nh3`, turn `<br>`/`</p>`/`</div>`/`</tr>` into line breaks, strip
   the other tags, unescape entities.
3. Also descend into `message/rfc822` parts, which is what forwarded mail uses.
4. Decode `body.data` with `base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))`, so that it
   works with or without padding.
5. The bytes are already free of the Content-Transfer-Encoding. Decode them with the part's
   `charset` from its `Content-Type` header (default UTF-8, `errors="replace"`).
6. Normalise `\r\n` to `\n`. Cut quoted reply history (`^>` lines, "… kirjutas:") only for the
   relevance check. The extractor sees the whole text, because case 20 of the eval set needs the
   new text and the quote kept apart.
7. Headers: look names up case-insensitively. If a value still contains an RFC 2047 encoded word
   (`=?utf-8?…?=`), decode it with `email.header` as a precaution.
8. **Reference date for relative expressions** ("homme"): the `Date` header, parsed with
   `email.utils.parsedate_to_datetime` and converted to Europe/Tallinn. Fall back to
   `internalDate` when the header is missing or unparseable, or more than 2 days away from
   `internalDate`.

Fixtures: `tests/fixtures/gmail/message_1_multipart_mixed_attachment.json` (mixed → alternative
plus a PDF), `message_2_ekool_html_only.json` (single-part HTML), and
`message_3_multipart_alternative.json`.

## 9. Prefilter design (before any AI call)

Configured per account on the Google card. The default comes from the plan: label **or** sender
list.

1. **Label mode** (recommended for the personal account): the user makes a Gmail filter
   `from:(gag.ee OR ekool.eu) → apply label "Kool"`. Märkmik stores that label's id (`Label_…`
   from `users.labels.list`) and passes it as `labelId` to `history.list`, so only labelled mail is
   listed at all. The backfill uses `label:Kool`.
2. **Sender mode**: glob patterns on the address from `From` (`email.utils.parseaddr`, lowercased):
   - default `*@gag.ee`, `*@ekool.eu` (the eKool notification sender is **a guess**; take the real
     address from the user's mailbox in S4), plus any the user adds;
   - list-level `*@*.gag.ee` only if S4 shows subdomains.
3. **Always skip** messages whose labels include `SPAM`, `TRASH`, `DRAFT`, `SENT` or `CHATS`, and
   by default `CATEGORY_PROMOTIONS`. Take the labels from `messages.get`, not from history.
   In sender mode, fetch `format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date&metadataHeaders=List-Id&metadataHeaders=List-Unsubscribe`
   first, and only fetch `full` for matches. Bodies of unrelated mail are then never downloaded
   or stored.
4. **Sender trust** (against spoofing and look-alike domains, eval case 12):
   - An allow-listed domain only counts if the top-level `Authentication-Results` header shows
     `dmarc=pass` with `header.from=<that domain>`.
   - Otherwise the mail may still be extracted, but its events can **only** be `pakutud`
     (Postkast), never auto-confirmed.
5. **Bulk hint**: a `List-Unsubscribe` or `List-Id` header, or `Precedence: bulk`, is passed to the
   haiku relevance step as a hint ("likely newsletter"). It is not a hard drop, because school
   newsletters can announce events.
6. **Cross-account dedupe**: the `Message-ID` header is the key when the same mail arrives in both
   accounts, for example with forwarding (§10). The second copy is stored as a `source_items` row
   but skipped for extraction.

The prefilter is plain code with unit tests on the fixtures. The AI steps start only after it.

## 10. School account (under 18) and the forwarding fallback

- In Google Workspace for Education, users designated under 18 are **blocked from third-party
  apps that the admin has not configured**. They see a block message, possibly with a "request
  access" option [E18].
- Admins can also restrict scopes, which gives `admin_policy_enforced` at consent [N].
- Expect the school account to fail in S4 unless GAG's admin marks the OAuth client as trusted.
- Fallback: in the school Gmail, Settings → Forwarding → forward to the personal address (Gmail
  sends a confirmation code to the target), then keep a copy.
  - Auto-forwarded mail keeps the original `From`, so the sender rules still work.
  - `Authentication-Results` on the personal side may then fail DMARC for the original domain, so
    forwarded school mail will only reach Postkast, not auto-confirm. Decide in S4 whether to trust
    `X-Forwarded-For`/`Delivered-To` from the user's own school address.
  - The admin may also have disabled automatic forwarding. The last resort is a manual Gmail
    filter or forwarding by hand.

## 11. S4 checklist (with the user present)

1. The user creates the project and client per §2, and the JSON goes into `data/` (gitignored).
2. A one-off script in `/tmp` runs §3–§4 for the personal account. Record:
   - whether `client_secret` is required;
   - the exact redirect URL Google produced (which params);
   - the token response keys (values redacted).
3. `getProfile`. Then send a test mail to that account from the user's school address and wait.
   Then `history.list` from the stored `historyId`. Then `messages.get` in both `full` and
   `metadata` formats. Record whether history records carry `labelIds` and whether bodies carry
   `=` padding.
4. Force a 404: call `history.list` with `startHistoryId=1` and record the status and body.
5. Try the school account. Record the exact block screen or error code. If blocked, set up
   forwarding and repeat step 3 on the forwarded copy, noting `From` and `Authentication-Results`.
6. Find the real eKool notification sender and the real school domains in the user's mail. Update
   §9 defaults.
7. Anonymise the captured JSON into `tests/fixtures/gmail/`, replacing the synthetic files, and
   update this file with what was verified.
