// AI edits reviewed as a diff (docs/AI-REVIEW-PLAN.md), with the fake model (AI_FAKE=1): the
// Abiline's muuda_markmet opens the review window; each change kept or left out; Aktsepteeri puts
// the kept ones in as ordinary edits (co-editors see them, Ctrl+Z undoes them), onto the note as
// it is by then; Loobu and Esc change nothing.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });
// The harness page lays the panel out in the desktop's right pane, which a phone hides; the phone
// width of the window itself is checked by resizing below.
test.skip(({ isMobile }) => isMobile, "desktop harness layout");

let cookies = null;
async function editorPage(browser, baseURL, slug) {
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  if (cookies) {
    await context.addCookies(cookies);
    await page.goto(`/_test/ai/${slug}`);
    await waitReady(page);
  } else {
    await login(page, `/_test/ai/${slug}`);
    cookies = await context.cookies();
  }
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  return page;
}

/** A note with `md`, saved (the chat's note context is the editor's text either way). */
async function withNote(browser, baseURL, md) {
  const slug = unique("ulevaade");
  const page = await editorPage(browser, baseURL, slug);
  await page.evaluate((md) => window.editor.replaceMarkdown(md), md);
  await expect.poll(() => markdown(page)).toBe(md);
  return { page, slug };
}

const markdown = (page) => page.evaluate(() => window.editor.getMarkdown());
const fake = (page, script) => api(page, "/_test/ai-fake", { method: "POST", body: script });
const dialog = (page) => page.getByRole("dialog", { name: "Vaata muudatused üle" });

/** Ask the Abiline; the fake edits the note as `edit` says. Returns the review window. */
async function askEdit(page, edit, message = "Muuda märget") {
  await fake(page, { reply: "Tegin muudatused.", edit });
  await page.getByLabel("Sõnum Abilisele").fill(message);
  await page.getByLabel("Sõnum Abilisele").press("Enter");
  await expect(page.locator(".ai-assistant").last()).toHaveText("Tegin muudatused.");
  await expect(dialog(page)).toBeVisible();
  return dialog(page);
}

async function done(...pages) {
  await fake(pages[0], {});
  for (const page of pages) {
    expect(page.errors, "JavaScript errors on the page").toEqual([]);
    await page.context().close();
  }
}

test("one of two changes left out: the note gets only the other, and keeps its one title", async ({ browser, baseURL }) => {
  const { page, slug } = await withNote(browser, baseURL, "# Märge\n\nKontrolltöö on reedel.\n\nTeine lõik.\n");
  const win = await askEdit(page, { replace: [["reedel", "neljapäeval"]], append: "Õpi valemid." });
  const changes = win.locator(".ai-review-change");
  await expect(changes).toHaveCount(2);
  // The model kept the title it was shown: one title in the window, none added.
  await expect(win.locator("h1")).toHaveText(["Märge"]);
  await expect(changes.nth(0).locator("del.ai-del")).toHaveText("reedel");
  await expect(changes.nth(0).locator("ins.ai-ins")).toHaveText("neljapäeval");
  await expect(changes.nth(1).locator(".ai-review-ins")).toHaveText("Õpi valemid.");
  await expect(changes.nth(1).locator(".ai-review-del")).toHaveCount(0);

  await changes.nth(1).getByRole("button", { name: "Jäta ära" }).click();
  await expect(changes.nth(1)).toHaveClass(/rejected/);
  await expect(changes.nth(1).getByRole("button", { name: "Taasta" })).toBeVisible();
  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(win).toBeHidden();
  await expect.poll(() => markdown(page)).toBe("# Märge\n\nKontrolltöö on neljapäeval.\n\nTeine lõik.\n");
  await expect(page.locator(".ai-proposal")).toContainText("Rakendatud");
  await expect(page.locator(".ai-proposal").getByRole("button")).toHaveCount(0);
  // An ordinary edit: saved by the editor like typing.
  await expect.poll(() => api(page, `/api/notes/${slug}`).then((r) => r.data.body)).toContain("neljapäeval");
  await expect(page.locator(".ai-review")).toHaveCount(0); // removed, not only hidden
  await done(page);
});

test("the accepted change reaches a co-editor, and Ctrl+Z undoes it", async ({ browser, baseURL }) => {
  const md = "# Koos\n\nEsimene lõik.\n";
  const { page, slug } = await withNote(browser, baseURL, md);
  const other = await editorPage(browser, baseURL, slug);
  await expect.poll(() => markdown(other)).toBe(md);
  const win = await askEdit(page, { replace: [["Esimene", "Muudetud"]] });
  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect.poll(() => markdown(page)).toBe("# Koos\n\nMuudetud lõik.\n");
  await expect.poll(() => markdown(other)).toBe("# Koos\n\nMuudetud lõik.\n");

  await page.locator("#editor .mu-editor h1").click();
  await page.keyboard.press("Control+z");
  await expect.poll(() => markdown(page)).toBe(md);
  await expect.poll(() => markdown(other)).toBe(md);
  await done(page, other);
});

