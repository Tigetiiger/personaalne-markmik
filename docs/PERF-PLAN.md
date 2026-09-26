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

- [ ] esbuild bundle with hashed names, a manifest, and lazy chunks per heavy page; `npm run build`
      makes it; tests and Docker use it.
- [ ] Immutable caching for versioned static URLs; `.gz` for bundles and CSS.
- [ ] `modulepreload` for each page's chunks.
- [ ] Note page API calls in parallel; `/api/inbox/count` checked.
- [ ] Before/after numbers from the probe in this file.
- [ ] Whole suite green: pytest; Playwright chromium, firefox and mobile.
