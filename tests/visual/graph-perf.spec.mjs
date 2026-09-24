// /graaf with a large vault: 1 000 notes and 2 000 links draw and stand still within 2 s.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

function bigVault() {
  let state = 42;
  const random = () => (state = (state * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
  const slug = (i) => `n${i}`;
  const notes = Array.from({ length: 1000 }, (_, i) => ({ slug: slug(i), title: `Märge ${i}`, folder: `kaust${i % 7}` }));
  const pairs = new Set();
  while (pairs.size < 2000) {
    const src = Math.floor(random() * 1000);
    // Squared: low numbers are hit more often, so some notes become hubs as in a real vault.
    const dst = Math.floor(random() ** 2 * 1000);
    if (src !== dst) pairs.add(`${src} ${dst}`);
  }
  const links = [...pairs].map((p) => p.split(" ").map((i) => slug(i)));
  return { notes, links };
}

// Tracing records the screen while the graph animates and would be what gets measured.
test.use({ trace: "off" });

test("1 000 notes and 2 000 links settle in under 2 s", async ({ page }) => {
  await login(page);
  const r = await api(page, "/_test/graph/seed", { method: "POST", body: bigVault() });
  expect(r.status).toBe(204);

  await page.goto("/graaf");
  // performance.now() counts from this page's navigation, so this is load + fetch + layout.
  const ms = await page.waitForFunction(() => document.getElementById("graaf")?.dataset.settled === "1" && performance.now(), null, { polling: "raf" });
  const elapsed = await ms.jsonValue();
  console.log(`graph of 1000 notes settled after ${Math.round(elapsed)} ms`);
  expect(elapsed).toBeLessThan(2000);
  await waitReady(page);
  expect(await page.evaluate(() => window.__graph.graphData().links.length)).toBe(2000);
});
