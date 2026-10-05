# Data files in notes for Python cells (plan, 2026-10-05)

**Status (2026-10-05):** built (store on `wp/failid-store`, sandbox and UI on `wp/failid-sandbox`).
Changed while building: a text file holding a NUL byte is refused (Windows-1257 decodes almost any
binary); a web address takes its type and name from its last path part; the code runs in its own
folder (`kaust/`) inside the run's scratch space, so only the note's files and `valjund/` are there;
the note's linked files are part of the inputs hash, so "Värskenda" re-runs the cells; the request
cap is 26 MB so a 25 MB file fits with its form packaging. Spike S11 was folded into the tests: a
run with files is checked against the real container (`pytest -m docker`).

**The user's request:** link or upload an input file in a note and use it in the note's code.

## The user's answers (2026-10-05)

| Question | Answer |
|---|---|
| File types | **Data files:** CSV, TSV, TXT, JSON, Excel (.xlsx), Parquet; a helper reads them into a DataFrame or text; anything else is refused |
| How files get in | **All three:** upload or drag and drop; a web address the app downloads a copy of (with "refresh"); picking a file already attached to another note |
| Git | **Server only:** git gets the note's link, not the file |
| Files written by code | **Yes:** what a cell writes into `valjund/` becomes a downloadable attachment under its output |

## What exists

- **Pictures** (`app/images.py`): content-hash names in `data/pildid`, picture types only, 10 MB,
  `note_images` says which pictures a note's share-link guests may load. **Meedia** is a separate
  picture and video store. Neither takes data files.
- **The sandbox** (`arvutus`, docs/ARVUTUS-PLAN.md) has no network and no volume but its socket;
  each run is a fresh process in an emptied scratch folder, one run at a time. Requests and answers
  are one JSON line (request cap 2 MB, answer cap 24 MB).
- **`ics.download(url, …)`** fetches public addresses only (checked per redirect, pinned IP), with
  size and time caps: the safe way to download a web address.
- **Output regions** (docs/JUPYTER-PLAN.md) are blocks the app owns under a cell; plots are stored
  with author `arvutus` and cleaned at night when no note shows them.
- `MAX_CONTENT_LENGTH` is 25 MB app-wide. The nightly backup copies only the SQLite database, on
  the same disk.

## How it looks in a note

```markdown
Mõõtmised: [andmed.csv](/failid/3f2a…9c.csv) · [hinnad.xlsx](/failid/81b0…d4.xlsx)

```arvuta
df = fail("andmed.csv")            # a DataFrame (CSV, TSV, Excel, Parquet), JSON as data, TXT as text
raw = open("andmed.csv").read()    # the files are also in the cell's working folder by name
df.describe().to_csv("valjund/kokkuvote.csv")
```

📎 [kokkuvote.csv](/failid/77c1…0e.csv)
```

## Lead's calls

### Storing and linking

