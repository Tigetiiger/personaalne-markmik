// Phone layout (WP-C): the sidebar is a drawer, the right panel a bottom sheet, the editor full
// width. Written for --project=mobile; the phone viewport makes it hold under chromium too.
import { expect, test } from "@playwright/test";

import { createNote, editor, enter, openNote, opened, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.use({ viewport: { width: 390, height: 844 } });

test("drawer and bottom sheet open, close, and get out of the way", async ({ page }) => {
  await enter(page, "/");
  const other = await createNote(page, unique("Teine "));
  const n = await createNote(page, unique("Telefonis "), "Lühike tekst.\n\n## Osa\n\nVeel.");
  await openNote(page, n.slug);
  const app = page.locator("#app");
  await expect(page.locator("#left")).toBeHidden();
  await expect(page.locator("#right")).toBeHidden();
  await expect(page.locator(".toggle-right")).toBeHidden();
  const width = await editor(page).evaluate((el) => el.getBoundingClientRect().width);
  expect(width).toBeGreaterThan(300);

  // Drawer: the menu button opens it, a note in it opens that note and closes it.
  await page.locator(".toggle-left").click();
  await expect(app).toHaveClass(/show-left/);
  await expect(page.locator("#left .side-new")).toBeVisible();
  await page.locator("#left .pane-close").click();
  await expect(page.locator("#left")).toBeHidden();
  await page.locator(".toggle-left").click();
  await page.locator("#left a.side-note", { hasText: other.title }).first().click();
  await opened(page, other.slug);
  await expect(page.locator("#left")).toBeHidden();

  // Sheet: Viited in the crumb row; the tabs work; ✕ closes it.
  await page.goBack();
  await opened(page, n.slug);
  await page.locator("#note-meta .meta-panel").click();
  await expect(app).toHaveClass(/show-right/);
  await expect(page.locator("#right")).toBeVisible();
  await page.locator('#panel-tabs button[data-panel="sisukord"]').click();
  await expect(page.locator('.panel[data-panel="sisukord"] .outline button')).toHaveText([n.title, "Osa"]);
  await page.locator("#right .pane-close").click();
  await expect(page.locator("#right")).toBeHidden();

  // A tap on the note closes whichever is open; the collapse state of a wide screen is untouched.
  await page.locator(".toggle-left").click();
  await expect(page.locator("#left")).toBeVisible();
  await page.mouse.click(370, 600);
  await expect(page.locator("#left")).toBeHidden();
  await expect(app).not.toHaveClass(/no-left/);
  expect(await page.evaluate(() => [localStorage.getItem("shell:left"), localStorage.getItem("shell:right")])).toEqual([null, null]);
});

test("the top bar fits one line: nothing wraps or squashes, only the page links scroll, the search hides", async ({ page }) => {
  for (const path of ["/", "/kalender"]) {
    await enter(page, path);
    const boxes = await page.evaluate(() => {
      const box = (sel) => document.querySelector(sel).getBoundingClientRect().toJSON();
      return { bar: box(".topbar .inner"), uus: box(".new-note"), seaded: box(".seaded"), teema: box("#teema"), nav: box(".topbar nav") };
    });
    for (const name of ["uus", "seaded", "teema"]) {
      expect(boxes[name].top, `${path} ${name}`).toBeGreaterThanOrEqual(boxes.bar.top);
      expect(boxes[name].bottom, `${path} ${name}`).toBeLessThanOrEqual(boxes.bar.bottom);
    }
    expect(boxes.teema.width, path).toBeCloseTo(boxes.teema.height, 0); // square, not squashed
    expect(boxes.seaded.right, path).toBeLessThanOrEqual(boxes.bar.right);
    expect(boxes.nav.width, path).toBeGreaterThan(60);
    await expect(page.locator(".palette-open")).toBeHidden();
  }
});

test("with both panes closed on the desktop, the phone still shows the page full width", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("shell:left", "0");
    localStorage.setItem("shell:right", "0");
  });
  await enter(page, "/");
  const n = await createNote(page, unique("Suletud paanid "));
  await openNote(page, n.slug);
  await expect(page.locator("#app")).toHaveClass(/no-left.*no-right|no-right.*no-left/);
  expect(await page.locator("#main").evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(350);
  expect(await editor(page).evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(300);
});
