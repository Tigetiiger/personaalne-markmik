# Meedia: storing, tagging and sharing pictures and videos (plan, 2026-10-02)

**Status (2026-10-02):** steps 1–5 and 7 built on `wp/meedia`; step 6 (live, Discord) waits for the tunnel hostname.

**The user's request:** a store for videos and pictures with tags and normal search. Pictures get
tags automatically. Tags and other extra info live in a companion file in the same folder. Any
file can be shared as a link (e.g. into Discord), served on its own domain through the tunnel.

## The user's answers (2026-10-02)

| Question | Answer |
|---|---|
| Own app or part of Märkmik | **Part of Märkmik** (a new section; login, worker, AI runner and tunnel are reused) |
| Where the files live | **A plain folder for now**, but easy to back up: a new VM disk and a remote backup come later |
| What makes the automatic tags | **A light Claude model: Haiku** |
| Share domain and links | **`meedia.tigetiiger.ee`, public link** (unguessable token, no password, so Discord can unfurl it) |
| iPhone HEVC `.mov` videos | **Converted on upload; only one format is kept** (the original is not kept) |
| How files come in | **Web upload, copying into the folder, and a one-off import of an existing collection** |
| Folder layout | **One flat folder**; order comes from tags, dates and search |

## What exists

- **Note pictures** (`app/images.py`): content-hash names under `data/pildid`, 10 MB, no videos.
  They stay as they are; Meedia is a separate store and does not touch them.
- **Pictures for the model** (`app/ai/pictures.py`): `prepare(path)` turns a file upright,
  shrinks it to 1568 px and re-encodes it as JPEG without metadata. Reused for tagging.
- **The AI runner** (`app/ai/runner.py`): `run_structured` takes no images today; `stream_chat`
  does (`images=[(media_type, bytes)]`). `AI_DAILY_CAP` (300) caps all calls per day;
  extraction keeps its own share (`extract.DAILY_CAP = 150`).
- **The tunnel** (docs/TUNNEL-PLAN.md): `cloudflared` in the `tunnel` profile, `client_ip()`,
  Secure cookies and HSTS on tunnel requests, `PUBLIC_URL`, an allowed-Host list
  (`app/security.py`). Share links `/j/<token>` store only a sha256 of the token.
- **Limits that matter here:**
  - `MAX_CONTENT_LENGTH` is 25 MB app-wide.
  - Containers have `/tmp` on tmpfs, and Werkzeug spools large uploads there.
  - The root disk has 7.5 GB free.
  - The Cloudflare free plan caps a request body at **100 MB**. Its terms discourage serving
    large amounts of video through the plain CDN; a few personal Discord links are the gray zone.
  - The host has 4 cores and no GPU.

## Lead's calls

### The folder and the companion files

1. **One media root, mounted on its own.** Compose mounts `${MEDIA_HOST_DIR:-./data/meedia}` at
   `/meedia` in web and worker. Moving to the new VM disk later is one line in `.env`.
2. **The root holds only originals and their companion files.** Backup is one `rsync`/`restic`
   of that folder. Thumbnails, posters and previews go to `data/meedia-cache/`. The cache is not
   backed up and is rebuilt on demand.
3. **Flat.** `IMG_1234.jpg` sits next to `IMG_1234.jpg.json`. A clashing name gets `-2`, `-3`.
   Hidden working folders in the root: `.saabumas/` (uploads in progress, conversions) and
   `.prügi/` (deleted files with their companion files, emptied after 30 days).
4. **The companion file is the truth; SQLite is an index.**
   - `python -m app.meedia.reindex` rebuilds every table from the folder.
   - A companion file edited by hand is picked up on the next scan.
   - Shares are the exception: they are access control and live only in the database, which the
     nightly backup already covers.
