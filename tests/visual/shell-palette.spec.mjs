// Command palette and quick capture (WP-C).
import { expect, test } from "@playwright/test";

import { api, createNote, enter, opened, unique, waitReady, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

const palette = (page) => page.locator("dialog.palette");
const items = (page) => palette(page).locator(".palette-item");

test("Ctrl+K opens the palette; typing finds a note; Enter opens it; Esc closes", async ({ page }) => {
  await enter(page, "/");
  const n = await createNote(page, unique("Paletis "));
  await page.keyboard.press("Control+k");
  await expect(palette(page)).toBeVisible();
  await expect(palette(page).locator(".palette-input")).toBeFocused();
  await page.keyboard.type(n.title.slice(0, 12));
  await expect(items(page).first()).toContainText(n.title);
  await expect(items(page).first()).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(palette(page)).toBeHidden();
  await opened(page, n.slug);

  await page.click(".palette-open");
  await expect(palette(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette(page)).toBeHidden();
});

test("commands: arrow keys pick one, Enter runs it", async ({ page }) => {
  await enter(page, "/");
  const theme = await page.locator("html").getAttribute("data-teema");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("vaheta teem");
  await expect(items(page).filter({ hasText: "Vaheta teema" })).toHaveCount(1);
  const labels = await items(page).locator(".palette-label").allTextContents();
  for (let i = 0; i < labels.indexOf("Vaheta teema"); i++) await page.keyboard.press("ArrowDown");
  await expect(items(page).filter({ hasText: "Vaheta teema" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).not.toHaveAttribute("data-teema", theme);
  await page.click("#teema"); // back

  await page.keyboard.press("Control+k");
  await page.keyboard.type("täna");
  await items(page).filter({ hasText: /^Tänakäsk$/ }).click();
  await expect(page).toHaveURL("/tana");
});

test("Ctrl+N makes a new note from any page", async ({ page }) => {
  await enter(page, "/tana");
  await page.keyboard.press("Control+n");
  await expect(page).toHaveURL(/\/m\/marge/);
  await opened(page, new URL(page.url()).pathname.slice(3));
  await page.keyboard.press("Control+n");
  await expect(page.locator(".mu-editor")).toHaveCount(1);
  await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/m\/marge-\d+$/);
});

test("Uus märge from the palette opens a new note with the caret in the title", async ({ page }) => {
  await enter(page, "/tana");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("uus märge");
  await items(page).filter({ hasText: "Uus märge" }).click();
  await expect(page).toHaveURL(/\/m\/marge/);
  const slug = new URL(page.url()).pathname.slice(3);
  await opened(page, slug);
  await page.keyboard.type("Paleti kaudu");
  await expect.poll(async () => (await api(page, `/api/notes/${slug}`)).data.body, { timeout: 10_000 }).toBe("# Paleti kaudu\n");
});

test("quick capture: a date makes an event, a task goes to the daily note, > makes a note", async ({ page }) => {
  await enter(page, "/");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("mate kontrolltöö reedel 10.15");
  const add = items(page).filter({ hasText: "Lisa: mate kontrolltöö reedel 10.15" });
  await expect(add).toContainText(/kontrolltöö · R \d+\.\d+\. kell 10:15/);
  await expect(add).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page.locator("#toast")).toContainText("Lisatud kalendrisse: mate kontrolltöö");

  const task = unique("vii raamat tagasi ");
  await page.keyboard.press("Control+k");
  await page.keyboard.type(`todo ${task}`);
  await expect(items(page).first()).toContainText("ülesanne tänasesse päevikusse");
  await page.keyboard.press("Enter");
  await expect(page.locator("#toast")).toHaveText("Ülesanne lisatud tänasesse päevikusse.");
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
  const daily = (await api(page, `/api/daily/${today}`)).data;
  expect(daily.body).toContain(`- [ ] ${task}\n`);

  const title = unique("Kiire mõte ");
  await page.keyboard.press("Control+k");
  await page.keyboard.type(`>${title}`);
  await expect(items(page)).toHaveCount(1);
  await expect(items(page).first()).toContainText("uus märge");
  await page.keyboard.press("Enter");
  await expect(page.locator("#note-meta")).toBeVisible();
  await expect(page).toHaveTitle(`${title} · Märkmik`);
});

test("plain text that is no date offers notes, not Lisa", async ({ page }) => {
  await enter(page, "/");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("lihtsalt sõnad");
  await expect(palette(page).locator(".palette-empty, .palette-item").first()).toBeVisible();
  await expect(items(page).filter({ hasText: "Lisa:" })).toHaveCount(0);
});
