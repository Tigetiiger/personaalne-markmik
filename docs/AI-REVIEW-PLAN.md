# AI edits reviewed as a diff (2026-09-26)

**Problem.** When the user used the AI in a note, the Abiline chat's `lisa_markmesse` could only
append text at the end. The model appended a whole note with a second title under the one the user
had already written. Inline AI can only replace the selection or add below it, with a plain preview.

**Decisions (the user, 2026-09-26)**

| Question | Answer |
|---|---|
| Where the review appears | In a separate window (a modal over the page) |
| How the diff looks | Formatted: headings, lists and emphasis rendered; removed text red and struck through, added text green |
| Which AI edits go through it | All: the Abiline chat and inline AI (selection + Ctrl+J / ✦, the `@` menu "AI") |
| What the chat may change | The whole note, anywhere: change, delete, add. The user decides block by block |

**Lead's calls**

- The chat tool becomes `muuda_markmet(slug, markdown)`. The model returns the **whole new note**.
  The chat request sends the editor's current Markdown, and the model sees it as `<note>`, so the
  model edits the existing title instead of adding one. That Markdown is the diff's base. The
  system prompt says to keep what the user did not ask to change.
- **Diff units:**
  - A diff runs over top-level blocks: paragraphs, headings, code fences, tables, and each list
    item on its own.
  - A **change** is a run of adjacent changed blocks. Each change has "✕ Jäta ära" / "↺ Taasta".
  - A changed paragraph shows word-level del/ins inside it. The block falls back to "old block red,
    new block green" when the markers cannot be placed cleanly, for example across inline markup.
  - Long unchanged stretches collapse to "… N plokki muutmata …".
- **Rendering** goes through the server's `render.py` with nh3, the same sanitising as the rest of
  the app: one request for all blocks of a diff. No new client-side Markdown library.
- **Applying:**
  - "Aktsepteeri" builds the result from the base plus the kept changes. It applies them to the
    editor as ordinary edits (`replaceMarkdown`), so co-editors see them and Ctrl+Z undoes them.
  - If the note changed while the window was open, each kept change is applied where its base
    blocks are still found intact. A change whose blocks were edited meanwhile is skipped, and the
    user is told which.
  - "Loobu" closes the window with no change. Esc = Loobu.
- **Inline AI:** the popup still streams the answer. When it finishes, the review window opens with
  the diff of the whole note, with the selection replaced or text inserted at the caret. The
  choice between "Asenda" and "Lisa alla" stays in the popup.
- Chat card: "Muudatusettepanek märkmele" with "Vaata üle", which reopens the window until the
  proposal is accepted or dropped. The old append-only card goes.

## Checklist (package `wp/ai-review`)

- [ ] Pure diff module (block split, block diff, word diff, changes, apply kept changes, three-way
      apply onto a changed document). Unit tests over tricky cases: title edited, block moved,
      list item inserted, table cell changed, code fence, empty note, identical text, trailing
      whitespace only.
- [ ] `POST /api/render/blocks` (owner only, size cap): Markdown blocks → sanitised HTML. Pytest,
      including an XSS case.
- [ ] Review modal: formatted diff, per-change ✕/↺, Aktsepteeri / Loobu, keyboard (Esc, Enter on
      Aktsepteeri), focus trap, dark mode, phone width. Playwright.
- [ ] Chat: `muuda_markmet` replaces `lisa_markmesse` (same write cap, open note only). The editor's
      Markdown is sent with the message and used as `<note>`. Prompt updated. SSE `edit` event
      carries `{slug, base, markdown}`. Pytest with the fake runner. The fake returns the same
      title, and the diff must not show a second title.
- [ ] Inline AI goes through the modal (replace selection, insert below, `@` menu). Playwright.
- [ ] Playwright:
      - reject one of two changes, accept, and the note has only the other one;
      - a co-editor sees the accepted change;
      - Ctrl+Z undoes it;
      - the note changed during review: the untouched change applies and the conflicting one is
        reported;
      - Loobu leaves the note as it was.
- [ ] CONTRACTS.md updated (tool, SSE event, endpoint).
- [ ] Whole suite green before and after the merge. Then redeploy.
