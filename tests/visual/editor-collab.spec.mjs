// Live co-editing: two browsers on the same note (ported from Steam's koostoo.spec.mjs).
import { DatabaseSync } from "node:sqlite";

import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";

const DB = `.vt-data-${process.env.VT_PORT || 8780}/test.db`;
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });

// One login per file: /login allows 20 requests a minute per address.
let cookies = null;
async function editorFor(browser, baseURL, slug) {
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  if (cookies) {
    await context.addCookies(cookies);
    await page.goto(`/_test/editor/${slug}`);
    await waitReady(page);
  } else {
    await login(page, `/_test/editor/${slug}`);
    cookies = await context.cookies();
  }
  await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
  return page;
}

const markdown = (page) => page.evaluate(() => window.editor.getMarkdown());

async function caretAtEnd(page, selector, n = 0) {
  await page.locator(`#editor .mu-editor ${selector}`).nth(n).click();
  await page.keyboard.press("End");
}

async function close(...pages) {
  for (const page of pages) {
    expect(page.errors, "JavaScript errors on the page").toEqual([]);
    await page.context().close();
  }
}

test("two people type at once: same text on both sides, the other's caret shown", async ({ browser, baseURL }) => {
  const slug = unique("koos");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate(() => window.editor.replaceMarkdown(`# Koos\n\n- üks\n- kaks\n\nLõik tekstiga.\n\nTeine lõik.\n`));
  const b = await editorFor(browser, baseURL, slug);
  await expect.poll(() => markdown(b)).toBe(await markdown(a));

  // Different places and the same line at once.
  await caretAtEnd(a, "h1");
  await caretAtEnd(b, "li p", 0);
  await Promise.all([a.keyboard.type(" AAA", { delay: 20 }), b.keyboard.type(" BBB", { delay: 20 })]);
  await caretAtEnd(a, "p", 2);
  await caretAtEnd(b, "p", 2);
  await Promise.all([a.keyboard.type(" a1a2", { delay: 20 }), b.keyboard.type(" b1b2", { delay: 20 })]);

  await expect.poll(async () => (await markdown(a)) === (await markdown(b)), { timeout: 10_000 }).toBe(true);
  const text = await markdown(a);
  expect(text).toContain("# Koos AAA");
  expect(text).toContain("- üks BBB");
  expect(text).toMatch(/tekstiga\. (a1a2 b1b2|b1b2 a1a2)/);

  // b selects a line: a sees the selection and b's caret with its name.
  await b.locator("#editor .mu-editor p").nth(3).click();
  await b.keyboard.press("End");
  await b.keyboard.press("Shift+Home");
  await expect(a.locator(".mt-remote-caret span", { hasText: "Omanik" })).toBeVisible();
  await expect(a.locator(".mt-remote-selection").first()).toBeVisible();
  // Whoever is connected saves; both see it saved.
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await expect(b.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await close(a, b);
});

test("a tick, Enter and Backspace at block edges reach the other side", async ({ browser, baseURL }) => {
  const slug = unique("ulesanded");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate(() => window.editor.replaceMarkdown(`# Ülesanded\n\n- [ ] esimene\n- [ ] teine\n\nLõik\n`));
  const b = await editorFor(browser, baseURL, slug);
  await expect.poll(() => markdown(b)).toContain("- [ ] teine");
  await a.locator("#editor .mu-task-list-checkbox").nth(1).click();
  await expect.poll(() => markdown(b)).toBe(`# Ülesanded\n\n- [ ] esimene\n- [x] teine\n\nLõik\n`);
  await expect(b.locator("#editor .mu-task-list-checkbox").nth(1)).toHaveClass(/mu-checkbox-checked/);

  await caretAtEnd(b, "p", 2);
  await b.keyboard.press("Home");
  await b.keyboard.press("Enter");
  await b.keyboard.press("Backspace");
  await b.keyboard.press("Backspace");
  await expect.poll(async () => (await markdown(a)) === (await markdown(b))).toBe(true);
  expect(await markdown(a)).toBe(`# Ülesanded\n\n- [ ] esimene\n- [x] teineLõik\n`);
  await close(a, b);
});

test("a late joiner gets the unsaved work; a write from outside resets everyone to it", async ({ browser, baseURL }) => {
  const slug = unique("valjast");
  const a = await editorFor(browser, baseURL, slug);
  await caretAtEnd(a, "h1");
  await a.keyboard.type(" salvestamata");
  await expect.poll(() => a.evaluate(() => window.lastState.seq)).toBeGreaterThan(0);
  const b = await editorFor(browser, baseURL, slug);
  expect(await markdown(b)).toBe(`# ${slug} salvestamata\n`);

  // Another writer (a task tick on Täna, a git import) changes the note: rev moves on.
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  db.prepare("UPDATE notes SET body = ?, rev = rev + 1 WHERE slug = ?").run("# Väljast muudetud\n", slug);
  db.close();
  await expect.poll(() => markdown(a), { timeout: 8000 }).toBe("# Väljast muudetud\n");
  await expect.poll(() => markdown(b), { timeout: 8000 }).toBe("# Väljast muudetud\n");
  // And they go on together in the new session.
  await caretAtEnd(a, "h1");
  await a.keyboard.type("!");
  await expect.poll(() => markdown(b)).toBe("# Väljast muudetud!\n");
  await close(a, b);
});
