// Live co-editing: two browsers on the same note (ported from Steam's koostoo.spec.mjs).
import { createHash } from "node:crypto";
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
    // Saved after all: the copy kept just in case is not offered.
    await page.goto(`/_test/editor/${slug}`);
    await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
    await expect(page.locator("#editor .mt-copy")).toHaveCount(0);
    expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("markmik:koopia:")))).toEqual([]);
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const outsideWrite = (slug, body) => {
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  db.prepare("UPDATE collab_sessions SET last_active_at = '2020-01-01T00:00:00Z'").run();
  db.prepare("UPDATE notes SET body = ?, rev = rev + 1 WHERE slug = ?").run(body, slug);
  db.close();
};

test("a tab leaving with an op on its way never saves over a co-editor's op", async ({ browser, baseURL }) => {
  const slug = unique("klobber");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate((m) => window.editor.replaceMarkdown(m), `# ${slug}\n\nA lõik\n\nB lõik\n`);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  const b = await editorFor(browser, baseURL, slug);
  let hold = true; // A hears nothing from the server
  await a.route("**/api/collab/*/sync", (route) => (hold && route.request().resourceType() === "fetch" ? route.abort() : route.continue()));
  await caretAtEnd(b, "p", 1);
  await b.keyboard.type(" BBB");
  await sleep(1500); // B's op on the server
  await caretAtEnd(a, "p", 0);
  await a.keyboard.type(" AAA");
  await sleep(300);
  hold = false;
  await a.goto("/login"); // pagehide: the last save carries A's op, which cannot get the next place
  await expect(b.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await sleep(1000);
  expect(noteBody(slug)).toContain("B lõik BBB");
  a.errors.splice(0);
  await close(a, b);
});

test("changes queued behind the op on its way are saved on pagehide and the next open starts from them", async ({ browser, baseURL }) => {
  const slug = unique("jarjekord");
  const a = await editorFor(browser, baseURL, slug);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await a.route("**/api/collab/*/sync", async (route) => {
    if (route.request().resourceType() === "fetch") await sleep(4000);
    return route.continue().catch(() => {});
  });
  await caretAtEnd(a, "h1");
  await a.keyboard.type(" eka", { delay: 30 });
  await sleep(400); // on its way (held)
  await a.keyboard.type(" TEINE", { delay: 30 }); // queued
  await sleep(100);
  await a.goto("/login");
  await expect.poll(() => noteBody(slug), { timeout: 5000 }).toBe(`# ${slug} eka TEINE\n`);
  // The copy kept at pagehide is dropped: the server says the final save was written.
  await a.unroute("**/api/collab/*/sync");
  await a.goto(`/_test/editor/${slug}`);
  await expect(a.locator("body")).toHaveAttribute("data-editor", "ready");
  await expect(a.locator("#editor .mt-copy")).toHaveCount(0);
  expect(await a.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("markmik:koopia:")))).toEqual([]);
  const b = await editorFor(browser, baseURL, slug); // within GONE_S of A leaving
  expect(await markdown(b)).toBe(`# ${slug} eka TEINE\n`);
  await caretAtEnd(b, "h1");
  await b.keyboard.type(" kolmas");
  await expect.poll(() => noteBody(slug), { timeout: 8000 }).toBe(`# ${slug} eka TEINE kolmas\n`);
  a.errors.splice(0);
  await close(a, b);
});

test("a change too large to share is kept in the browser, no reset loop, offered back", async ({ browser, baseURL }) => {
  const slug = unique("suur");
  const a = await editorFor(browser, baseURL, slug);
  const joins = [];
  const refused = [];
  a.on("request", (r) => /\/join$/.test(r.url()) && joins.push(r));
  a.on("response", (r) => r.status() === 413 && refused.push(r));
  // One block over the 64 KB op limit (typed into one paragraph, Muya itself takes half a minute).
  const code = "```\n" + `${"x".repeat(99)}\n`.repeat(670) + "```\n";
  await a.evaluate((md) => window.editor.replaceMarkdown(md), `# ${slug}\n\n${code}`);
  await expect(a.locator("#toast")).toContainText("liiga suur");
  await sleep(6000);
  expect(refused.length).toBe(1);
  expect(joins.length).toBe(1);
  expect(await markdown(a)).toBe(`# ${slug}\n`);
  const bar = a.locator("#editor .mt-copy");
  await expect(bar).toContainText("liiga suur");
  await bar.getByRole("button", { name: "Loobu" }).click();
  await expect(bar).toHaveCount(0);
  expect(await a.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("markmik:koopia:")))).toEqual([]);
  a.errors.splice(0);
  await close(a);
});

