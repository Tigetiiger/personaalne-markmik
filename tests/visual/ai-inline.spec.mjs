// Inline AI in the editor with the fake model (AI_FAKE=1): Asenda / Lisa alla / Loobu through
// the editor handle, Ctrl+Z, a co-editor seeing the change, the "@" menu item, and a target that
// changed meanwhile.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });

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

async function withNote(browser, baseURL, md) {
  const slug = unique("inline");
  const page = await editorPage(browser, baseURL, slug);
  await page.evaluate((md) => window.editor.replaceMarkdown(md), md);
  return { page, slug };
}

const markdown = (page) => page.evaluate(() => window.editor.getMarkdown());
const fake = (page, reply) => api(page, "/_test/ai-fake", { method: "POST", body: reply === undefined ? {} : { reply } });

async function selectText(page, from, to = from) {
  await page.locator("#editor .mu-editor").focus();
  await page.evaluate(([from, to]) => {
    const find = (text, end) => {
      const walker = document.createTreeWalker(document.querySelector("#editor .mu-editor"), NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.data.indexOf(text);
        if (at >= 0) return [node, end ? at + text.length : at];
      }
      throw new Error(`"${text}" is not in the editor`);
    };
    const range = document.createRange();
    range.setStart(...find(from, false));
    range.setEnd(...find(to, true));
    document.getSelection().removeAllRanges();
    document.getSelection().addRange(range);
  }, [from, to]);
}

async function done(...pages) {
  await fake(pages[0]);
  for (const page of pages) {
    expect(page.errors, "JavaScript errors on the page").toEqual([]);
    await page.context().close();
  }
}

test("Asenda replaces the selection, Ctrl+Z undoes it, a co-editor sees it", async ({ browser, baseURL }) => {
  const { page, slug } = await withNote(browser, baseURL, "# Proov\n\nEsimene lause on siin. Teine lause.\n");
  const other = await editorPage(browser, baseURL, slug);
  await expect.poll(() => markdown(other)).toContain("Esimene lause on siin.");
  await fake(page, "Lühike");
  await selectText(page, "Esimene lause on siin.");
  await page.keyboard.press("Control+j");
  const box = page.locator(".ai-inline");
  await expect(box).toBeVisible();
  await box.getByRole("button", { name: "Lühemaks" }).click();
  await expect(box.locator(".ai-preview")).toHaveText("Lühike");
  await box.getByRole("button", { name: "Asenda" }).click();
  await expect(box).toBeHidden();
  await expect.poll(() => markdown(page)).toBe("# Proov\n\nLühike Teine lause.\n");
  await expect.poll(() => markdown(other)).toBe("# Proov\n\nLühike Teine lause.\n");

  await page.keyboard.press("Control+z");
  await expect.poll(() => markdown(page)).toBe("# Proov\n\nEsimene lause on siin. Teine lause.\n");
  await expect.poll(() => markdown(other)).toBe("# Proov\n\nEsimene lause on siin. Teine lause.\n");
  await done(page, other);
});

test("Loobu leaves the text as it was (opened with the ✦ in the metadata row)", async ({ browser, baseURL }) => {
  const { page } = await withNote(browser, baseURL, "# Loobu\n\nSee jääb alles.\n");
  await fake(page, "Midagi muud");
  await selectText(page, "See jääb");
  await page.locator(".ai-meta").click();
  const box = page.locator(".ai-inline");
  await box.getByLabel("Juhis AI-le").fill("tee paremaks");
  await box.getByLabel("Juhis AI-le").press("Enter");
  await expect(box.locator(".ai-preview")).toHaveText("Midagi muud");
  await box.getByRole("button", { name: "Loobu" }).click();
  await expect(box).toBeHidden();
  expect(await markdown(page)).toBe("# Loobu\n\nSee jääb alles.\n");

  // The ✦ next to a selection opens it too; Escape closes it.
  await selectText(page, "alles");
  await page.locator(".ai-spark").click();
  await expect(box).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(box).toBeHidden();
  expect(await markdown(page)).toBe("# Loobu\n\nSee jääb alles.\n");
  await done(page);
});

