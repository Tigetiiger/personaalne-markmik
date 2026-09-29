// Bundles third-party browser libraries that, unlike Muya, bundle cleanly: FullCalendar,
// force-graph and TradingView's Lightweight Charts become one ES module each in site/vendor. The app uses the system font: no web fonts.
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const vendor = join(root, "site", "vendor");
rmSync(vendor, { recursive: true, force: true });
mkdirSync(vendor, { recursive: true });

const entries = {
  // One module exporting everything the calendar view needs, Estonian locale included.
  fullcalendar: `
    export { Calendar } from "@fullcalendar/core";
    export { default as dayGridPlugin } from "@fullcalendar/daygrid";
    export { default as timeGridPlugin } from "@fullcalendar/timegrid";
    export { default as listPlugin } from "@fullcalendar/list";
    export { default as interactionPlugin } from "@fullcalendar/interaction";
    export { default as etLocale } from "@fullcalendar/core/locales/et";
  `,
  "force-graph": `export { default } from "force-graph";`,
  // The stock page's price chart (Aktsiad). Its licence asks for the TradingView attribution link,
  // which the page shows.
  "lightweight-charts": `export * from "lightweight-charts";`,
};
for (const [name, contents] of Object.entries(entries)) {
  await build({
    stdin: { contents, resolveDir: root, loader: "js" },
    bundle: true,
    format: "esm",
    minify: true,
    // Apache-2.0 (Lightweight Charts) wants its notice kept with the code.
    legalComments: name === "lightweight-charts" ? "eof" : "none",
    outfile: join(vendor, `${name}.js`),
  });
}

console.log("site/vendor built");