test("the note changed during review: the untouched change goes in, the other is reported", async ({ browser, baseURL }) => {
  const md = "# Vahepeal\n\nEsimene lõik.\n\nKeskmine.\n\nTeine lõik.\n";
  const { page, slug } = await withNote(browser, baseURL, md);
  const other = await editorPage(browser, baseURL, slug);
  await expect.poll(() => markdown(other)).toBe(md);
  const win = await askEdit(page, { replace: [["Esimene lõik.", "Esimene, AI."], ["Teine lõik.", "Teine, AI."]] });
  await expect(win.locator(".ai-review-change")).toHaveCount(2);

  await other.locator("#editor .mu-editor p", { hasText: "Esimene lõik." }).click();
  await other.keyboard.press("End");
  await other.keyboard.type(" Käsitsi.");
  await expect.poll(() => markdown(page)).toContain("Esimene lõik. Käsitsi.");

  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(page.locator("#toast")).toHaveText("Tekst muutus vahepeal, see jäi muutmata: „Esimene lõik.“");
  const after = "# Vahepeal\n\nEsimene lõik. Käsitsi.\n\nKeskmine.\n\nTeine, AI.\n";
  await expect.poll(() => markdown(page)).toBe(after);
  await expect.poll(() => markdown(other)).toBe(after);
  await expect(page.locator(".ai-proposal")).toContainText("Rakendatud");
  await done(page, other);
});

test("Loobu and Esc leave the note as it was; Vaata üle opens the window again", async ({ browser, baseURL }) => {
  const md = "# Loobu\n\nSee jääb.\n";
  const { page } = await withNote(browser, baseURL, md);
  const win = await askEdit(page, { append: "Seda ei tule." });
  await page.keyboard.press("Escape");
  await expect(win).toBeHidden();
  expect(await markdown(page)).toBe(md);

  const view = page.locator(".ai-proposal").getByRole("button", { name: "Vaata üle" });
  await view.click();
  await expect(win.locator(".ai-review-ins")).toHaveText("Seda ei tule.");
  await win.getByRole("button", { name: "Loobu" }).click();
  await expect(win).toBeHidden();
  expect(await markdown(page)).toBe(md);
  await expect(view).toBeVisible(); // still open to review
  await expect(page.locator(".ai-review")).toHaveCount(0); // removed, not only hidden

  // Everything left out, then accepted: nothing changes, and the proposal is settled.
  await view.click();
  await win.getByRole("button", { name: "Jäta ära" }).click();
  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(page.locator(".ai-proposal")).toContainText("Loobutud");
  expect(await markdown(page)).toBe(md);
  await done(page);
});

test("keyboard: focus starts on Aktsepteeri, Tab stays in the window, Enter accepts", async ({ browser, baseURL }) => {
  const { page } = await withNote(browser, baseURL, "# Klaviatuur\n\nÜks.\n\nKaks.\n\nKolm.\n\nNeli.\n");
  const win = await askEdit(page, { replace: [["Üks.", "Üks muudetud."], ["Neli.", "Neli muudetud."]] });
  const focused = () => page.evaluate(() => (document.activeElement.closest("dialog") ? document.activeElement.textContent.trim() : null));
  await expect.poll(focused).toBe("Aktsepteeri");
  await page.keyboard.press("Tab");
  expect(await focused()).toBe("Loobu");
  await page.keyboard.press("Tab");
  expect(await focused()).toBe("✕ Jäta ära");
  await page.keyboard.press("Shift+Tab");
  expect(await focused()).toBe("Loobu");
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await focused()).not.toBeNull();
  }
  // Space on a change's button leaves it out; Enter on Aktsepteeri accepts the rest.
  await win.locator(".ai-review-toggle").first().focus();
  await page.keyboard.press("Space");
  await expect(win.locator(".ai-review-change").first()).toHaveClass(/rejected/);
  await win.getByRole("button", { name: "Aktsepteeri" }).focus();
  await page.keyboard.press("Enter");
  await expect(win).toBeHidden();
  await expect.poll(() => markdown(page)).toBe("# Klaviatuur\n\nÜks.\n\nKaks.\n\nKolm.\n\nNeli muudetud.\n");
  await done(page);
});

