// A note as a notebook (docs/JUPYTER-PLAN.md), the sandbox's kernel running locally
// (/_test/arvuta/kohalik): colours, Shift+Enter and [n], "+ Kood · + Tekst", moving and deleting
// cells, a plot, a DataFrame table and the automatic switch.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });
test.skip(({ isMobile }) => isMobile, "toolbars and keys are for the desktop editor");

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const saved = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);

async function open(page, body) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, "/tana");
  expect((await api(page, "/_test/arvuta/kohalik", { method: "POST", body: {} })).status).toBe(204);
  const title = unique("Märkmik");
  const n = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n${body}` } })).data;
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator(".mu-code-block.arvuta-rakk").first()).toBeVisible();
  return n;
}

const cell = (page, i) => page.locator(".mu-code-block.arvuta-rakk").nth(i);
// A cell is edited in its code editor laid over it (site/arvuta/koodiredaktor.js).
const code = (page, i) => page.locator(".arvuta-cm-host .cm-content").nth(i);

test.afterEach(async ({ page }) => expect(page.errors || []).toEqual([]));

test("cells are coloured as Python and show [n] after a run", async ({ page }) => {
  await open(page, "```arvuta\nx = 'tere'  # kommentaar\nprint(x)\n```\n");
  await expect(cell(page, 0).locator(".token.string")).toHaveCount(1);
  await expect(cell(page, 0).locator(".token.comment")).toHaveCount(1);
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("tere");
  await expect(page.locator(".arvuta-nr")).toHaveText("[1]");
});

test("Shift+Enter runs and moves on, making a new cell after the last", async ({ page }) => {
  const n = await open(page, "```arvuta\na = 2\n```\n\n```arvuta\na * 21\n```\n");
  await page.getByRole("button", { name: "Lülita automaatne arvutus välja" }).click();
  await expect(page.getByRole("button", { name: "Lülita automaatne arvutus sisse" })).toBeVisible();
  await code(page, 0).click();
  await page.keyboard.press("Shift+Enter");
  await expect(page.locator(".arvuta-nr").first()).toHaveText("[1]");
  await page.keyboard.press("Shift+Enter"); // in the second cell now
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("42");
  await expect(page.locator(".mu-code-block.arvuta-rakk")).toHaveCount(3); // a new, empty one at the end
  await page.keyboard.type("a + 1");
  await page.keyboard.press("Control+Enter");
  await expect(page.locator(".mu-code-block.arvuta-valjund").last()).toContainText("3");
  await expect.poll(() => saved(page, n.slug)).toContain("```arvuta\na + 1\n```");
});

test("+ Kood and + Tekst add blocks; ↑ moves a cell with its output; ✕ and Ctrl+Z", async ({ page }) => {
  const n = await open(page, "Esimene lõik.\n\n```arvuta\nprint('A')\n```\n");
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("A");
  await page.locator(".arvuta-pluss").first().hover();
  await page.getByRole("button", { name: "Uus koodiplokk siia" }).click();
  await page.keyboard.type("print('B')");
  await expect(page.locator(".mu-code-block.arvuta-valjund").nth(1)).toContainText("B");
  await page.locator(".arvuta-pluss").nth(1).hover();
  await page.getByRole("button", { name: "Uus tekstilõik siia" }).last().click();
  await page.keyboard.type("Lõpptekst");
  await expect.poll(() => saved(page, n.slug)).toMatch(/print\('B'\)\n```\n\n```väljund\nB\n```\n\nLõpptekst/);
  // The second cell (with its output) goes above the first one's chunk.
  await page.getByRole("button", { name: "Liiguta plokk üles" }).nth(1).click();
  await expect.poll(() => saved(page, n.slug)).toMatch(/Esimene lõik\.\n\n```arvuta\nprint\('B'\)\n```\n\n```väljund\nB\n```\n\n```arvuta\nprint\('A'\)/);
  await page.getByRole("button", { name: "Kustuta plokk koos väljundiga" }).first().click();
  await expect(page.locator(".mu-code-block.arvuta-rakk")).toHaveCount(1);
  await page.locator("#editor .mu-editor").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+z");
  await expect(page.locator(".mu-code-block.arvuta-rakk")).toHaveCount(2);
});

test("a plot and a DataFrame show as a picture and a table; errors are red", async ({ page }) => {
  const n = await open(page, "```arvuta\nimport pandas as pd\nimport matplotlib.pyplot as plt\ndf = pd.DataFrame({'x': [1, 2, 3], 'y': [1, 4, 9]})\nplt.plot(df['x'], df['y'])\ndf\n```\n\n```arvuta\n1 / 0\n```\n");
  await expect(page.locator(".arvuta-graafik img")).toHaveCount(1);
  await expect(page.locator(".arvuta-tabel-pealkiri")).toContainText("Tabel: df · 3 rida × 2 veergu");
  await expect(page.locator("table.arvuta-valjund-tabel, .arvuta-valjund-tabel table").first()).toContainText("9");
  await expect(page.locator(".mu-code-block.arvuta-viga")).toContainText("ZeroDivisionError");
  await expect.poll(() => saved(page, n.slug), { timeout: 15_000 }).toMatch(/!\[väljund\]\(\/pildid\/[0-9a-f]{40}\.png\)/);
  // The output table is not an input: one run more changes nothing.
  await page.getByRole("button", { name: "Käivita kõik plokid" }).first().click();
  await expect(page.locator(".arvuta-graafik img")).toHaveCount(1);
});

test("with the switch off, nothing runs by itself", async ({ page }) => {
  const n = await open(page, "```arvuta\nprint('üks')\n```\n");
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("üks");
  await page.getByRole("button", { name: "Lülita automaatne arvutus välja" }).click();
  await expect.poll(() => api(page, `/api/notes/${n.slug}`).then((r) => r.data.arvutus_auto)).toBe(false);
  await code(page, 0).click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("\nprint('kaks')");
  await page.waitForTimeout(2000);
  await expect(page.locator(".mu-code-block.arvuta-valjund")).not.toContainText("kaks");
  await page.keyboard.press("Control+Enter");
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("kaks");
});
