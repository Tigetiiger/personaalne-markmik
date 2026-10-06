// Ajalugu (docs/MARKMED-PLAN.md 17): the panel lists the note's versions and one is restored through
// the review window. The test server has no brain repo, so the history answers are stubbed.
import { expect, test } from "@playwright/test";

import { api, createNote, editor, enter, openNote, saveState, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.skip(({ isMobile }) => isMobile, "desktop panels");

test("a note never synced says so", async ({ page }) => {
  await enter(page, "/");
  const note = await createNote(page, unique("Sünkroonimata "));
  await openNote(page, note.slug);
  await page.locator(".meta-more").click();
  await page.getByRole("menuitem", { name: "Ajalugu" }).click();
  await expect(page.locator('.panel[data-panel="ajalugu"]')).toContainText("pärast esimest sünkroonimist");
});

test("a version is restored through the review window", async ({ page }) => {
  await enter(page, "/");
  const title = unique("Ajalooga ");
  const note = await createNote(page, title, `# ${title}\n\npraegune tekst\n`);
  const sha = "a".repeat(40);
  await page.route(`**/api/notes/${note.slug}/ajalugu`, (route) =>
    route.fulfill({ json: { versions: [{ sha, at: "2026-10-01T09:30:00Z", message: "markmik: 1 note" }] } }));
  await page.route(`**/api/notes/${note.slug}/ajalugu/${sha}`, (route) =>
    route.fulfill({ json: { body: `# ${title}\n\nvana tekst\n` } }));
  await openNote(page, note.slug);
  await page.locator(".meta-more").click();
  await page.getByRole("menuitem", { name: "Ajalugu" }).click();
  const panel = page.locator('.panel[data-panel="ajalugu"]');
  await expect(panel).toContainText("markmik: 1 note");
  await panel.locator(".history-version").click();
  const review = page.locator("dialog.ai-review");
  await expect(review.locator("h2")).toHaveText(/^Versioon 1\.10\.2026,? 12:30$/);
  await review.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(editor(page)).toContainText("vana tekst");
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await expect.poll(async () => (await api(page, `/api/notes/${note.slug}`)).data.body).toBe(`# ${title}\n\nvana tekst\n`);
});