test("typing while the note cannot be rejoined shows unsaved and is kept for the next open", async ({ browser, baseURL }) => {
  const slug = unique("kinni");
  const a = await editorFor(browser, baseURL, slug);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await a.route("**/api/collab/join", (route) => route.fulfill({ status: 429, contentType: "application/json", body: '{"error":"Liiga palju avatud aknaid."}' }));
  outsideWrite(slug, `# ${slug}\n\nväljast\n`);
  await expect(a.locator("#toast")).toContainText("Märget ei saanud uuesti avada", { timeout: 8000 });
  await caretAtEnd(a, "h1");
  await a.keyboard.type(" KADUNUD");
  await expect(a.locator("#status")).toHaveText("Salvestamata");
  await a.goto("/login"); // pagehide with no session: the text stays in this browser
  await a.unroute("**/api/collab/join");
  await a.goto(`/_test/editor/${slug}`);
  await expect(a.locator("body")).toHaveAttribute("data-editor", "ready");
  expect(await markdown(a)).toBe(`# ${slug}\n\nväljast\n`);
  // Merged into what the note became, not put in its place.
  await a.locator("#editor .mt-copy").getByRole("button", { name: "Taasta" }).click();
  await expect.poll(() => markdown(a)).toBe(`# ${slug} KADUNUD\n\nväljast\n`);
  await expect.poll(() => noteBody(slug), { timeout: 8000 }).toBe(`# ${slug} KADUNUD\n\nväljast\n`);
  await expect(a.locator("#editor .mt-copy")).toHaveCount(0);
  a.errors.splice(0);
  await close(a);
});

test("carried-over typing lands in its own block, and an op already saved is not carried twice", async ({ browser, baseURL }) => {
  const slug = unique("plokk");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate((m) => window.editor.replaceMarkdown(m), `# ${slug}\n\nesimene\n\nteine\n`);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  // A's op reaches the server but its answer is lost; A then hears nothing.
  let mode = "lose";
  await a.route("**/api/collab/*/sync", async (route) => {
    const op = route.request().postDataJSON().op;
    if (mode === "lose" && op) {
      await route.fetch();
      mode = "offline";
      return route.abort();
    }
    return mode === "offline" ? route.abort() : route.continue();
  });
  await caretAtEnd(a, "p", 0);
  await a.keyboard.insertText(" MINU");
  await sleep(800);
  // A co-editor saves it; then an outside write puts a block before "esimene" and drops "teine".
  const b = await editorFor(browser, baseURL, slug);
  await expect(b.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  expect(noteBody(slug)).toBe(`# ${slug}\n\nesimene MINU\n\nteine\n`);
  await close(b);
  outsideWrite(slug, `# ${slug}\n\nUUS\n\nesimene MINU\n`);
  mode = "online";
  await expect.poll(() => markdown(a), { timeout: 10_000 }).toBe(`# ${slug}\n\nUUS\n\nesimene MINU\n`);
  await sleep(3000);
  expect(await markdown(a)).toBe(`# ${slug}\n\nUUS\n\nesimene MINU\n`);
  a.errors.splice(0);
  await close(a);
});

test("a view guest that cannot apply an op rejoins with growing waits, without 403s", async ({ browser, baseURL }) => {
  const slug = unique("vaataja");
  const owner = await editorFor(browser, baseURL, slug);
  const token = `test-${slug}`;
  const db = new DatabaseSync(DB);
  db.exec("PRAGMA busy_timeout = 5000");
  const note = db.prepare("SELECT id FROM notes WHERE slug = ?").get(slug);
  db.prepare("INSERT INTO shares (token_hash, note_id, mode) VALUES (?, ?, 'view')").run(createHash("sha256").update(token).digest("hex"), note.id);
  db.close();
  await owner.goto("/login"); // the owner's editor is gone; only the viewer is in the session
  const viewer = await (await browser.newContext({ baseURL })).newPage();
  await viewer.goto(`/_test/guest/${token}`);
  await expect(viewer.locator("body")).toHaveAttribute("data-editor", "ready");
  await viewer.evaluate((s) => window.openNoteInHarness(s, { readOnly: true }), slug);
  const joins = [];
  const refused = [];
  viewer.on("request", (r) => /\/join$/.test(r.url()) && joins.push(r));
  viewer.on("response", (r) => r.status() === 403 && refused.push(r.url()));
  await owner.evaluate(async (s) => {
    const csrf = document.querySelector('meta[name="csrf"]').content;
    const post = (u, b) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": csrf }, body: JSON.stringify(b) }).then((r) => r.json());
    const j = await post("/api/collab/join", { slug: s, client: "paha" });
    await post(`/api/collab/${j.session}/sync`, { client: "paha", seen: 0, op: [99, "text", { es: [0, "x"] }], op_id: "x1", base: j.ops.length });
  }, slug);
  await sleep(8000);
  expect(refused).toEqual([]);
  expect(joins.length).toBeGreaterThan(0);
  expect(joins.length).toBeLessThanOrEqual(3); // at once, after 3 s, after 9 s
  await close(owner);
  await viewer.context().close();
});


const copies = (page) => page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("markmik:koopia:")));

