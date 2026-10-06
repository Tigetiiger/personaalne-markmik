// The trash (docs/MARKMED-PLAN.md): "Kustuta" on a note, "Võta tagasi" in the toast, the
// Prügikast dialog (restore, delete for good) and "⋯ → Laadi alla .md".
import { expect, test } from "@playwright/test";

import { createNote, enter, opened, openNote, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

async function openTrash(page, isMobile) {
  if (isMobile) await page.locator(".toggle-left").click();
  await page.locator("#left .side-trash").click();
  return page.locator("dialog.trash");
}

test("delete, take back, restore from the trash, delete for good", async ({ page, isMobile }) => {
  await enter(page, "/tana");
  const title = unique("Kustutatav");
  const note = await createNote(page, title, `# ${title}\n\nsisu jääb alles\n`);
  await openNote(page, note.slug);

  await page.locator(".meta-delete").click();
  await expect(page).toHaveURL(/\/$/);
  const toast = page.locator("#toast");
  await expect(toast).toContainText("viidi prügikasti");
  await toast.getByRole("button", { name: "Võta tagasi" }).click();
  await opened(page, note.slug);
  await expect(page.locator("#editor .mu-editor")).toContainText("sisu jääb alles");

  await page.locator(".meta-delete").click();
  await expect(page).toHaveURL(/\/$/);
  let dialog = await openTrash(page, isMobile);
  const row = dialog.locator("li", { hasText: title });
  await expect(row).toContainText("kaob 30 päeva pärast");
  await row.getByRole("button", { name: "Taasta" }).click();
  await opened(page, note.slug);

  await page.locator(".meta-delete").click();
  await expect(page).toHaveURL(/\/$/);
  dialog = await openTrash(page, isMobile);
  page.once("dialog", (d) => d.accept());
  await dialog.locator("li", { hasText: title }).getByRole("button", { name: "Kustuta jäädavalt" }).click();
  await expect(dialog.locator("li", { hasText: title })).toHaveCount(0);
  const r = await page.request.get(`/api/notes/${note.slug}`);
  expect(r.status()).toBe(404);
});

test("⋯ menu downloads the note as Markdown", async ({ page }) => {
  const title = unique("Allalaetav");
  await enter(page, "/tana");
  const note = await createNote(page, title, `# ${title}\n\n[link](/m/muu)\n`);
  await openNote(page, note.slug);
  await page.locator(".meta-more").click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Laadi alla .md" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe(`${title}.md`);
  await expect(page.locator(".meta-menu")).toBeHidden();
});
