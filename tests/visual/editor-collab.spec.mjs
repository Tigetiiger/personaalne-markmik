// Live co-editing: two browsers on the same note (ported from Steam's koostoo.spec.mjs).
import { DatabaseSync } from "node:sqlite";

import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";

const DB = `.vt-data-${process.env.VT_PORT || 8780}/test.db`;
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });

// One login per file: /login allows 20 requests a minute per address.
let cookies = null;
async function editorFor(browser, baseURL, slug, { clock = false } = {}) {
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  if (clock) await page.clock.install();
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
  await expect(a.locator(".mt-remote-caret span")).toHaveText(["Omanik"]);
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

test("an op that cannot be applied is reported: the session closes and the editor rejoins", async ({ browser, baseURL }) => {
  const slug = unique("katki");
  const a = await editorFor(browser, baseURL, slug);
  const joins = [];
  a.on("request", (r) => r.url().endsWith("/api/collab/join") && joins.push(r));
  const failed = a.waitForRequest((r) => /\/api\/collab\/\d+\/leave$/.test(r.url()) && Boolean(r.postDataJSON().error));
  const session = await a.evaluate(async (s) => {
    const csrf = document.querySelector('meta[name="csrf"]').content;
    const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": csrf }, body: JSON.stringify(body) }).then((r) => r.json());
    const joined = await post("/api/collab/join", { slug: s, client: "paha" });
    // A path into a block that does not exist: Muya throws applying it.
    await post(`/api/collab/${joined.session}/sync`, { client: "paha", seen: 0, op: [99, "text", { es: [0, "x"] }], op_id: "x1", base: joined.ops.length });
    return joined.session;
  }, slug);
  expect((await failed).url()).toContain(`/api/collab/${session}/leave`);
  await expect.poll(() => joins.length).toBeGreaterThan(0);
  await expect.poll(() => a.evaluate(() => window.editor.getMarkdown())).toBe(`# ${slug}\n`);
  // The new session works.
  await caretAtEnd(a, "h1");
  await a.keyboard.type(" edasi");
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await close(a);
});

test("a tab hidden for a minute saves and leaves; shown again it rejoins", async ({ browser, baseURL }) => {
  const slug = unique("peidus");
  const page = await editorFor(browser, baseURL, slug, { clock: true });
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" enne");
  await expect.poll(() => page.evaluate(() => window.lastState.seq)).toBeGreaterThan(0);
  const visibility = (state) => page.evaluate((state) => {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
  await visibility("hidden");
  const left = page.waitForRequest((r) => /\/api\/collab\/\d+\/leave$/.test(r.url()));
  await page.clock.runFor(61_000);
  await left;
  await expect(page.locator("#status")).toHaveText("Salvestatud");
  const rejoined = page.waitForRequest((r) => r.url().endsWith("/api/collab/join"));
  await visibility("visible");
  await rejoined;
  await expect.poll(() => page.evaluate(() => window.editor.getMarkdown())).toBe(`# ${slug} enne\n`);
  await close(page);
});

const noteBody = (slug) => {
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  const row = db.prepare("SELECT body FROM notes WHERE slug = ?").get(slug);
  db.close();
  return row && row.body;
};

test("leaving the page saves what the autosave had not yet", async ({ browser, baseURL }) => {
  for (const wait of [700, 0]) { // the op on the server already / still on its way
    const slug = unique("lahku");
    const page = await editorFor(browser, baseURL, slug);
    await caretAtEnd(page, "h1");
    await page.keyboard.type(" kirjutatud");
    await page.waitForTimeout(wait);
    await page.goto("/login"); // pagehide
    await expect.poll(() => noteBody(slug), { timeout: 5000 }).toBe(`# ${slug} kirjutatud\n`);
    await close(page);
  }
});

test("typing that never reached an ended session is carried into the new one", async ({ browser, baseURL }) => {
  const slug = unique("vorguta");
  const page = await editorFor(browser, baseURL, slug);
  await page.evaluate((md) => window.editor.replaceMarkdown(md), `# ${slug}\n\n- [ ] üks\n- [ ] kaks\n`);
  await expect(page.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  let offline = true;
  await page.route("**/api/collab/**", (route) => (offline ? route.abort() : route.continue()));
  await page.locator("#editor .mu-editor li p").nth(1).click();
  await page.keyboard.press("End");
  await page.keyboard.type(" VÕRGUTA");
  await page.waitForTimeout(500);
  // Meanwhile the note looks idle to the server, and a task tick on Täna writes it.
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  db.prepare("UPDATE collab_sessions SET last_active_at = '2020-01-01T00:00:00Z'").run();
  db.prepare("UPDATE notes SET body = ?, rev = rev + 1 WHERE slug = ?").run(`# ${slug}\n\n- [x] üks\n- [ ] kaks\n\nServeri lõik\n`, slug);
  db.close();
  offline = false;
  const both = `# ${slug}\n\n- [x] üks\n- [ ] kaks VÕRGUTA\n\nServeri lõik\n`;
  await expect.poll(() => page.evaluate(() => window.editor.getMarkdown()), { timeout: 10_000 }).toBe(both);
  await expect(page.locator("#toast")).toHaveText("Sinu viimased muudatused taastati.");
  await expect.poll(() => noteBody(slug), { timeout: 10_000 }).toBe(both);
  page.errors.splice(0); // the aborted requests
  await close(page);
});

test("a rejoin that keeps failing waits longer each time", async ({ browser, baseURL }) => {
  const slug = unique("torm");
  const page = await editorFor(browser, baseURL, slug);
  let joins = 0;
  await page.route("**/api/collab/join", (route) => {
    joins++;
    route.abort();
  });
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  db.prepare("UPDATE notes SET rev = rev + 1 WHERE slug = ?").run(slug);
  db.close();
  await page.waitForTimeout(8000);
  // 3 s, 6 s, ... after the first attempt: at most three in eight seconds.
  expect(joins).toBeGreaterThan(0);
  expect(joins).toBeLessThanOrEqual(3);
  page.errors.splice(0);
  await close(page);
});

test("a hidden tab whose save fails stays in the session and saves once back online", async ({ browser, baseURL }) => {
  const slug = unique("peidus-vorguta");
  const page = await editorFor(browser, baseURL, slug, { clock: true });
  let offline = true;
  const leaves = [];
  page.on("request", (r) => /\/leave$/.test(r.url()) && leaves.push(r.url()));
  await page.route("**/api/collab/**", (route) => (offline ? route.abort() : route.continue()));
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" võrguta");
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(61_000); // hidden a minute: the save starts, and fails for ten seconds
  await page.clock.runFor(15_000);
  expect(leaves).toEqual([]);
  offline = false;
  await expect.poll(() => noteBody(slug), { timeout: 10_000 }).toBe(`# ${slug} võrguta\n`);
  page.errors.splice(0);
  await close(page);
});