test("Lisa alla and block-level answers become blocks of their own", async ({ browser, baseURL }) => {
  const { page } = await withNote(browser, baseURL, "# Plokid\n\nEsimene lõik.\n\nTeine lõik.\n\nKolmas lõik.\n");
  await fake(page, "Lisatud lõik.");
  await selectText(page, "Esimene");
  await page.keyboard.press("Control+j");
  const box = page.locator(".ai-inline");
  await box.getByRole("button", { name: "Pikemaks" }).click();
  await box.getByRole("button", { name: "Lisa alla" }).click();
  await expect.poll(() => markdown(page)).toBe("# Plokid\n\nEsimene lõik.\n\nLisatud lõik.\n\nTeine lõik.\n\nKolmas lõik.\n");

  // A table for part of a paragraph: the paragraph splits around it.
  await fake(page, "| a | b |\n| --- | --- |\n| 1 | 2 |");
  await selectText(page, "Teine");
  await page.keyboard.press("Control+j");
  await box.getByRole("button", { name: "Tabeliks" }).click();
  await box.getByRole("button", { name: "Asenda" }).click();
  await expect.poll(() => markdown(page)).toMatch(/Lisatud lõik\.\n\n\| a +\| b +\|\n\|[- |:]+\|\n\| 1 +\| 2 +\|\n\n lõik\.\n\nKolmas lõik\.\n$/);
  await expect(page.locator("#editor table")).toHaveCount(1);

  // Across blocks: the blocks it touches are rewritten whole.
  await fake(page, "Üks lõik kahe asemel.");
  await selectText(page, "Lisatud", "Kolmas");
  await page.keyboard.press("Control+j");
  await box.getByLabel("Juhis AI-le").fill("liida kokku");
  await box.getByLabel("Juhis AI-le").press("Enter");
  await box.getByRole("button", { name: "Asenda" }).click();
  await expect.poll(() => markdown(page)).toBe("# Plokid\n\nEsimene lõik.\n\nÜks lõik kahe asemel.\n");
  await done(page);
});

test("the @ menu's AI item writes at the caret", async ({ browser, baseURL }) => {
  const { page } = await withNote(browser, baseURL, "# Menüü\n\nTekst.\n");
  await fake(page, "Tere tulemast!");
  await page.locator("#editor .mu-editor p").first().click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("@ai");
  const item = page.locator(".mu-quick-insert .item").filter({ hasText: /^\s*AI\s*$/ });
  await expect(item).toBeVisible();
  await item.click();
  const box = page.locator(".ai-inline");
  await expect(box.locator(".ai-chip")).toHaveCount(0); // nothing selected: no rewrite chips
  await box.getByLabel("Juhis AI-le").fill("kirjuta tervitus");
  await box.getByLabel("Juhis AI-le").press("Enter");
  await box.getByRole("button", { name: "Lisa", exact: true }).click();
  await expect.poll(() => markdown(page)).toBe("# Menüü\n\nTekst.\n\nTere tulemast!\n");
  await done(page);
});

test("a block changed meanwhile is refused", async ({ browser, baseURL }) => {
  const { page, slug } = await withNote(browser, baseURL, "# Vahepeal\n\nMuudetav lause.\n");
  const other = await editorPage(browser, baseURL, slug);
  await expect.poll(() => markdown(other)).toContain("Muudetav lause.");
  await fake(page, "Uus");
  await selectText(page, "Muudetav");
  await page.keyboard.press("Control+j");
  const box = page.locator(".ai-inline");
  await box.getByRole("button", { name: "Paranda keel" }).click();
  await expect(box.locator(".ai-preview")).toHaveText("Uus");

  await other.locator("#editor .mu-editor p").first().click();
  await other.keyboard.press("End");
  await other.keyboard.type(" Lisa.");
  await expect.poll(() => markdown(page)).toContain("Muudetav lause. Lisa.");

  await box.getByRole("button", { name: "Asenda" }).click();
  await expect(page.locator("#toast")).toHaveText("Tekst muutus vahepeal, proovi uuesti");
  expect(await markdown(page)).toBe("# Vahepeal\n\nMuudetav lause. Lisa.\n");
  await done(page, other);
});
