// Bundles third-party browser libraries that, unlike Muya, bundle cleanly: FullCalendar and
// force-graph become one ES module each in site/vendor. The app uses the system font: no web fonts.
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
};
for (const [name, contents] of Object.entries(entries)) {
  await build({
    stdin: { contents, resolveDir: root, loader: "js" },
    bundle: true,
    format: "esm",
    minify: true,
    legalComments: "none",
    outfile: join(vendor, `${name}.js`),
  });
}

console.log("site/vendor built");
