# Notes: gaps and additions (plan, 2026-10-05)

**The user's request:** review the notes part, fix what is missing and add what would be useful. The
review found three gaps and a list of smaller improvements and new features; the user took all of
them.

## What the review found

- **A note cannot be deleted in the UI.** `DELETE /api/notes/<slug>` exists (soft delete,
  `deleted_at`), but no page calls it, and nothing lists or restores deleted notes.
- **New notes keep a meaningless slug.** "+ Uus märge" creates the note with an empty title, so
  `util.slugify("")` gives `marge`, `marge-2`, … and the slug is fixed at creation. The live
  database has four (`marge` is "Gustav Adolfi Gümnaasiumi ajalugu…"); their files in the brain repo
  are `notes/marge*.md`, and so are their gbrain pages.
- **Abandoned new notes stay.** `marge-4` is a bare `# ` line, synced to git, and cannot be removed.
- Smaller: tags and folders are typed into a bare input (a typo makes a new tag or folder); folders
  cannot be renamed; a link's `#heading` part is dropped (`onOpenNote(slug)`); search has no
  filters; tasks without a due date show nowhere but in their note; the daily note is only
  `# <date>`; the Seotud panel relies on gbrain, whose English-configured search misses Estonian.

## The user's answers (2026-10-05)

| Question | Answer |
|---|---|
| Slug | **Once:** an automatic slug follows the title the first time the note gets one, then stays; the four existing `marge*` notes are renamed too (nothing links to them and none is shared) |
| Trash and git | **Removed at once:** a deleted note's file leaves the brain repo as now (git history keeps it); a restore writes it back; after **30 days** in the trash the note is gone for good |
| History | **From the brain repo's git:** no new storage, and edits made on the laptop show too |
| Tasks | **Own page `/ulesanded`** in the top bar and the palette |
| Link text after a rename | **Offered:** a notice "3 märget lingivad vana pealkirjaga — uuenda?" opens the diff review |
| Seotud | **Local part on top:** "Sarnased märkmed" from the app's own FTS; gbrain's pages stay below |
| Export | **A note as `.md`** only |
| Empty new note | **Deleted on leaving**, for good (it never reaches git or the trash) |

## Lead's calls

### Deleting and the trash

1. **"Kustuta"** sits in the note's metadata row next to "Peida nimekirjast", with a small
   "⋯" menu holding "Laadi alla .md" and "Ajalugu". No confirmation: the note goes to the trash
   and the toast offers **"Võta tagasi"**. `toast()` gains an optional action button for this.
2. **The trash** is a section at the bottom of the sidebar, "Prügikast (n)", shown when n > 0. It
   opens a dialog: title, folder, deleted when, days left, **Taasta** and **Kustuta jäädavalt**.
3. **Restore** (`notes.restore`): `deleted_at` cleared, then `on_saved` rebuilds links and tasks,
   and links elsewhere waiting for the slug find it again. A daily note gets its `daily_date` back
   from its slug unless that day has a new daily note by then (it then stays an ordinary note). The
   export writes the file again (it has no `file_path` after the delete). Share links work again
   (`_valid_share` already joins on `deleted_at`).
4. **Purge** (`notes.purge`, worker job daily): notes deleted more than 30 days ago are deleted from
   the table, but only once their file is gone from git (`file_path IS NULL`), so the export never
   loses track of a file. Foreign keys do the rest: links, tasks, shares, pictures and files rows,
   collab sessions and the note's AI thread cascade; events and reminders keep their row with
   `note_id` NULL. Pictures and data files themselves are cleaned by their own night jobs.

### New notes

5. **Automatic slug once.** A table `note_auto_slug (note_id PRIMARY KEY)` marks notes created
   without a title (`schema.sql` is applied with `CREATE … IF NOT EXISTS`, so a new table needs no
   migration; a new column would). When such a note is **left** (below) and its derived title is
   real, `notes.rename_slug` gives it `slugify(title)` and drops the mark; a later title change
   never moves it again. The old slug goes into `old_slugs (slug PRIMARY KEY, note_id)`, so a
   bookmark, the back button or a link typed meanwhile still opens the note: `by_slug` falls back to
   it, `/m/<old>` redirects, and the note page replaces the address. `links` rows whose `dst_slug`
   was the old one are rewritten in their bodies the same way "Seo" writes (server-side, `NoteBusy`
   skips a note open elsewhere and retries next time). The export moves the file
   (`old` in `_Plan`), and git sees a rename.
