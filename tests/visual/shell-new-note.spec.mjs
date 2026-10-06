// New notes (docs/MARKMED-PLAN.md 5-6): a blank "+ Uus" note left goes; a titled one gets its
// title's slug when left, and its old address still opens it.
import { expect, test } from "@playwright/test";

import { api, createNote, enter, opened, saveState, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.skip(({ isMobile }) => isMobile, "desktop sidebar");

const slugOf = (page) => decodeURIComponent(new URL(page.url()).pathname.slice(3));

test("a blank new note left for another note is gone", async ({ page }) => {
  await enter(page, "/");
  const other = await createNote(page, unique("Teine "));
  await page.click(".side-new");
  await expect(page).toHaveURL(/\/m\/marge(-\d+)?$/);
  const blank = slugOf(page);
  await opened(page, blank);
  await page.locator("#left a.side-note", { hasText: other.title }).first().click();
  await opened(page, other.slug);
  await expect.poll(async () => (await api(page, `/api/notes/${blank}`)).status).toBe(404);
  await expect(page.locator(`#left a.side-note[data-slug="${blank}"]`)).toHaveCount(0);
});

test("a titled new note takes its title's slug when left; the old address opens it", async ({ page }) => {
  await enter(page, "/");
  const other = await createNote(page, unique("Kolmas "));
  await page.click(".side-new");
  await expect(page).toHaveURL(/\/m\/marge(-\d+)?$/);
  const old = slugOf(page);
  await opened(page, old);
  const title = unique("Ajaloo konspekt ");
  await page.keyboard.type(title);
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await page.locator("#left a.side-note", { hasText: other.title }).first().click();
  await opened(page, other.slug);
  const fresh = title.toLowerCase().replace(/ /g, "-");
  await expect(page.locator(`#left a.side-note[data-slug="${fresh}"]`).first()).toHaveText(title);

  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/m/${fresh}$`));
  await opened(page, fresh);
  await page.goto(`/m/${old}`);
  await expect(page).toHaveURL(new RegExp(`/m/${fresh}$`));
  await opened(page, fresh);
});
