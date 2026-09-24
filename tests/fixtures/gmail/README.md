# Gmail fixtures — SYNTHETIC

Invented responses in the exact JSON shape of the Gmail API v1 reference (see
[docs/spikes/gmail.md](../../../docs/spikes/gmail.md)). No mailbox was read to make them. Replace
them with anonymised real responses after spike S4.

| File | Call |
|---|---|
| `profile.json` | `GET /gmail/v1/users/me/profile`. Its `historyId` (`5820417`) is the stored cursor. |
| `history.json` | `GET /gmail/v1/users/me/history?startHistoryId=5820417&historyTypes=messageAdded`. Three `messagesAdded` records, one per message below; `historyId` `5821004` is the next cursor. |
| `history_404.json` | The error body for an expired `startHistoryId`, which should trigger a full resync. |
| `message_1_multipart_mixed_attachment.json` | `messages.get?format=full`: `multipart/mixed` → `multipart/alternative` (`0.0` text/plain, `0.1` text/html) + PDF attachment (`1`, body has `attachmentId` and no `data`). Carries the user label `Label_4829135` ("Kool"). |
| `message_2_ekool_html_only.json` | Single-part `text/html` at the top level (body data on the payload itself). The eKool sender address and the mail layout are guesses. |
| `message_3_multipart_alternative.json` | `multipart/alternative` with parts `0` and `1`. A cancellation mail. |

Details worth testing against:

- `body.data` is base64url **with** `=` padding here. The decoder should also accept it without
  padding.
- Decoded text parts use `\r\n` line endings.
- `snippet` is HTML-escaped (`&#39;`, `&quot;`) and cut short, so never use it as the body text.
- Header names are matched case-insensitively. `Date` has a `+0300` offset.
- `internalDate` is epoch milliseconds **as a string**.
- `Authentication-Results` carries `dmarc=pass header.from=gag.ee`, which the sender-trust check
  reads.
- The Message-IDs, DKIM tags, the `Received` line, and the IP from the RFC 5737 documentation
  range are all fake.