6. **Leaving a note** is the note page's switch to another note or to "no note". Not the collab
   "leave": a tab in the background for a minute also sends that, and the writer may still come
   back to the note. Not `pagehide` either (changed while building): a reload fires it too, and a
   blank note would be gone after it. The call is `POST /api/notes/<slug>/lahkun`, after the final
   save, and the server decides:
   - **Blank** (only an empty `# ` line, marked as new, its collab session has no operation at all,
     no other participant): deleted for good. Any operation in the log means something was typed,
     maybe still on its way in a save beacon; the note then stays, and the sweep below takes it if it
     ends up blank.
   - **Marked, with a real title, nobody else in it:** the slug is renamed (5).
7. **A sweep** (worker, every 15 minutes, `notes-tidy`) does the same for marked notes whose tab
   went without a word (closed, or another page opened): a blank one untouched for an hour, a named
   one for 10 minutes, never while someone writes in it. It also points links still at an old slug
   (their note was busy at the rename) at the new one.
8. **The four existing `marge*` notes** get the mark once (`python -m app.notes_tidy`, run by hand),
   so the next sweep renames three of them; `marge-4` (blank) goes to the **trash**, not straight
   out, because it is already in git.

### Tags and folders

9. **Completion:** the tag and folder inputs get a `<datalist>` from `/api/tags` and the folder tree.
   The folder input also accepts a new folder as now.
10. **Rename a folder:** "Nimeta ümber" on a folder in the sidebar (a button that shows on hover and
    focus; on a phone, always). `POST /api/kaustad/nimeta {from, to}` moves every note in it and its
    subfolders in one transaction; moving into an existing folder merges. Template notes whose
    `mall_kaust` names the old folder are updated too. Git sees the moves.

### Links

11. **`#heading` in a link** (`[x](/m/slug#pealkiri)`, `[[slug#pealkiri|x]]` in git, which linkfmt
    already keeps): the note opens and scrolls to the first heading whose slugified text equals the
    fragment (decoded), else to the top. `go(slug, hash)` carries it; the address gets the hash.
12. **Link text after a rename:** when a save changes the note's title, the note page asks
    `GET /api/notes/<slug>/vanad-lingid?pealkiri=<old>`: notes whose link here has exactly the old
    title as its text. A notice with "Uuenda (n)" then walks them in the existing review window
    (`site/ai/review.js`, as the home chat's `reviewOnServer` does: a stand-in editor, then
    `POST …/apply` with the rev, 409 → shown again). The review window's AI-specific texts become
    parameters. Other link texts ("vt siit") are never touched.

### Search, Seotud, tasks, the daily note

13. **Search filters:** `#silt` (the tag), `kaust:kool` (the folder and below), `-#silt` excluded;
    the rest are words as now. Filters alone list the matching notes, newest first. Results show
    the folder and a "peidetud" mark.
14. **Sarnased märkmed** on top of the Seotud panel: an FTS `OR` query from the note's title words,
    tags and its 15 most frequent body words (4+ letters, an Estonian stop list), bm25 order, at most
    8, the note itself and daily notes left out. No new index.
15. **`/ulesanded`:** every task of live notes outside `Mallid/`, in groups: üle tähtaja · täna ·
    see nädal · hiljem · tähtajata; filters: folder, note, "näita tehtuid". A checkbox toggles
    through `/api/tasks/<id>/toggle` (a note open elsewhere says so, as on Täna). Top bar link
    "Ülesanded", palette `g u`.
16. **Daily template:** if `Mallid/` holds a note titled **"Päevik"**, a new daily note is made from
    it (`mallid.fill`, the heading replaced by the long date), still in `paevik/` with its
    `daily_date`.

### History and export