5. **Companion file format: JSON**, UTF-8, indented, keys in a fixed order (readable diffs).
   ```json
   {
     "version": 1,
     "sha256": "…",
     "kind": "image",
     "mime": "image/jpeg",
     "bytes": 2483112,
     "width": 4032, "height": 3024, "duration": null,
     "taken_at": "2026-09-30T14:02:11+03:00",
     "gps": {"lat": 59.437, "lon": 24.745},
     "camera": "Apple iPhone 15",
     "added_at": "2026-10-02T18:00:00+03:00",
     "source": "upload",
     "converted_from": null,
     "title": "", "description": "Koer pargis sügisel",
     "tags": ["perekond"],
     "auto_tags": ["koer", "park", "sügis", "lehed"],
     "ocr": "",
     "ai": {"model": "haiku", "at": "2026-10-02T18:00:05+03:00"}
   }
   ```
   `tags` are the user's; `auto_tags` are the model's, kept apart so re-tagging never overwrites
   the user's. Removing an auto tag by hand records it in `rejected_tags`, so a re-run does not
   bring it back.

### Getting files in

6. **One intake path for all three ways in.** Every new file goes through the same steps:
   1. Read the metadata (EXIF / `ffprobe`).
   2. Convert the file if needed (step 7).
   3. Write the companion file.
   4. Index it.
   5. Queue it for tagging.