test("the last keystrokes of a tab whose final save is refused are kept and merged back", async ({ browser, baseURL }) => {
  const slug = unique("klobls");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate((m) => window.editor.replaceMarkdown(m), `# ${slug}\n\nA lõik\n\nB lõik\n`);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  const b = await editorFor(browser, baseURL, slug);
  let hold = true;
  await a.route("**/api/collab/*/sync", (route) => (hold && route.request().resourceType() === "fetch" ? route.abort() : route.continue()));
  await caretAtEnd(b, "p", 1);
  await b.keyboard.type(" BBB");
  await sleep(1500);
  await caretAtEnd(a, "p", 0);
  await a.keyboard.type(" AAA");
  await sleep(300);
  hold = false;
  await a.goto("/login"); // its save is refused: B's op took the place
  await expect(b.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  expect(noteBody(slug)).toBe(`# ${slug}\n\nA lõik\n\nB lõik BBB\n`);
  expect(await copies(a)).toHaveLength(1);
  await a.unroute("**/api/collab/*/sync");
  await a.goto(`/_test/editor/${slug}`);
  await expect(a.locator("body")).toHaveAttribute("data-editor", "ready");
  await a.locator("#editor .mt-copy").getByRole("button", { name: "Taasta" }).click();
  await expect.poll(() => markdown(a)).toBe(`# ${slug}\n\nA lõik AAA\n\nB lõik BBB\n`);
  await expect.poll(() => markdown(b)).toBe(`# ${slug}\n\nA lõik AAA\n\nB lõik BBB\n`);
  expect(await copies(a)).toEqual([]);
  a.errors.splice(0);
  await close(a, b);
});

test("switching away from a note that cannot be rejoined keeps what was typed in it", async ({ browser, baseURL }) => {
  const slug = unique("vaheta");
  const other = unique("teine");
  const a = await editorFor(browser, baseURL, other); // creates the other note
  await a.goto(`/_test/editor/${slug}`);
  await expect(a.locator("body")).toHaveAttribute("data-editor", "ready");
  await a.evaluate((m) => window.editor.replaceMarkdown(m), `# ${slug}\n\nalgus\n`);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await a.route("**/api/collab/join", (route) => (route.request().postDataJSON().slug === slug
    ? route.fulfill({ status: 429, contentType: "application/json", body: '{"error":"Liiga palju avatud aknaid."}' })
    : route.continue()));
  outsideWrite(slug, `# ${slug}\n\nalgus\n\nväljast\n`);
  await expect(a.locator("#toast")).toContainText("Märget ei saanud uuesti avada", { timeout: 8000 });
  await caretAtEnd(a, "p", 0);
  await a.keyboard.type(" KADUNUD");
  await a.evaluate((s) => window.openNoteInHarness(s), other);
  expect(await markdown(a)).toBe(`# ${other}\n`);
  expect(await copies(a)).toHaveLength(1);
  await a.unroute("**/api/collab/join");
  await a.evaluate((s) => window.openNoteInHarness(s), slug);
  await a.locator("#editor .mt-copy").getByRole("button", { name: "Taasta" }).click();
  await expect.poll(() => markdown(a)).toBe(`# ${slug}\n\nalgus KADUNUD\n\nväljast\n`);
  a.errors.splice(0);
  await close(a);
});

test("an old kept copy is merged into the note's newer text, and dropped only once applied", async ({ browser, baseURL }) => {
  const slug = unique("vana");
  const a = await editorFor(browser, baseURL, slug);
  await a.evaluate((m) => window.editor.replaceMarkdown(m), `# ${slug}\n\nüks\n`);
  await expect(a.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await a.route("**/api/collab/join", (route) => route.fulfill({ status: 429, contentType: "application/json", body: '{"error":"x"}' }));
  outsideWrite(slug, `# ${slug}\n\nüks\n`);
  await expect(a.locator("#toast")).toContainText("Märget ei saanud uuesti avada", { timeout: 8000 });
  await caretAtEnd(a, "p", 0);
  await a.keyboard.type(" MINU");
  await a.goto("/login");
  outsideWrite(slug, `# ${slug}\n\nüks\n\nTEISE INIMESE UUS LÕIK\n`);
  await a.unroute("**/api/collab/join");
  await a.goto(`/_test/editor/${slug}`);
  await expect(a.locator("#editor .mt-copy")).toBeVisible();
  // Reloaded without choosing: still offered.
  await a.reload();
  await expect(a.locator("#editor .mt-copy")).toBeVisible();
  await a.locator("#editor .mt-copy").getByRole("button", { name: "Taasta" }).click();
  await expect.poll(() => markdown(a)).toBe(`# ${slug}\n\nüks MINU\n\nTEISE INIMESE UUS LÕIK\n`);
  await expect.poll(() => noteBody(slug), { timeout: 8000 }).toBe(`# ${slug}\n\nüks MINU\n\nTEISE INIMESE UUS LÕIK\n`);
  expect(await copies(a)).toEqual([]);
  a.errors.splice(0);
  await close(a);
});
