// Bundles the app's own browser code (site/*.js, site/app.css) into content-hashed files in
// site/dist, so the browser can cache them for good and a page needs a handful of requests instead
// of a waterfall of ~25 modules. Run after build-vendor (the calendar and graph chunks take in its
// FullCalendar and force-graph). Muya is not bundled (tools/build-editor.mjs says why).
//
// site/dist/manifest.json tells the server (app/views/core.py):
//   files:   source name -> built file, for the <script>/<link> tags;
//   modules: the modules code outside the bundle imports by their /static URL (the editor under
//            /muya, tests). The server answers those URLs with a re-export of the built file, so
//            there is still exactly one instance of app.js and its registries;
//   preload: the files every page needs ("*") and the lazy ones a page's mount loads at once;
//   lazy:    all of those lazy ones;
//   outputs: every file the build wrote, all content-hashed: the only ones /static/dist serves.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { build } from "esbuild";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const site = join(root, "site");
const out = join(site, "dist");

// Imported by URL from outside the bundle: app.js by everything, ai/review.js and ai/sse.js by the
// editor's inline AI (tools/editor/inline-ai.js), ai/index.js by the AI tests.
const MODULES = ["app.js", "ai/index.js", "ai/review.js", "ai/sse.js"];
// What a page's mount (registerPage) imports lazily as soon as it runs.
const PAGE_LAZY = {
  calendar: ["vendor/fullcalendar.js"],
  graph: ["vendor/force-graph.js"],
  inbox: ["inbox/postkast.js"],
  settings: ["inbox/settings.js"],
  connections: ["inbox/cards.js"],
  wizard: ["inbox/wizard.js"],
  "share-view": ["share/guest.js"],
  "share-edit": ["share/guest.js"],
  "stocks-watchlist": ["stocks/watchlist.js"],
  "stocks-ticker": ["stocks/ticker.js", "stocks/research.js", "stocks/position.js", "stocks/news.js", "vendor/lightweight-charts.js"],
  "stocks-portfolio": ["stocks/portfolio.js"],
  "stocks-tx": ["stocks/tx.js"],
  "stocks-explore": ["stocks/explore.js"],
  "stocks-sources": ["stocks/sources.js"],
  today: ["stocks/today.js"],
  meedia: ["meedia/page.js"],
};

rmSync(out, { recursive: true, force: true });

// The sources import each other as "/static/<path>", the URL they are served at.
const staticUrls = {
  name: "static-urls",
  setup(b) {
    b.onResolve({ filter: /^\/static\// }, (args) => ({ path: join(site, args.path.slice("/static/".length)) }));
  },
};

const modules = await build({
  entryPoints: MODULES.map((m) => join(site, m)),
  bundle: true,
  splitting: true,
  format: "esm",
  minify: true,
  legalComments: "none",
  outdir: out,
  outbase: site,
  entryNames: "[dir]/[name]-[hash]",
  chunkNames: "chunks/[name]-[hash]",
  plugins: [staticUrls],
  metafile: true,
  logLevel: "warning",
});
// theme.js stays a classic script: it must run before the first paint.
const assets = await build({
  entryPoints: [join(site, "theme.js"), join(site, "app.css")],
  bundle: true,
  minify: true,
  legalComments: "none",
  outdir: out,
  outbase: site,
  entryNames: "[dir]/[name]-[hash]",
  metafile: true,
  logLevel: "warning",
});

const src = (path) => relative(site, join(root, path)); // metafile paths are relative to root
const built = (path) => relative(out, join(root, path));
const outputs = { ...modules.metafile.outputs, ...assets.metafile.outputs };
const files = {};
for (const [path, o] of Object.entries(outputs)) if (o.entryPoint) files[src(o.entryPoint)] = built(path);

// A file and every chunk it imports statically, in load order.
function closure(file, seen = new Set()) {
  if (seen.has(file)) return seen;
  seen.add(file);
  for (const i of modules.metafile.outputs[relative(root, join(out, file))].imports) {
    if (i.kind === "import-statement") closure(built(i.path), seen);
  }
  return seen;
}
// Every page loads app.js and the package entries it imports (site/app.js, MODULES there).
const always = closure(files["app.js"]);
for (const i of modules.metafile.inputs[relative(root, join(site, "app.js"))].imports) {
  if (i.kind === "dynamic-import") closure(files[src(i.path)], always);
}
const preload = { "*": [...always] };
const lazy = new Set();
for (const [page, names] of Object.entries(PAGE_LAZY)) {
  const now = new Set();
  for (const name of names) closure(files[name], now);
  preload[page] = [...now].filter((f) => !always.has(f));
  preload[page].forEach((f) => lazy.add(f));
}

for (const path of Object.keys(outputs)) {
  const file = join(root, path);
  writeFileSync(`${file}.gz`, gzipSync(readFileSync(file), { level: 9 }));
}
mkdirSync(out, { recursive: true });
writeFileSync(join(out, "manifest.json"), JSON.stringify({ files, modules: MODULES, preload, lazy: [...lazy], outputs: Object.keys(outputs).map(built) }, null, 1));
console.log("site/dist built");