1. **Files live in `data/failid/<sha256>.<ext>`** by content (the same file twice is one file),
   with a table `files` (name, original name, type, size, author, source URL, fetched at) and
   `note_files` (which note's share-link guests may download which file), like pictures.
2. **A note uses a file by linking it:** `[andmed.csv](/failid/<sha>.csv)`. The link's text is the
   name the code uses; it must be a plain file name (no folders), unique in the note. Links in the
   note's output regions are outputs, not inputs.
3. **Accepted:** `.csv .tsv .txt .json .xlsx .parquet`, at most **25 MB** each. Each is checked on
   the way in: text types decode as UTF-8 (or Windows-1257, the Estonian Excel export) and JSON
   parses; `.xlsx` is a zip holding `xl/workbook.xml` whose unpacked size stays under 200 MB; Parquet
   starts and ends with `PAR1`. Anything else is refused with a reason.
4. **Git gets the link only.** The file stays in `data/failid`. Since the nightly backup copies only
   the database, it also copies new files to `data/backups/failid/` — still the same disk: the VM
   snapshot (or a later off-site backup, as planned for Meedia) is what survives a lost disk.

### Getting files in

5. **A "Failid" tab in the note's right panel** lists the note's files (name, size, source, a
   download link) and has **"Lisa fail"** with three ways in:
   - **Laadi üles:** a file picker; dropping a data file onto the note does the same.
   - **Veebiaadress:** the app downloads the address with `ics.download` (public addresses only,
     25 MB, 30 s), remembers the address, and **"Värskenda"** fetches it again; new content is a new
     file, and the note's links are moved to it in the editor.
   - **Olemasolev fail:** a search over files already attached to other notes.

   Each adds a paragraph with the link under the block the caret is in (or at the note's end).
6. **Owner only adds files.** A share-link guest can download the files the shared note links
   (`note_files`), not add them.

### In the sandbox

7. **A run gets exactly the files its note links**, not the store: their bytes go with the request
   (base64), at most 50 MB per run, and the sandbox server writes them into the run's working
   folder under their link names before the code starts. The store is never mounted into the
   sandbox, so code cannot read another note's files.
8. **`fail(name)`** reads by type: CSV and TSV into a DataFrame (separator sniffed; with `;` the
   decimal comma), Excel (first sheet, or `leht=`) and Parquet into a DataFrame, JSON into Python
   data, TXT into a string. The sandbox image gets `openpyxl` and `pyarrow`.
9. **Files a cell writes into `valjund/`** (at most 5 per cell, the accepted types, 25 MB each) come
   back with the answer, are checked like uploads, stored with author `arvutus` and shown in the
   output region as `📎 [name](/failid/<sha>.<ext>)`. The nightly clean-up deletes `arvutus` files
   no note links any more, as it does plots.
10. **Completion knows `fail()`** (the stub in `taiendus` returns a DataFrame for table types).

## Defaults

| # | Default | Change if… |
|---|---|---|
| D1 | 25 MB per file, 50 MB of input per run | your data is bigger |
| D2 | Excel: the first sheet unless `fail("x.xlsx", leht="Nimi")` | — |
| D3 | Web addresses are fetched only when added or on "Värskenda", never at run time | you want a fresh copy every run |

## Spike S11 (first, half a day)

- Base64 through the sandbox socket: time and memory for a 25 MB CSV in an automatic run; whether
  a cache of files the sandbox has already seen is needed.
- `openpyxl` and `pyarrow` in the sandbox image: size, import time, memory under the limits.
- Muya: a link paragraph to `/failid/…` renders and survives editing; a dropped non-picture file
  reaches our handler, not Muya's picture upload.

## Packages and steps

1. **`wp/failid-store`**: schema (`files`, `note_files`), `app/failid.py` (check, store, by link,
   guest access), `POST /api/failid` (upload), `POST /api/failid/url` and `/<name>/refresh`,
   `GET /api/failid?q=`, `GET /failid/<name>` (owner, or a guest of a note that links it;
   `Content-Disposition: attachment`, `nosniff`), the backup copy, the clean-up.
   → check: pytest for each type's check (a renamed binary, a zip bomb, a bad encoding refused), an
   SSRF address refused, refresh making a new file, guest access only through `note_files`.
2. **`wp/failid-sandbox`**: inputs into the working folder, `fail()`, `valjund/` collected, caps,
   the image with openpyxl and pyarrow; `/api/arvuta` sending the note's linked files and storing
   the outputs; output-region `📎` links; the taiendus stub.
   → check: pytest with the local kernel: each type through `fail()`, a cell writing a CSV that
   becomes a link, a missing or duplicate name explained, input cap; `pytest -m docker`: the files
   are there, another note's are not.
3. **`wp/failid-ui`**: the "Failid" panel tab, the "Lisa fail" dialog (upload, address, existing),
   drop onto the note, refresh moving links.
   → check: Playwright: upload a CSV and use it in a cell, add by address (a test route serves the
   file), pick an existing file, refresh with new content, a guest downloads a linked file and is
   refused another.
4. **Review, deploy.** A reviewer and a security pass (uploads, the URL fetch, downloads), full
   pytest and Playwright, `docker compose up -d --build`.

## Risks

| Risk | Mitigation |
|---|---|
| Files lost with the disk | server-only by choice; nightly copy to `data/backups/failid`, VM snapshots; off-site backup is a later step |
| Big files slow every automatic run | 50 MB per run cap; spike S11 measures; a sandbox-side cache by content hash if needed; the automatic switch |
| A web address reaching the tailnet or the VM | `ics.download`: public addresses only, checked per redirect, IP pinned |
| A hostile file (zip bomb, odd encoding) | checked on the way in; read only inside the sandbox |
| Code reading another note's files | the store is never in the sandbox; a run gets only its note's linked files |
| A downloaded file run as a page | served as an attachment with `nosniff` and the sandboxed CSP |
