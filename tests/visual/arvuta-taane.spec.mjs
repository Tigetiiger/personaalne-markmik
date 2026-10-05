// Indenting in cells: CodeMirror after for/if/def (desktop), the same in a cell Muya edits (phone),
// a cell typed into a note that had none, and ```arvuta keeping its name in Muya's language picker.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const saved = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);

async function note(page, body) {
  await login(page, "/tana");
  await api(page, "/_test/arvuta/kohalik", { method: "POST", body: {} });
  const title = unique("Taane");
  const n = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n${body}` } })).data;
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator("#editor .mu-editor")).toBeVisible();
  return n.slug;
}

async function typeBlock(page) {
  await page.keyboard.type("for i in range(3):");
  await page.keyboard.press("Enter");
  await page.keyboard.type("if i:");
  await page.keyboard.press("Enter");
  await page.keyboard.type("print(i)");
}

test("a cell typed into a note keeps its name and gets the code editor with indenting", async ({ page, isMobile }) => {
  test.skip(isMobile, "the phone has no code editor");
  const slug = await note(page, "Tekst.\n");
  await page.locator("#editor .mu-editor p").last().click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("```arvuta");
  await page.keyboard.press("Enter");
  await expect(page.locator(".arvuta-cm-host .cm-editor")).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => Boolean(document.activeElement?.closest(".arvuta-cm-host")))).toBe(true);
  await typeBlock(page);
  await expect.poll(() => saved(page, slug)).toContain("```arvuta\nfor i in range(3):\n    if i:\n        print(i)\n```");
});

test("Enter in a cell Muya edits keeps the indent and adds one after a colon", async ({ page, isMobile }) => {
  test.skip(!isMobile, "on a desktop the code editor indents");
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const slug = await note(page, "```arvuta\nx = 1\n```\n");
  const code = page.locator(".mu-code-block.arvuta-rakk .mu-codeblock-content");
  await code.click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await typeBlock(page);
  await expect.poll(() => saved(page, slug), { timeout: 15_000 }).toContain("```arvuta\nx = 1\nfor i in range(3):\n    if i:\n        print(i)\n```");
  expect(errors).toEqual([]);
});
