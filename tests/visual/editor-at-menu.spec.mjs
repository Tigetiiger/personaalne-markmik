// The "@" menu's own items (Image, AI) empty the "@" line with a text edit. While the op before it
// was still on its way, that edit and the queued "@" cancelled out into a null op, which the
// server never acknowledges: nothing typed afterwards was sent or saved (tools/editor/collab.js
// enqueue). A letter typed and deleted meanwhile did the same.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

// One login per file: /login allows 20 requests a minute per address.
let cookies = null;
async function open(page, path, ready) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  if (cookies) {
    await page.context().addCookies(cookies);
    await page.goto(path);
    await waitReady(page);
  } else {
    await login(page, path);
    cookies = await page.context().cookies();
  }
  await expect(page.locator("body")).toHaveAttribute(...ready);
}

/** A new paragraph under the title, its op held on the way to the server until release(). */
async function newLineWhileSending(page) {
  let release;
  const released = new Promise((resolve) => (release = resolve));
  let sent;
  const held = new Promise((resolve) => (sent = resolve));
  let first = true;
  await page.route(/\/api\/collab\/\d+\/sync$/, async (route) => {
    if (first && route.request().postDataJSON().op) {
      first = false;
      sent();
      await released;
    }
    await route.continue();
  });
  await page.locator("#editor .mu-editor h1").click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await held;
  return release;
}

async function typeAndExpectSaved(page, slug, release) {
  release();
  await page.locator("#editor .mu-editor p").last().click();
  await page.keyboard.type("tekst");
  await expect.poll(async () => (await api(page, `/api/notes/${slug}`)).data.body, { timeout: 10_000 }).toBe(`# ${slug}\n\ntekst\n`);
  expect(page.errors, "JavaScript errors on the page").toEqual([]);
}

test("@ menu Image picked with the mouse: what is typed after it is saved", async ({ page }) => {
  const slug = unique("at-pilt");
  await open(page, `/_test/editor/${slug}`, ["data-editor", "ready"]);
  const release = await newLineWhileSending(page);
  await page.keyboard.type("@");
  await page.locator('.mu-quick-insert .item[data-label="image"]').click();
  const gallery = page.locator("#editor dialog.mt-gallery");
  await expect(gallery).toHaveJSProperty("open", true);
  await page.keyboard.press("Escape");
  await expect(gallery).toHaveJSProperty("open", false);
  await typeAndExpectSaved(page, slug, release);
});

test("@ menu AI picked with the keyboard: what is typed after it is saved", async ({ page }) => {
  const slug = unique("at-ai");
  await open(page, `/_test/ai/${slug}`, ["data-ai", "ready"]);
  const release = await newLineWhileSending(page);
  await page.keyboard.type("@");
  const items = page.locator(".mu-quick-insert .item");
  await expect(items.first()).toBeVisible();
  const at = (await items.evaluateAll((els) => els.map((el) => el.dataset.label))).indexOf("ai");
  for (let i = 0; i < at; i++) await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  const box = page.locator(".ai-inline");
  await expect(box).toBeVisible();
  await box.getByLabel("Juhis AI-le").press("Escape");
  await expect(box).toBeHidden();
  await typeAndExpectSaved(page, slug, release);
});

test("a letter typed and deleted while the op before it is on its way", async ({ page }) => {
  const slug = unique("at-tagasi");
  await open(page, `/_test/editor/${slug}`, ["data-editor", "ready"]);
  const release = await newLineWhileSending(page);
  // getMarkdown hands each keystroke's op to collab on its own, as separate frames would.
  const markdown = () => page.evaluate(() => window.editor.getMarkdown());
  await page.keyboard.type("a");
  await expect.poll(markdown).toBe(`# ${slug}\n\na\n`);
  await page.keyboard.press("Backspace");
  await expect.poll(markdown).toBe(`# ${slug}\n\n\n`); // the empty paragraph
  await typeAndExpectSaved(page, slug, release);
});
