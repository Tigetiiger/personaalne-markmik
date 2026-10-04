// The cells' code editor (docs/TAIENDUS-PLAN.md): CodeMirror over each cell with completion from a
// local Jedi (/_test/arvuta/kohalik), column names, signatures, errors while typing, Shift+Enter, the
// text saved in the note's Markdown, and a co-editor's change showing up.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });
test.skip(({ isMobile }) => isMobile, "the code editor is for the desktop");

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const saved = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);
const BODY = "## Andmed\n\n| Kuu | Kulu (€) |\n| --- | --- |\n| mai | 4 |\n\n```arvuta\nt = tabel(\"Andmed\")\n```\n\n```arvuta\nprint(1)\n```\n";

async function open(page, slug) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  if (!slug) {
    await login(page, "/tana");
    expect((await api(page, "/_test/arvuta/kohalik", { method: "POST", body: {} })).status).toBe(204);
    const title = unique("Redaktor");
    slug = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n${BODY}` } })).data.slug;
  }
  await page.goto(`/m/${slug}`);
  await expect(page.locator(".arvuta-cm-host .cm-editor")).toHaveCount(2);
  return slug;
}

const cm = (page, i) => page.locator(".arvuta-cm-host .cm-content").nth(i);

test.afterEach(async ({ page }) => expect(page.errors || []).toEqual([]));

test("completion, column names, signature and errors while typing", async ({ page }) => {
  const slug = await open(page);
  await expect(cm(page, 0)).toHaveText('t = tabel("Andmed")');
  await cm(page, 1).click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("\nt.sort_v");
  const list = page.locator(".cm-tooltip-autocomplete");
  await expect(list).toContainText("sort_values");
  await page.waitForTimeout(200);
  await page.keyboard.press("Tab");
  await page.keyboard.type("(");
  await expect(page.locator(".cm-signature")).toContainText("sort_values(");
  await expect(page.locator(".cm-signature b")).toHaveText("by");
  await page.keyboard.type('["');
  await expect(list).toContainText("Kulu (€)");
  // CodeMirror ignores Enter for 75 ms after the list opens, so a quick key is no accident.
  await page.waitForTimeout(200);
  await page.keyboard.press("Enter");
  await expect(cm(page, 1)).toContainText('t.sort_values(["Kulu (€)"])');
  await page.keyboard.press("Control+End");
  await page.keyboard.type("\nprint(tundmatu)");
  await expect(page.locator(".cm-lintRange-error")).toHaveCount(1);
  await expect.poll(() => saved(page, slug)).toContain('t.sort_values(["Kulu (€)"])\nprint(tundmatu)\n```');
});

test("Shift+Enter in the editor runs and moves to the next cell", async ({ page }) => {
  await open(page);
  await cm(page, 0).click();
  await page.keyboard.press("Shift+Enter");
  await expect(page.locator(".arvuta-nr").first()).not.toHaveText("[ ]");
  await expect.poll(() => page.evaluate(() => document.activeElement?.closest(".arvuta-cm-host") === document.querySelectorAll(".arvuta-cm-host")[1])).toBe(true);
});

test("a co-editor's typing shows up in the other page's editor", async ({ page, context }) => {
  const slug = await open(page);
  const other = await context.newPage();
  await open(other, slug);
  await cm(other, 1).click();
  await other.keyboard.press("Control+End");
  await other.keyboard.type("\nprint('teiselt')");
  await expect(cm(page, 1)).toContainText("print('teiselt')", { timeout: 15_000 });
  await other.close();
});

test("a co-editor's change merges around this editor's own unsent one", async ({ page }) => {
  await login(page, "/tana");
  const cases = await page.evaluate(async () => {
    const { theirChange } = await import("/static/arvuta/yhenda.js");
    const apply = (text, c) => (c ? text.slice(0, c.from) + c.insert + text.slice(c.to) : text);
    const run = (base, mine, theirs) => apply(mine, theirChange(base, mine, theirs));
    return [
      run("a = 1\nb = 2\n", "a = 1\nb = 2\n", "a = 10\nb = 2\n"), // nothing of mine: theirs
      run("a = 1\nb = 2\n", "a = 1\nb = 2\nc = 3\n", "a = 10\nb = 2\n"), // theirs before mine
      run("a = 1\nb = 2\n", "x = 0\na = 1\nb = 2\n", "a = 1\nb = 20\n"), // theirs after mine
      run("a = 1\n", "a = 5\n", "a = 7\n"), // the same place: mine stays
    ];
  });
  expect(cases).toEqual(["a = 10\nb = 2\n", "a = 10\nb = 2\nc = 3\n", "x = 0\na = 1\nb = 20\n", "a = 5\n"]);
});
