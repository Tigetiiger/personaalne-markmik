// /graaf: the link graph page (WP-I). Notes are seeded through /_test/graph/seed.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const INK = { light: [20, 32, 46], dark: [228, 233, 237] };
const HIVIS = [242, 194, 48];

// 30 notes: m01 is a hub, m02..m28 form a chain, m29 and m30 have no links; one linked and one
// unlinked daily note.
function vault() {
  const notes = [];
  const links = [];
  for (let i = 1; i <= 30; i++) {
    const slug = `m${String(i).padStart(2, "0")}`;
    const folder = i <= 6 ? "kool" : i <= 10 ? "kool/matemaatika" : i <= 20 ? "isiklik" : "";
    notes.push({ slug, title: `Märge ${i}`, folder, tags: i % 5 === 0 ? ["tähtis"] : [] });
    if (i >= 2 && i <= 27) links.push([slug, `m${String(i + 1).padStart(2, "0")}`]);
    if (i >= 2 && i <= 28 && i % 3 === 0) links.push([slug, "m01"]);
  }
  notes.push({ slug: "2026-09-24", folder: "paevik", is_daily: true }, { slug: "2026-09-23", folder: "paevik", is_daily: true });
  links.push(["2026-09-24", "m05"], ["m04", "puudub"]);
  return { notes, links };
}

async function seed(page, data) {
  const r = await api(page, "/_test/graph/seed", { method: "POST", body: data });
  expect(r.status).toBe(204);
}

async function settled(page) {
  await expect(page.locator("#graaf")).toHaveAttribute("data-settled", "1");
}

async function openGraph(page) {
  await page.goto("/graaf");
  await waitReady(page);
  await settled(page);
}

const nodeCount = (page) => page.evaluate(() => window.__graph.graphData().nodes.length);

/** Page coordinates of a node's centre, from force-graph's own transform. */
function nodePoint(page, slug) {
  return page.evaluate((slug) => {
    const fg = window.__graph;
    const node = fg.graphData().nodes.find((n) => n.slug === slug);
    const p = fg.graph2ScreenCoords(node.x, node.y);
    const rect = document.querySelector("#graaf-louend canvas").getBoundingClientRect();
    return { x: rect.left + p.x, y: rect.top + p.y };
  }, slug);
}

/** The slug of the node farthest from every other node: nothing else can take its click. */
function loneliestNode(page) {
  return page.evaluate(() => {
    const nodes = window.__graph.graphData().nodes;
    const gap = (n) => Math.min(...nodes.filter((m) => m !== n).map((m) => Math.hypot(m.x - n.x, m.y - n.y)));
    return nodes.reduce((a, b) => (gap(b) > gap(a) ? b : a)).slug;
  });
}

/** [r, g, b, a] of the canvas at a page point. The canvas is transparent where nothing is drawn. */
function pixel(page, { x, y }) {
  return page.evaluate(({ x, y }) => {
    const canvas = document.querySelector("#graaf-louend canvas");
    const rect = canvas.getBoundingClientRect();
    const k = canvas.width / rect.width;
    return [...canvas.getContext("2d").getImageData(Math.round((x - rect.left) * k), Math.round((y - rect.top) * k), 1, 1).data];
  }, { x, y });
}

/** Opaque and within a few steps of the expected colour. */
const isColor = ([r, g, b, a], expected) => a === 255 && Math.max(...[r, g, b].map((v, i) => Math.abs(v - expected[i]))) < 12;

test.beforeEach(async ({ page }) => {
  await login(page);
});

test("draws the vault, highlights a hovered node's neighbours and follows the theme", async ({ page }) => {
  const problems = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  await seed(page, vault());
  await openGraph(page);

  const canvas = page.locator("#graaf-louend canvas");
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box.width).toBeGreaterThan(500);
  expect(box.height).toBeGreaterThan(500);
  // 30 notes + the linked daily note; the unlinked daily note and the missing target are not nodes.
  await expect(page.locator("#graaf-arv")).toHaveText("31 märget · 36 seost");
  expect(await nodeCount(page)).toBe(31);

  const hub = await nodePoint(page, "m01");
  const lone = await nodePoint(page, "m30");
  expect(isColor(await pixel(page, hub), INK.light)).toBe(true);
  expect(isColor(await pixel(page, lone), INK.light)).toBe(true);

  await page.mouse.move(hub.x, hub.y);
  await expect(canvas).toHaveClass(/clickable/);
  await expect.poll(async () => isColor(await pixel(page, hub), HIVIS)).toBe(true);
  // m30 is not m01's neighbour: drawn nearly transparent.
  expect((await pixel(page, lone))[3]).toBeLessThan(60);

  await page.mouse.move(box.x + 3, box.y + 3);
  await expect(canvas).not.toHaveClass(/clickable/);
  await page.click("#teema");
  await expect(page.locator("html")).toHaveAttribute("data-teema", "tume");
  await expect.poll(async () => isColor(await pixel(page, hub), INK.dark)).toBe(true);
  expect(problems.filter((p) => !p.includes("Failed to load resource"))).toEqual([]);
});

test("the filters narrow the graph", async ({ page }) => {
  await seed(page, vault());
  await openGraph(page);
  await expect(page.locator("#graaf-filtrid select[name=folder] option")).toHaveText(["Kõik", "isiklik", "kool", "kool/matemaatika", "paevik"]);
  await expect(page.locator("#graaf-filtrid select[name=tag] option")).toHaveText(["Kõik", "tähtis"]);

  await page.selectOption("#graaf-filtrid select[name=folder]", "kool");
  await settled(page);
  expect(await nodeCount(page)).toBe(10);
  await expect(page.locator("#graaf-arv")).toHaveText(/^10 märget/);

  await page.selectOption("#graaf-filtrid select[name=folder]", "");
  await page.selectOption("#graaf-filtrid select[name=tag]", "tähtis");
  await settled(page);
  expect(await nodeCount(page)).toBe(6);

  await page.selectOption("#graaf-filtrid select[name=tag]", "");
  await page.uncheck("#graaf-filtrid input[name=orphans]");
  await settled(page);
  expect(await nodeCount(page)).toBe(29);

  await page.selectOption("#graaf-filtrid select[name=folder]", "kool/matemaatika");
  await page.selectOption("#graaf-filtrid select[name=tag]", "tähtis");
  await settled(page);
  await expect(page.locator("#graaf-tuhi")).toHaveText("Valikule vastavaid märkmeid pole.");
  await expect(page.locator("#graaf-louend")).toBeHidden();
});

test("clicking a node opens its note", async ({ page }) => {
  await seed(page, vault());
  await openGraph(page);
  const slug = await loneliestNode(page);
  const point = await nodePoint(page, slug);
  await page.mouse.move(point.x, point.y);
  await expect(page.locator("#graaf-louend canvas")).toHaveClass(/clickable/);
  await page.mouse.click(point.x, point.y);
  await page.waitForURL(`**/m/${slug}`);
});

test("an empty vault says so", async ({ page }) => {
  await seed(page, { notes: [], links: [] });
  await openGraph(page);
  await expect(page.locator("#graaf-tuhi")).toContainText("Märkmeid veel pole.");
  await expect(page.locator("#graaf-louend")).toBeHidden();
  await expect(page.locator("#graaf-arv")).toHaveText("0 märget · 0 seost");
});
