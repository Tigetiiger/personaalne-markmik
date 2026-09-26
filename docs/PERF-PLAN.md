# Faster page loads (2026-09-26)

**Complaint.** Pages load slowly. The goal is to make everyday use more comfortable.

## Measured (deployed app, Chromium, 85 ms added latency: the user's RTT over Tailscale)

| Page | Time to ready | Requests |
|---|---|---|
| Märkmed, Täna, Kalender, Graaf, Postkast, Seaded | 770–1000 ms each | 32–39, of them ~28 answered 304 |
| A note (`/m/<slug>`) | 1800 ms first, 1070 ms again | ~60 |

**Causes, in order of weight**

1. **Every page revalidates every asset.** `/static/*` is sent with `Cache-Control: no-cache`, even
   the CSS with a `?v=<hash>`. So each navigation asks again for ~28 files: the app's ~25 ES
   modules plus the CSS. Each answer is a 304 that costs a round trip.
2. **The app's modules load as a waterfall.** `app.js` imports the shell and the other packages,
   which import their own modules. HTTP/1.1 allows 6 connections, and each import level waits one
   more round trip.
3. **Every top-bar click is a full page load.** HTML, then the modules, then the API calls.
4. The note page chains its API calls (`/api/notes/<slug>`, then `/api/collab/join`) before the
   editor shows text. `/api/inbox/count` costs about 100 ms on the server, on every page.
5. The app's own static files are not gzipped. Muya's are.

## Plan (lead's calls)

1. **Bundle the app's own JS** with esbuild into content-hashed files, and let Flask emit the
   hashed script tag from a manifest. Pages that need little code must not pull in the calendar
   or graph code: use code splitting, with lazy chunks for the heavy pages. Muya stays unbundled
   (esbuild breaks it) and keeps its immutable `/muya/<ver>/` URLs. One module instance stays
   one instance (the reason `app.js` had no `?v`).
2. **Cache what is versioned forever.** Hashed bundles, `?v=<hash>` CSS, vendor files and fonts
   get `Cache-Control: public, max-age=31536000, immutable`. HTML and APIs are unchanged.
   Precompress the bundles and CSS with `.gz` at build time, served as Muya's are.
3. **Flatten what is left:** `modulepreload` links for the chunks a page needs, so nothing waits
   on a chain.
4. **Note page:** start `collab/join` in parallel with the note fetch, or return the note with
   the join. Show the text as soon as it is known. Look at why `/api/inbox/count` takes about
   100 ms and fix it if it is cheap.
5. **Measure again** with the same probe. Target at 85 ms latency: top-bar pages under 400 ms,
   a note under 700 ms. If the pages are still over target, the next step is client-side
   navigation between top-bar pages. It is not done now.

## Checklist (package `wp/perf`)

- [x] esbuild bundle with hashed names, a manifest, and lazy chunks per heavy page; `npm run build`
      makes it; tests and Docker use it.
- [x] Immutable caching for versioned static URLs; `.gz` for bundles and CSS.
- [x] `modulepreload` for each page's chunks.
- [x] Note page API calls in parallel; `/api/inbox/count` checked.
- [x] Before/after numbers from the probe in this file.
- [x] Whole suite green: pytest; Playwright chromium, firefox and mobile.

## Result (2026-09-26, `wp/perf`)

Same probe (`tools/perf-probe.mjs`, now with `NOTE=<slug>` for the note page), Chromium, 85 ms
added latency, three runs each, against a throwaway instance on `100.122.97.7:8093`: gunicorn as
in the Dockerfile, a seeded database (32 notes, one of them a realistic page; 13 events; the
wizard answered). Before = `arendus` (ef92c85), after = `wp/perf`. Time to ready (ms), range
over the three runs:

| Page | Before | After | Requests before → after |
|---|---|---|---|
| `/` (first page after login) | 854–869 | 387–420 | 39 (23 × 304) → 31 (0 × 304) |
| `/tana` | 883–897 | 296–315 | 35 → 30 |
| `/kalender` | 959–993 | 327–342 | 32 → 24 |
| `/graaf` | 975–997 | 324–333 | 33 → 26 |
| `/postkast` | 950–965 | 241–261 | 34 → 25 |
| `/seaded` | 958–995 | 238–269 | 34 → 25 |
| `/` again | 767–790 | 239–254 | 35 → 35 |
| Note, first visit | 1620–1739 | 604–618 | 55 → 44 |
| Note, again | 1097–1184 | 472–504 | 67 → 49 |

After, no request is a 304: everything versioned comes from the browser's cache. The request
counts include those cache hits (the preloads and prefetches). The first `/` is the slowest
because the login page's own preloads are still arriving when the probe moves on; the next `/`
takes 240–254 ms. A top-bar page now costs its HTML round trip plus one round of API calls.

What changed, and why:

1. **Bundle** (`tools/build-app.mjs`, `npm run build`): the app's ~30 modules become content-hashed
   chunks in `site/dist`, 10 small files every page needs plus a lazy chunk per heavy page
   (FullCalendar, force-graph, the Postkast, Seaded, wizard and share code); the nine stylesheets
   become one (`site/app.css` lists them). Modules imported by URL from outside the bundle (the
   editor imports `/static/app.js`, `/static/ai/review.js`, `/static/ai/sse.js`; tests import
   `/static/app.js`, `/static/ai/index.js`) are answered with a one-line re-export of their built
   copy, so there is still one `app.js` instance. Muya stays unbundled; its build now lists what
   `note-editor.js` imports (`site/muya/PRELOAD`).
2. **Caching**: `/static/dist/*` is `immutable` and sent as its prebuilt `.gz` (the helper the
   `/muya` route already used). **Firefox finding:** every request reads the session, so Flask
   added `Vary: Cookie`, and the permanent session's cookie is re-signed on every response.
   Firefox therefore revalidated every "immutable" file on every page, Muya's included (Chromium's
   memory cache hid it). Static responses now leave the session alone; verified in Firefox with
   the server's access log: after the first visit only HTML, API calls and the re-export
   `/static/app.js` reach the server.
3. **Preload**: each page links `modulepreload` for its chunks; the note page also for Muya's
   modules and `/static/app.js` (the one URL still revalidated, now off the critical path). The
   owner's pages `prefetch` the other pages' lazy chunks, so the first visit to Kalender or Graaf
   after a deploy finds them cached too.
4. **Note page**: a page's mounts now run side by side (`app.js`): the notes list no longer waits
   for the note to open, nor Täna's agenda for the notes list. The note, the notes list and the
   tags load in one round; then the join. `/api/inbox/count` takes 3 ms on the server: the
   ~190 ms seen before was queueing behind the 304s on six connections; it is one round trip now.

**Client-side navigation is not needed** for the target: every top-bar page is under 400 ms and
the note page under 700 ms. It could save at most the HTML round trip (~130 ms) per click.

Open:

- The note's `collab/join` still starts after the note fetch and Muya's evaluation (~200 ms after
  the HTML). Starting the join with the page needs an `openNote` change (WP-A): about 100 ms more.
- A first note visit after a deploy fetches Muya's 22 icons and 2 fonts after the editor mounts
  (not preloaded).
- The probe is Chromium only; Firefox was checked for caching, not timed.