7. **Conversion: one format per kind** (the user's call for video; mine for pictures).
   - **Video becomes H.264 + AAC `.mp4`** (`ffmpeg -c:v libx264 -crf 22 -preset medium`,
     `+faststart`). Anything taller than 1080p is scaled down to 1080p, because 4K iPhone video
     would take hours on 4 cores and be four times the size.
   - **The original goes only after the copy is checked:** `ffprobe` opens it and the
     duration matches within 0.5 s. `converted_from` records the original's name and sha256.
     The original is moved to `.prügi/`, so it can be had back for 30 days (an import's original
     stays in the collection instead).
   - **HEIC/HEIF pictures become JPEG** (quality 92, EXIF kept). Browsers other than Safari
     cannot show HEIC. PNG, JPEG, WebP and GIF stay as they are.
   - **One conversion at a time, in the worker**, at low priority (`nice`). The file shows up as
     "töötlen" until it is done.
8. **Web upload** (`POST /meedia/upload`, owner only):
   - The upload is chunked (8 MB pieces into `.saabumas/`) and resumed on retry. Werkzeug never
     spools a whole video into the tmpfs `/tmp`.
   - Each chunk stays under Cloudflare's 100 MB request cap, so uploads through the tunnel work
     too.
   - The app-wide 25 MB limit stays for every other route.
   - The upload is accepted when phone and laptop agree on the final sha256.
9. **Copying into the folder:** the worker scans the root every 2 minutes. A file without a
   companion file, whose size has not changed between two scans, is taken in as `source: "folder"`.
10. **Import of an existing collection:** `python -m app.meedia.importer <path> [--move]`.
    - It copies (or moves) every file through the same intake, skipping duplicates by sha256.
    - Tagging is queued, not done in place (step 12).

### Automatic tags

11. **Haiku, with pictures in the call.** `run_structured` gets an `images=` argument, the same
    shape as `stream_chat`'s. **First a spike** checks that it works with `output_format`, and
    what one call costs in time.
12. **Batching and a cap.**
    - Up to 4 pictures go in one call, with one schema item per picture. A video is a call of its
      own (its 4 frames).
    - Tagging has its own daily share, `MEEDIA_DAILY_CAP` (default 100), like extraction (150).
      Both also count against `AI_DAILY_CAP` (300), so a large import cannot use up the
      assistant's calls.
    - Example: at 4 per call and 100 calls a day, 400 pictures a day. A collection of 10 000
      pictures takes about 25 days, unless both caps are raised for the import.
    - Spike (2026-10-02): 3 real Haiku calls tagged 3 photos, a 4 s video and a price label in
      12 s; the label's text came back exactly.
13. **What the model returns per picture:**
    - 3–10 tags in Estonian, lowercase, in the singular base form
    - a one-sentence description
    - text it can read on the picture (OCR)
    The prompt lists the 200 most used tags, so the model reuses "koer" instead of inventing
    "koerake".
14. **Videos:** `ffmpeg` takes 4 frames (10/35/60/85 % of the length), sent as one call. The
    poster is the 10 % frame.
15. **Privacy:** pictures go to Anthropic as the home chat's pictures already do: shrunk, without
    EXIF or GPS. The GPS stays in the companion file and is never sent.

### Search and the page

16. **`/meedia`**, a new item in the sidebar.
    - Layout: a grid of thumbnails, newest first, grouped by month (`taken_at`, else `added_at`).
    - Lightbox with arrows; videos play in it.
    - A side panel shows tags (add or remove), title, description, EXIF and the share button.
17. **One search box.**
    - Free text is searched with an FTS5 prefix search over name, title, description, tags and
      OCR. `unicode61 remove_diacritics 2` lets "koik" find "kõik".
    - `#koer` is a required tag and `-#koer` excludes one.
    - Chips: kind (pilt / video), a date range, "sildita" (untagged), "jagatud" (shared).
18. **Bulk actions** on a selection: add or remove a tag, delete, share as an album.

### Sharing on meedia.tigetiiger.ee

19. **A second public hostname in the same tunnel.** `meedia.tigetiiger.ee` points to
    `http://web:8000`, and `MEEDIA_PUBLIC_URL` in `.env` names it.
20. **The media host serves only shares.**
    - It serves `/s/<token>`, the files under it, and the CSS of that page.
    - Every other path is a 404. No login, no session cookie, no owner page on that host.
    - `/s/` on the owner's hosts is also a 404. The two hosts share nothing but the process.
21. **A share is a single file or a selection (album).**
    - The token is random (32 bytes, base64url) and the database stores only its sha256.
    - Optional settings: an expiry date and "allow download" (the original file). Revoking a
      share works at once.
    - The share dialog lists the file's active links.
22. **Discord unfurl.** `/s/<token>` is a small page with Open Graph tags.
    - Pictures: `og:image` is a 1600 px preview, with `twitter:card=summary_large_image`.
    - Videos: `og:video` (+ `:type video/mp4`, `:width`, `:height`) points straight at the `.mp4`,
      and `og:image` is the poster.
    - Unverified: how large a video Discord still plays inline. **Tested with a real Discord
      message** in the last step.
23. **Serving:**
    - `send_file` with Range requests, so video seeking works.
    - `Cache-Control: private, max-age=300`, so a revoked link stops working within minutes even
      through Cloudflare.
    - Public routes are rate-limited by `client_ip()`.

### Dependencies

- `ffmpeg` in the Docker image (apt, about 90 MB). It is needed for conversion, posters and video
  frames; there is no lighter tool for HEVC.
- `pillow-heif` (pip). Pillow alone cannot open iPhone HEIC pictures.

## Checklist (package `wp/meedia`)

1. [x] **Store:** mount, `meedia_*` tables + FTS5, companion read/write, `reindex`, folder scan,
   trash. Verify with tests: round trip companion file to index to companion file; a hand edit is
   picked up; a deleted index rebuilds equal; name clashes; a half-copied file is not taken in.
2. [x] **Intake:** metadata, HEIC to JPEG, video to mp4 with the check before the original is
   deleted, chunked upload, `import`. Verify with tests on small sample files (a HEIC, an HEVC
   `.mov` of 2 s, a broken file) and a resumed upload.
3. [x] **Tagging:** spike for `run_structured(images=)`, then the batched job, `MEEDIA_DAILY_CAP`,
   video frames, `rejected_tags`. Verify with tests in fake mode, plus one real call on 4 pictures.
4. [x] **Page:** grid, search syntax, lightbox, side panel, bulk actions. Verify with pytest for
   the search parser and queries, and Playwright for search, tagging and video play.
5. [x] **Sharing:** host split, `/s/<token>`, OG tags, Range, expiry and revoke, rate limit.
   Verify with tests: owner pages 404 on the media host; `/s/` 404 on the owner host; a revoked
   or expired token gives 404; a Range request gives 206.
6. [ ] **Live:** the user adds the public hostname in Cloudflare and `MEEDIA_PUBLIC_URL` in `.env`.
   Smoke test from outside: a picture link and a video link pasted into Discord unfurl and play.
7. [x] **Docs:** README (the media folder, how to back it up, moving it to the new disk),
   CONTRACTS, STATUS. Review, merge.

## The user's steps

- Later: a new VM disk, then `MEDIA_HOST_DIR=/srv/meedia` in `.env` and a move of the folder.
- In Cloudflare: Tunnel `markmik` → Public Hostname `meedia.tigetiiger.ee` → HTTP `web:8000`.
- A remote backup of the media folder and of `data/backups/`.
