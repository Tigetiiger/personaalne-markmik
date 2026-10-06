// Ülesanded, search filters and Sarnased märkmed (docs/MARKMED-PLAN.md 13-15).
import { expect, test } from "@playwright/test";

import { api, createNote, enter, openNote, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());

test("Ülesanded groups a folder's tasks and toggles one", async ({ page }) => {
  await enter(page, "/");
  const folder = unique("ulesanded");
  const note = await createNote(page, unique("Tööd "), `- [ ] vana @2020-01-01\n- [ ] tänane @${today}\n- [ ] kunagi\n- [x] valmis\n`);
  await api(page, `/api/notes/${note.slug}`, { method: "PATCH", body: { folder } });
  await enter(page, "/ulesanded");
  await expect(page.locator('#nav a[href="/ulesanded"]')).toHaveAttribute("aria-current", "page");
  await page.locator("#ulesanded-filtrid select").first().selectOption(folder);
  const section = (title) => page.locator("#ulesanded-sisu section", { has: page.locator("h2", { hasText: title }) });
  await expect(section("Üle tähtaja")).toContainText("vana");
  await expect(section("Täna")).toContainText("tänane");
  await expect(section("Tähtajata")).toContainText("kunagi");
  await expect(page.locator("#ulesanded-sisu")).not.toContainText("valmis");
  await section("Tähtajata").getByRole("checkbox").check();
  await expect(section("Tähtajata").locator("li.done")).toHaveCount(1);
  expect((await api(page, `/api/notes/${note.slug}`)).data.body).toContain("- [x] kunagi");
  await page.getByLabel("Näita tehtuid").check();
  await expect(section("Tehtud")).toContainText("valmis");
});

test("search filters by tag and folder; Seotud lists similar notes", async ({ page, isMobile }) => {
  test.skip(isMobile, "the sidebar search hides on a phone");
  await enter(page, "/");
  const word = unique("klorofüll");
  const folder = unique("bio");
  const a = await createNote(page, unique("Fotosüntees "), `${word} valgus lehed\n`);
  const b = await createNote(page, unique("Taimed "), `${word} lehed\n`);
  await api(page, `/api/notes/${a.slug}`, { method: "PATCH", body: { tags: ["bioloogia"], folder } });
  const search = page.locator("#left .side-search");
  await search.fill(`#bioloogia kaust:${folder}`);
  const results = page.locator("#left .side-results li");
  await expect(results).toHaveCount(1);
  await expect(results.first()).toContainText(a.title);
  await expect(results.first().locator(".side-where")).toHaveText(folder);

  await openNote(page, a.slug);
  await page.locator(".meta-panel").click();
  await page.locator('#panel-tabs [data-panel="related"]').click();
  await expect(page.locator('.panel[data-panel="related"] a.related-title', { hasText: b.title })).toBeVisible();
});
