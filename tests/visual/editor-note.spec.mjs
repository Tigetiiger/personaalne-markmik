// The note editor on its own (tools/editor/note-editor.js through the /_test/editor harness).
import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

// One login per file: /login allows 20 requests a minute per address.
let cookies = null;
async function openHarness(page, slug) {
  if (cookies) {
    await page.context().addCookies(cookies);
    await page.goto(`/_test/editor/${slug}`);
    await waitReady(page);
  } else {
    await login(page, `/_test/editor/${slug}`);
    cookies = await page.context().cookies();
  }
  await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
  await expect(page.locator("#editor .mu-editor")).toBeVisible();
}

const markdown = (page) => page.evaluate(() => window.editor.getMarkdown());
const setMarkdown = (page, md) => page.evaluate((md) => window.editor.replaceMarkdown(md), md);
const floatShown = (page, inner) => page.locator(`body > .mu-float-wrapper:has(.${inner})`).evaluate((el) => getComputedStyle(el).opacity === "1");

/** Caret at the end of the n-th element matching selector inside the editor. */
async function caretAtEnd(page, selector, n = 0) {
  await page.locator(`#editor .mu-editor ${selector}`).nth(n).click();
  await page.keyboard.press("End");
}

async function listenerCounts(page) {
  const cdp = await page.context().newCDPSession(page);
  const count = async (expression) => {
    const { result } = await cdp.send("Runtime.evaluate", { expression });
    return (await cdp.send("DOMDebugger.getEventListeners", { objectId: result.objectId, depth: 0 })).listeners.length;
  };
  const counts = { document: await count("document"), window: await count("window") };
  await cdp.detach();
  return counts;
}

test.beforeEach(async ({ page }) => {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
});
test.afterEach(async ({ page }) => {
  expect(page.errors, "JavaScript errors on the page").toEqual([]);
});