test("the window: formatted blocks, word changes, whole blocks where words cannot be marked, folds, dark mode, phone width", async ({ browser, baseURL }) => {
  const paras = Array.from({ length: 8 }, (_, i) => `Lõik ${i + 1}.`);
  const md = `# Vormistus\n\n${paras.join("\n\n")}\n\n[vana link](https://a.example/) ja **rasvane** tekst.\n\n- üks\n- kaks\n`;
  const { page } = await withNote(browser, baseURL, md);
  await page.evaluate(() => (document.documentElement.dataset.teema = "tume"));
  const win = await askEdit(page, {
    replace: [["Lõik 2.", "Lõik kaks."], ["[vana link](https://a.example/)", "[uus link](https://b.example/)"], ["- kaks", "- kaks **ja kolm**"]],
  });
  const changes = win.locator(".ai-review-change");
  await expect(changes).toHaveCount(3);
  // Rendered, not source: the title, bold text, list items; unchanged blocks next to a change
  // shown, a longer stretch folded.
  await expect(win.locator("h1")).toHaveText("Vormistus");
  await expect(win.locator(".ai-review-same")).toHaveText(["Vormistus", "Lõik 1.", "Lõik 3.", "Lõik 8.", "üks"]);
  await expect(win.locator(".ai-review-fold")).toHaveText("… 4 plokki muutmata …");
  await expect(changes.nth(0).locator("del.ai-del")).toHaveText("2");
  await expect(changes.nth(0).locator("ins.ai-ins")).toHaveText("kaks");
  // The link's address changed inside the link: old block red, new block green, each whole.
  const link = changes.nth(1);
  await expect(link.locator(".ai-review-del a")).toHaveText("vana link");
  await expect(link.locator(".ai-review-ins a")).toHaveText("uus link");
  await expect(link.locator(".ai-review-del strong")).toHaveText("rasvane");
  await expect(link.locator(".ai-del, .ai-ins")).toHaveCount(0);
  await expect(win.locator("a[href]")).toHaveCount(0); // links go nowhere from the window
  // The list item's new words, bold ones included, are marked inside the item.
  await expect(changes.nth(2).locator("li ins.ai-ins strong")).toHaveText("ja kolm");

  const style = (locator, prop) => locator.evaluate((node, prop) => getComputedStyle(node)[prop], prop);
  expect(await style(changes.nth(0).locator("del.ai-del"), "textDecorationLine")).toBe("line-through");
  expect(await style(win, "backgroundColor")).toBe("rgb(21, 24, 30)"); // --surface, dark
  expect(await style(changes.nth(0).locator("ins.ai-ins"), "color")).toBe("rgb(95, 211, 141)"); // --ok, dark
  expect(await style(changes.nth(0).locator("del.ai-del"), "color")).toBe("rgb(242, 122, 111)"); // --danger, dark

  await page.setViewportSize({ width: 390, height: 700 });
  const box = await win.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeLessThanOrEqual(700);
  await expect(win.getByRole("button", { name: "Aktsepteeri" })).toBeInViewport();
  expect(await win.locator(".ai-review-doc").evaluate((d) => d.scrollWidth <= d.clientWidth)).toBe(true);
  await win.getByRole("button", { name: "Loobu" }).click();
  expect(await markdown(page)).toBe(md);
  await done(page);
});

test("the Abiline changes only the open note; after a reload an open proposal can still be reviewed", async ({ browser, baseURL }) => {
  const { page, slug } = await withNote(browser, baseURL, "# Ainult see\n\nTekst.\n");
  await fake(page, { reply: "Ei saa.", tool: { name: "muuda_markmet", input: { slug: "muu-marge", markdown: "# Muu\n\nsaladus" } } });
  await page.getByLabel("Sõnum Abilisele").fill("Muuda teist märget");
  await page.getByLabel("Sõnum Abilisele").press("Enter");
  await expect(page.locator(".ai-assistant").last()).toHaveText("Ei saa.");
  await expect(page.locator(".ai-proposal")).toHaveCount(0);
  await expect(dialog(page)).toHaveCount(0);

  let win = await askEdit(page, { append: "Lisatud." });
  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect.poll(() => api(page, `/api/notes/${slug}`).then((r) => r.data.body)).toContain("Lisatud.");
  win = await askEdit(page, { append: "Hiljem." });
  await win.getByRole("button", { name: "Loobu" }).click();

  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  await expect(page.locator(".ai-assistant")).toHaveCount(3);
  const cards = page.locator(".ai-proposal");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText("Rakendatud");
  await expect(cards.nth(0).getByRole("button")).toHaveCount(0); // applied once, never again
  await expect(dialog(page)).toHaveCount(0); // a reload opens nothing by itself
  await cards.nth(1).getByRole("button", { name: "Vaata üle" }).click();
  await expect(win.locator(".ai-review-ins")).toHaveText("Hiljem.");
  await win.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect.poll(() => markdown(page)).toBe("# Ainult see\n\nTekst.\n\nLisatud.\n\nHiljem.\n");
  await expect(cards.nth(1)).toContainText("Rakendatud");
  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  await expect(page.locator(".ai-proposal", { hasText: "Rakendatud" })).toHaveCount(2);
  await done(page);
});
