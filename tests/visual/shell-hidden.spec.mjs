// Hidden notes: "Peida nimekirjast" on the note takes it out of the sidebar's lists; "Näita
// peidetuid" at the bottom of the sidebar brings them back, faded, and is remembered.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const unique = (name) => `${name} ${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test("a hidden note leaves the sidebar until Näita peidetuid", async ({ page, isMobile }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page, "/tana");
  const title = unique("Peidetav");
  const note = (await api(page, "/api/notes", { method: "POST", body: { title, folder: "peitmine" } })).data;
  await page.goto(`/m/${note.slug}`);
  await waitReady(page);
  const left = page.locator("#left");
  const inList = left.locator(".side-lists a.side-note", { hasText: title });
  if (isMobile) await page.locator(".toggle-left").click();
  await expect(inList.first()).toBeAttached();
  if (isMobile) await page.locator(".pane-close").first().click();

  await page.getByRole("button", { name: "Peida nimekirjast" }).click();
  await expect(page.locator(".meta-hide")).toHaveAttribute("aria-pressed", "true");
  await expect(inList).toHaveCount(0);
  if (isMobile) await page.locator(".toggle-left").click();
  const toggle = left.locator(".side-hidden-toggle");
  await expect(toggle).toContainText("Näita peidetuid");
  await toggle.click();
  await expect(inList.first()).toHaveClass(/side-hidden/);
  // Remembered in this browser.
  await page.reload();
  await waitReady(page);
  if (isMobile) await page.locator(".toggle-left").click();
  await expect(left.locator(".side-lists a.side-note.side-hidden", { hasText: title }).first()).toBeAttached();
  await left.locator(".side-hidden-toggle").click();
  await expect(inList).toHaveCount(0);
  // Search still finds it.
  if (!isMobile) {
    await left.locator(".side-search").fill(title.split(" ")[1]);
    await expect(left.locator(".side-results a.side-note", { hasText: title })).toHaveCount(1);
  }
  // Unhide on the note.
  await api(page, `/api/notes/${note.slug}`, { method: "PATCH", body: { hidden: false } });
  await page.reload();
  await waitReady(page);
  await expect(left.locator(".side-lists a.side-note", { hasText: title }).first()).toBeAttached();
  expect(errors).toEqual([]);
});