test("mounts, typing changes the markdown, autosave keeps it over a reload", async ({ page }) => {
  const slug = unique("proov");
  await openHarness(page, slug);
  expect(await markdown(page)).toBe(`# ${slug}\n`);
  await caretAtEnd(page, "h1");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Esimene lõik.");
  await expect.poll(() => markdown(page)).toBe(`# ${slug}\n\nEsimene lõik.\n`);
  await expect(page.locator("#status")).toHaveText("Salvestamata");
  // Autosave: 2–3 s after the change reached the server.
  await expect(page.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
  expect(await markdown(page)).toBe(`# ${slug}\n\nEsimene lõik.\n`);
});

test("focusTitle puts the caret at the end of the # heading", async ({ page }) => {
  const slug = unique("pealkiri");
  await openHarness(page, slug);
  await setMarkdown(page, `Sissejuhatus\n\n# ${slug}\n\nTekst\n`);
  await page.evaluate(() => window.editor.focusTitle());
  await page.keyboard.type(" uus");
  await expect.poll(() => markdown(page)).toBe(`Sissejuhatus\n\n# ${slug} uus\n\nTekst\n`);
});

test("switching notes saves the old one, keeps no stale listeners and talks to one session", async ({ page }) => {
  const a = unique("a");
  const b = unique("b");
  const sessions = {};
  const syncs = [];
  page.on("response", async (r) => {
    if (r.url().endsWith("/api/collab/join")) {
      const body = r.request().postDataJSON();
      sessions[body.slug] = (await r.json()).session;
    }
  });
  page.on("request", (r) => {
    const m = r.url().match(/\/api\/collab\/(\d+)\/sync$/);
    if (m) syncs.push({ session: Number(m[1]), op: Boolean(r.postDataJSON().op), at: Date.now() });
  });
  await openHarness(page, b); // creates note b
  await openHarness(page, a);
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" A");
  const before = await listenerCounts(page); // after a first click: Playwright adds its own
  for (const slug of [b, a, b]) await page.evaluate((s) => window.openNoteInHarness(s), slug);
  expect(await markdown(page)).toBe(`# ${b}\n`);
  expect(await listenerCounts(page)).toEqual(before);
  // The switch saved note a with its change.
  const switched = Date.now();
  await page.evaluate((s) => window.openNoteInHarness(s), a);
  expect(await markdown(page)).toBe(`# ${a} A\n`);
  await page.evaluate((s) => window.openNoteInHarness(s), b);

  // One keystroke: exactly one op, to b's session; nothing more goes to a's.
  const t0 = Date.now();
  await caretAtEnd(page, "h1");
  await page.keyboard.type("!");
  await expect.poll(() => markdown(page)).toBe(`# ${b}!\n`);
  await page.waitForTimeout(1500);
  const recent = syncs.filter((s) => s.at >= t0);
  expect(recent.filter((s) => s.op).map((s) => s.session)).toEqual([sessions[b]]);
  expect(recent.every((s) => s.session === sessions[b])).toBe(true);
  expect(syncs.filter((s) => s.at > switched + 1500 && s.session === sessions[a])).toEqual([]);

  // A note that cannot be opened: the caller hears why, the editor stays on (and in) note b.
  await expect(page.evaluate(() => window.openNoteInHarness("puudub-sellist-pole"))).rejects.toThrow(/Märget ei leitud/);
  await expect.poll(() => page.evaluate(() => document.body.dataset.slug)).toBe(b);
  await caretAtEnd(page, "h1");
  await page.keyboard.type("?");
  await expect.poll(() => markdown(page)).toBe(`# ${b}!?\n`);
  await expect(page.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
});

test("Ctrl+click and the hover jump icon open a linked note", async ({ page }) => {
  const slug = unique("lingid");
  await openHarness(page, slug);
  // One link per line: Muya 0.2.0 renders only the last link of a line (reported to the lead).
  await setMarkdown(page, `# ${slug}\n\nVaata [Teine märge](/m/teine-marge) siit.\n\n[välist](https://example.org)\n\nViimane rida.\n`);
  const link = page.locator("#editor span.mu-link", { hasText: "Teine märge" });
  await link.click({ modifiers: ["Control"] });
  await expect.poll(() => page.evaluate(() => window.opened)).toEqual(["teine-marge"]);
  // A plain click edits, it does not follow.
  await caretAtEnd(page, "p", 2);
  await link.click();
  await caretAtEnd(page, "p", 2);
  expect(await page.evaluate(() => window.opened)).toEqual(["teine-marge"]);
  await link.hover();
  const jump = page.locator(".mu-link-tools-container li.item.jump");
  await expect(jump).toBeVisible();
  await jump.click();
  await expect.poll(() => page.evaluate(() => window.opened)).toEqual(["teine-marge", "teine-marge"]);
});

test("[[ opens the note picker; choosing links the note, Loo uus creates one", async ({ page }) => {
  const slug = unique("viited");
  const suggested = [];
  await page.route("**/api/notes/suggest?*", (route) => {
    const q = new URL(route.request().url()).searchParams.get("q");
    suggested.push(q);
    const all = [
      { slug: "matemaatika", title: "Matemaatika", folder: "kool" },
      { slug: "matkad", title: "Matkad [2026]", folder: "" },
    ];
    route.fulfill({ json: all.filter((n) => n.title.toLowerCase().includes(q.toLowerCase())) });
  });
  let created = null;
  await page.route("**/api/notes", (route) => {
    created = route.request().postDataJSON();
    route.fulfill({ status: 201, json: { id: 99, slug: "uus-teema", title: created.title, folder: "", body: "" } });
  });
  await openHarness(page, slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Vt [[mat");
  const picker = page.locator(".mt-wikilink");
  await expect(picker.locator(".mt-wikilink-item")).toHaveText(["Matemaatikakool", "Matkad [2026]", "Loo uus «mat»"]);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(picker).toBeHidden();
  await page.keyboard.type(" ja ");
  await expect.poll(() => markdown(page)).toBe(`# ${slug}\n\nVt [Matkad \\[2026\\]](/m/matkad) ja \n`);
  expect(suggested).toContain("mat");

  await page.keyboard.type("[[Uus teema");
  await picker.locator(".mt-wikilink-item.create").click();
  await expect.poll(() => markdown(page)).toBe(`# ${slug}\n\nVt [Matkad \\[2026\\]](/m/matkad) ja [Uus teema](/m/uus-teema)\n`);
  expect(created).toEqual({ title: "Uus teema" });

  // Escape closes it and the brackets stay text.
  await page.keyboard.type(" [[x");
  await expect(picker).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(picker).toBeHidden();
  await page.keyboard.type("y");
  await expect(picker).toBeHidden();
  await expect.poll(() => markdown(page)).toContain("[[xy]");
});

test("links, tasks with dates and pictures survive the round trip exactly", async ({ page }) => {
  const slug = unique("ring");
  const text = `# ${slug}\n\n[T](/m/slug)\n\n- [ ] tee ülesanded @reede\n- [x] tehtud @3.10\n\n![x](/pildid/abc.png)\n`;
  await openHarness(page, slug);
  await setMarkdown(page, text);
  expect(await markdown(page)).toBe(text);
  await expect(page.locator("#status")).toHaveText("Salvestatud", { timeout: 8000 });
  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
  expect(await markdown(page)).toBe(text);
});

test("a task with @date then Enter does not open the @ menu; @ on its own line does", async ({ page }) => {
  const slug = unique("at");
  await openHarness(page, slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.press("Enter");
  await page.keyboard.type("- [ ] x @reede");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  expect(await floatShown(page, "mu-quick-insert")).toBe(false);
  await expect.poll(() => markdown(page)).toBe(`# ${slug}\n\n- [ ] x @reede\n- [ ] \n`);
  // Control: an "@" alone in a paragraph is the block menu.
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await page.keyboard.type("@");
  await expect.poll(() => floatShown(page, "mu-quick-insert")).toBe(true);
});

test("HTML blocks are previewed sanitised", async ({ page }) => {
  const slug = unique("xss");
  await openHarness(page, slug);
  await setMarkdown(page, `# ${slug}\n\n<div class="kast"><img src="x" onerror="window.__xss=1"><b>paks</b></div>\n\nLõpp\n`);
  const preview = page.locator("#editor .mu-html-preview");
  await expect(preview.locator("b")).toHaveText("paks");
  await expect(preview.locator("img")).toHaveCount(1);
  expect(await preview.locator("img").getAttribute("onerror")).toBeNull();
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test("selection, applyBlockEdit and toggleTask edit single blocks as ops", async ({ page }) => {
  const slug = unique("plokk");
  await openHarness(page, slug);
  await setMarkdown(page, `# ${slug}\n\nüks\n\nkaks\n\n- [ ] a\n- [ ] b\n`);
  await page.locator("#editor .mu-editor p", { hasText: "kaks" }).click();
  const sel = await page.evaluate(() => (window.sel = window.editor.selection()));
  expect(sel.text).toBe("kaks");
  // A block inserted above moves "kaks"; the captured path follows it.
  await setMarkdown(page, `# ${slug}\n\nuus\n\nüks\n\nkaks\n\n- [ ] a\n- [ ] b\n`);
  expect(await page.evaluate(() => window.editor.applyBlockEdit(window.sel.path, "KAKS"))).toBe(true);
  expect(await markdown(page)).toBe(`# ${slug}\n\nuus\n\nüks\n\nKAKS\n\n- [ ] a\n- [ ] b\n`);
  // Changed since the capture: refused.
  await page.locator("#editor .mu-editor p", { hasText: "üks" }).click();
  await page.evaluate(() => (window.sel = window.editor.selection()));
  await page.keyboard.press("End");
  await page.keyboard.type("!");
  expect(await page.evaluate(() => window.editor.applyBlockEdit(window.sel.path, "ÜKS"))).toBe(false);

  await page.locator("#editor .mu-editor li p", { hasText: "b" }).click();
  const task = await page.evaluate(() => window.editor.selection());
  expect(await page.evaluate((p) => window.editor.toggleTask(p, true), task.path)).toBe(true);
  expect(await markdown(page)).toBe(`# ${slug}\n\nuus\n\nüks!\n\nKAKS\n\n- [ ] a\n- [x] b\n`);
  await expect(page.locator("#editor .mu-task-list-checkbox").nth(1)).toHaveClass(/mu-checkbox-checked/);
  // Ctrl+Z undoes it like any edit.
  await page.keyboard.press("Control+z");
  await expect.poll(() => markdown(page)).toBe(`# ${slug}\n\nuus\n\nüks!\n\nKAKS\n\n- [ ] a\n- [ ] b\n`);
});

test("@ menu Image opens the gallery; an uploaded picture goes into the text", async ({ page }) => {
  const slug = unique("pilt");
  await openHarness(page, slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.press("Enter");
  await page.keyboard.type("@");
  const image = page.locator(".mu-quick-insert .item", { hasText: "Image" });
  await expect(image).toBeVisible();
  await image.click();
  const gallery = page.locator("#editor dialog.mt-gallery");
  await expect(gallery).toHaveJSProperty("open", true);
  await gallery.locator("input[type=file]").setInputFiles({ name: "robot.png", mimeType: "image/png", buffer: PNG });
  await expect(gallery).toHaveJSProperty("open", false);
  await expect.poll(() => markdown(page)).toMatch(new RegExp(`^# ${slug}\\n\\n!\\[robot\\]\\(/pildid/[0-9a-f]{40}\\.png\\)\\n$`));
  await expect(page.locator("#editor .mu-editor img")).toHaveJSProperty("naturalWidth", 1);
});

test("read-only follows along but neither types nor sends", async ({ page }) => {
  const slug = unique("vaade");
  const ops = [];
  page.on("request", (r) => /\/sync$/.test(r.url()) && r.postDataJSON().op && ops.push(r));
  await openHarness(page, slug);
  await page.evaluate((s) => window.openNoteInHarness(s, { readOnly: true }), slug);
  await expect(page.locator("#editor .mt-bar")).toBeHidden();
  await page.locator("#editor .mu-editor h1").click();
  await page.keyboard.press("End");
  await page.keyboard.type("x");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(500);
  expect(await markdown(page)).toBe(`# ${slug}\n`);
  expect(ops).toEqual([]);
});

test("detach saves, leaves and takes the editor away", async ({ page }) => {
  const slug = unique("lahku");
  const requests = [];
  page.on("request", (r) => r.url().includes("/api/collab/") && requests.push({ url: r.url(), at: Date.now() }));
  await openHarness(page, slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" viimane");
  await page.evaluate(() => window.editor.detach());
  await expect(page.locator("#editor")).toBeEmpty();
  await expect(page.locator("body > .mu-float-wrapper")).toHaveCount(0);
  await expect.poll(() => requests.some((r) => r.url.endsWith("/leave"))).toBe(true);
  const after = Date.now();
  await page.waitForTimeout(1500);
  expect(requests.filter((r) => r.at > after + 200)).toEqual([]);
  // The last change was saved on the way out.
  await openHarness(page, slug);
  expect(await markdown(page)).toBe(`# ${slug} viimane\n`);
});
