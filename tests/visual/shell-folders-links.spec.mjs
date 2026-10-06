// Folders and links (docs/MARKMED-PLAN.md 9-12): completion for tags and folders, renaming a folder,
// a link's #heading, and link texts after a title change.
import { expect, test } from "@playwright/test";

import { api, caretAtEnd, createNote, editor, enter, openNote, opened, saveState, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.skip(({ isMobile }) => isMobile, "desktop sidebar");

test("the tag and folder inputs offer what exists", async ({ page }) => {
  await enter(page, "/");
  const tag = unique("silt");
  const folder = unique("kaust");
  const first = await createNote(page, unique("Esimene "));
  await api(page, `/api/notes/${first.slug}`, { method: "PATCH", body: { tags: [tag], folder: `${folder}/all` } });
  const second = await createNote(page, unique("Teine "));
  await openNote(page, second.slug);
  await page.locator(".chip-add").click();
  await expect(page.locator(`#meta-input-options option[value="${tag}"]`)).toBeAttached();
  await page.locator(".meta-input").fill(tag);
  await page.keyboard.press("Enter");
  await expect(page.locator(".meta-tags .chip", { hasText: `#${tag}` })).toBeVisible();
  await page.locator(".meta-crumb").click();
  await expect(page.locator(`#meta-input-options option[value="${folder}"]`)).toBeAttached();
  await expect(page.locator(`#meta-input-options option[value="${folder}/all"]`)).toBeAttached();
  await page.keyboard.press("Escape");
  await expect(page.locator("#meta-input-options")).toHaveCount(0);
});

test("renaming a folder moves its notes and the open note's crumb follows", async ({ page }) => {
  await enter(page, "/");
  const folder = unique("vanakaust");
  const note = await createNote(page, unique("Kaustas "));
  await api(page, `/api/notes/${note.slug}`, { method: "PATCH", body: { folder } });
  await openNote(page, note.slug);
  const summary = page.locator(`#left summary[data-folder="${folder}"]`);
  await summary.hover();
  const fresh = `${folder}-uus`;
  page.once("dialog", (d) => d.accept(fresh));
  await summary.locator(".side-folder-rename").click();
  await expect(page.locator(`#left summary[data-folder="${fresh}"]`)).toBeVisible();
  await expect(page.locator(".meta-crumb")).toHaveText(fresh);
  expect((await api(page, `/api/notes/${note.slug}`)).data.folder).toBe(fresh);
});

test("a link's #heading opens the note at that heading", async ({ page }) => {
  await enter(page, "/");
  const filler = Array.from({ length: 60 }, (_, i) => `Lõik ${i}.`).join("\n\n");
  const target = await createNote(page, unique("Pikk "), `${filler}\n\n## Teine osa\n\nSiin.\n`);
  const from = await createNote(page, unique("Viitaja "), `[vaata](/m/${target.slug}#teine-osa)\n`);
  await openNote(page, from.slug);
  await editor(page).locator("span.mu-link", { hasText: "vaata" }).click({ modifiers: ["Control"] });
  await opened(page, target.slug);
  await expect(page).toHaveURL(new RegExp(`/m/${target.slug}#teine-osa$`));
  await expect(editor(page).locator("h2", { hasText: "Teine osa" })).toBeInViewport();
});

test("after a title change the notes linking with the old title are offered an update", async ({ page }) => {
  await enter(page, "/");
  const old = unique("Vana pealkiri ");
  const target = await createNote(page, old);
  const src = await createNote(page, unique("Allikas "), `[${old}](/m/${target.slug}) ja [vt](/m/${target.slug})\n`);
  await openNote(page, target.slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" uus");
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  const toast = page.locator("#toast");
  await expect(toast).toContainText("1 märge lingib siia vana pealkirjaga");
  await toast.getByRole("button", { name: "Uuenda (1)" }).click();
  const review = page.locator("dialog.ai-review");
  await expect(review).toContainText(`Lingitekst märkmes`);
  await review.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(toast).toContainText("Lingitekst uuendatud.");
  expect((await api(page, `/api/notes/${src.slug}`)).data.body).toContain(`[${old} uus](/m/${target.slug}) ja [vt](/m/${target.slug})\n`);
});