17. **"Ajalugu"** is a right-panel tab. `GET /api/notes/<slug>/ajalugu`: the commits that touched
    the note's file, following renames (`git log --follow --name-only`), at most 50: date and
    message. `GET …/ajalugu/<sha>`: that version as a note body (`linkfmt.from_file`). Clicking a
    version opens the review window against the editor's text; **Aktsepteeri** applies the kept
    changes into the open editor (`replaceMarkdown`), so co-editors get them as ordinary edits and
    git gets a new version. Read without the sync lock (plain reads of committed objects); a read
    that hits a reset answers 503 "Proovi uuesti". A note never synced says "Ajalugu tekib pärast
    esimest sünkroonimist". The last 2 minutes of typing are not in git yet: the panel says so.
18. **"Laadi alla .md":** `GET /api/notes/<slug>/md`, the body as stored (Markdown links,
    `/pildid/` addresses), `Content-Disposition: attachment` with the title as file name.

## Defaults

| # | Default | Change if… |
|---|---|---|
| D1 | 30 days in the trash | you want longer |
| D2 | Blank-note sweep after 1 h untouched, named after 10 min | — |
| D3 | 50 versions in Ajalugu | — |
| D4 | The daily template is the note titled "Päevik" in `Mallid/` | you want another name |
| D5 | Tasks of `Mallid/` notes are not listed | — |

## Packages and steps

One branch per package, in this order (each touches `notes.py`, `notes_api.py` or the note page,
so they go one after another, each merged into `arendus` before the next starts).

1. **`wp/prugikast`** (1–4, 18): `notes.restore/purge`, the purge job, `toast` action, Kustuta and
   the "⋯" menu, the trash dialog, `/api/prugikast`, `…/taasta`, `…/md`.
   → check: pytest: restore brings back links, tasks, a share and the daily date (and not when the
   day has a new one); purge waits for `file_path IS NULL`, then cascades; gitsync: delete → file
   gone, restore → file back. Playwright: delete, "Võta tagasi", restore and delete for good from
   the dialog, download.
2. **`wp/uus-marge`** (5–8): the two tables, `rename_slug`, `lahkun`, the sweep, `by_slug`
   fallback and redirect, `app/notes_tidy.py`.
   → check: pytest: blank + no ops → gone; one op → stays; real title → renamed once, a second
   title change keeps the slug; links pointing at the old slug rewritten; old slug opens the note;
   a note busy elsewhere untouched; gitsync renames the file. Playwright: "+ Uus", leave empty →
   not in the sidebar; type a title, switch → address and sidebar show the new slug; back button.
3. **`wp/kaustad-lingid`** (9–12): datalists, folder rename, heading fragments, link-text offer,
   review window texts as parameters.
   → check: pytest: rename moves subfolders, merges, updates `mall_kaust`; `vanad-lingid` finds only
   exact old texts. Playwright: completion picks a tag; rename a folder; a `#heading` link scrolls;
   rename a title → notice → review → other note's link text changed.
4. **`wp/otsing-ulesanded`** (13–16): search filters, Sarnased, `/ulesanded`, the daily template.
   → check: pytest: each filter, filters alone, hidden mark; Sarnased finds a note sharing words
   and leaves out the note itself; groups by date (Tallinn day edges); daily note from the
   template. Playwright: search `#silt kaust:x`, the tasks page toggles a task.
5. **`wp/ajalugu`** (17): the endpoints and the panel tab.
   → check: pytest against a temporary repo: versions follow a move and a rename, a version body
   comes back as the app's Markdown; Playwright (fake repo): open a version, keep one change.
6. **Review, deploy.** A reviewer per package before its merge; full `uv run pytest` and Playwright
   (chromium, firefox, mobile) with `set -o pipefail`; `docker compose up -d --build`; then run
   `python -m app.notes_tidy` in the web container and check the four notes.

## Risks

| Risk | Mitigation |
|---|---|
| A blank note deleted while someone types in it | only with zero operations in its session and no other participant; anything typed keeps it |
| A slug rename breaking a link or bookmark | `old_slugs` redirect; link bodies rewritten; never renamed while someone else is in it |
| Purge losing a file git still has | purge waits until the export removed the file |
| A restored version overwriting a co-editor's typing | applied through the editor as ordinary edits after the diff review |
| `git log` racing a pull's reset | plain reads of committed objects; a failed read says "proovi uuesti" |
| Deleting by mistake | trash for 30 days, "Võta tagasi" in the toast |
