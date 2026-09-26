// Command palette and quick capture (WP-C).
import { expect, test } from "@playwright/test";

import { api, createNote, enter, opened, unique, waitReady, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

const palette = (page) => page.locator("dialog.palette");
const items = (page) => palette(page).locator(".palette-item");

// Every date the palette tests type ("täna", "reede", "3.1", 1 October 2026) lies within ~200 days
// of today; the events API caps a range at 400 days.
async function eventCount(page) {
  const day = (offset) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
  const r = await api(page, `/api/events?start=${day(-199)}&end=${day(200)}`);
  expect(r.status).toBe(200);
  return r.data.length;
}

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
  await items(page).filter({ hasText: /^Tänag t$/ }).click(); // the command, its key as the hint
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

test("Alt+N makes a new note too, and the palette shows it", async ({ page }) => {
  await enter(page, "/");
  await page.keyboard.press("Alt+n");
  await expect(page).toHaveURL(/\/m\/marge/);
  await opened(page, new URL(page.url()).pathname.slice(3));
  await page.keyboard.press("Control+k");
  await page.keyboard.type("uus");
  await expect(items(page).filter({ hasText: "Uus märge" }).locator(".palette-hint-inline")).toHaveText("n · Alt+N");
});

test("a command or note the text names comes before quick capture", async ({ page }) => {
  await enter(page, "/");
  const count = () => eventCount(page);
  const before = await count();
  // "täna" is also a date: Enter runs the Täna command, it does not add an event.
  await page.keyboard.press("Control+k");
  await page.keyboard.type("täna");
  await expect(palette(page).locator(".palette-list")).toHaveAttribute("data-query", "täna");
  await expect(items(page).first()).toHaveText("Tänag t");
  await expect(items(page).filter({ hasText: "Lisa:" })).toHaveCount(0); // a date alone: no capture
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/tana");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  // A note whose title looks like a date opens; it is not captured.
  const n = await createNote(page, unique("Peatükk 3.1 "));
  await page.keyboard.press("Control+k");
  await page.keyboard.type(n.title);
  await expect(items(page).first()).toHaveText(n.title);
  await page.keyboard.press("Enter");
  await opened(page, n.slug);
  expect(await count()).toBe(before);
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
  // Capture is never what Enter does by itself: it is chosen with the arrows.
  await expect(add).toHaveAttribute("aria-selected", "false");
  await page.keyboard.press("ArrowDown");
  await expect(add).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page.locator("#toast")).toContainText("Lisatud kalendrisse: mate kontrolltöö");

  const task = unique("vii raamat tagasi ");
  await page.keyboard.press("Control+k");
  await page.keyboard.type(`todo ${task}`);
  await expect(items(page).first()).toContainText("ülesanne tänasesse päevikusse");
  await page.keyboard.press("ArrowDown");
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
  await expect(items(page).first()).toHaveAttribute("aria-selected", "true"); // ">" asks for it
  await page.keyboard.press("Enter");
  await expect(page.locator("#note-meta")).toBeVisible();
  await expect(page).toHaveTitle(`${title} · Märkmik`);
});

test("a date alone offers no capture, and Enter never captures without >", async ({ page }) => {
  await enter(page, "/");
  const count = () => eventCount(page);
  const before = await count();
  for (const text of ["reede", "kolmapäev", "Neljapäev, 1. oktoober 2026"]) {
    await page.keyboard.press("Control+k");
    await page.keyboard.type(text);
    await expect(palette(page).locator(".palette-list")).toHaveAttribute("data-query", text);
    await expect(items(page).filter({ hasText: "Lisa:" })).toHaveCount(0);
    await page.keyboard.press("Escape");
  }
  const text = unique("Lõik 3.1 "); // no note has this title
  await page.keyboard.press("Control+k");
  await page.keyboard.type(text);
  await expect(palette(page).locator(".palette-list")).toHaveAttribute("data-query", text);
  await expect(items(page)).toHaveCount(1);
  await expect(items(page).filter({ hasText: `Lisa: ${text}` })).toHaveAttribute("aria-selected", "false");
  await page.keyboard.press("Enter");
  await expect(palette(page)).toBeVisible(); // nothing chosen, nothing done
  await page.keyboard.press("Escape");
  expect(await count()).toBe(before);
});

test("a refused capture opens the palette again with the text", async ({ page }) => {
  await enter(page, "/");
  await page.route("**/api/capture", (route) =>
    route.request().postDataJSON().preview ? route.continue() : route.fulfill({ status: 409, json: { error: "Märget muudetakse teises aknas." } }));
  await page.keyboard.press("Control+k");
  await page.keyboard.type("todo kirjuta essee");
  await expect(items(page).filter({ hasText: "Lisa: todo kirjuta essee" })).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.locator("#toast")).toHaveText("Märget muudetakse teises aknas.");
  await expect(palette(page)).toBeVisible();
  await expect(palette(page).locator(".palette-input")).toHaveValue("todo kirjuta essee");
  await expect(items(page).filter({ hasText: "Lisa: todo kirjuta essee" })).toBeVisible();
});

test("plain text that is no date offers notes, not Lisa", async ({ page }) => {
  await enter(page, "/");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("lihtsalt sõnad");
  await expect(palette(page).locator(".palette-empty, .palette-item").first()).toBeVisible();
  await expect(items(page).filter({ hasText: "Lisa:" })).toHaveCount(0);
});
