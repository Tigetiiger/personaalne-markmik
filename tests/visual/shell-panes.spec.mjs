// Side panes (WP-C): collapse toggles that persist, full-width pages, light/dark look.
import { expect, test } from "@playwright/test";

import { api, createNote, enter, openNote, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.skip(({ isMobile }) => isMobile, "desktop panes; the phone has its own spec");

test("Ctrl+\\ and Ctrl+Shift+\\ collapse the panes; the buttons open them; both are remembered", async ({ page }) => {
  await enter(page, "/");
  const app = page.locator("#app");
  await expect(page.locator("#left")).toBeVisible();
  await expect(page.locator("#right")).toBeVisible();
  await page.keyboard.press("Control+Backslash");
  await page.keyboard.press("Control+Shift+Backslash");
  await expect(app).toHaveClass(/no-left/);
  await expect(app).toHaveClass(/no-right/);
  await expect(page.locator("#left")).toBeHidden();
  await expect(page.locator(".toggle-left")).toHaveAttribute("aria-expanded", "false");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  await expect(app).toHaveClass(/no-left/);
  await expect(app).toHaveClass(/no-right/);
  await page.click(".toggle-left");
  await page.click(".toggle-right");
  await expect(page.locator("#left")).toBeVisible();
  await expect(page.locator("#right")).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  await expect(app).not.toHaveClass(/no-left|no-right/);
  // Täna has the sidebar but no right pane.
  await page.goto("/tana");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  await expect(app).toHaveClass(/no-right/);
  await expect(app).not.toHaveClass(/no-left/);
  await expect(page.locator(".toggle-right")).toHaveCount(0);
});

test("the graph page gets the full width", async ({ page }) => {
  await enter(page, "/graaf");
  await expect(page.locator("#app")).toHaveClass(/no-left/);
  await expect(page.locator("#app")).toHaveClass(/no-right/);
  await expect(page.locator(".toggle-left, .toggle-right")).toHaveCount(0);
  const width = await page.locator("#graaf").evaluate((el) => el.getBoundingClientRect().width);
  expect(width).toBeGreaterThan(1200);
});

// Each theme has its own note: a note just closed in another test still counts its reader for a
// while, and "Koos: ..." would change the picture.
for (const [theme, title] of [["hele", "Hele leht"], ["tume", "Tume leht"]]) {
  test(`note page looks right (${theme})`, async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "screenshot baselines are chromium only");
    await page.addInitScript((t) => localStorage.setItem("teema", t), theme);
    await enter(page, "/");
    const note = await createNote(page, title, "Märkmik hoiab märkmeid ja kalendrit koos.\n\n## Nädala plaan\n\n- [ ] matemaatika lk 12\n- [x] füüsika katse\n\n> Tsitaat, mis jääb meelde.");
    await api(page, `/api/notes/${note.slug}`, { method: "PATCH", body: { folder: "kool/reaal", tags: ["kool", "näide"] } });
    await openNote(page, note.slug);
    await expect(page.locator("html")).toHaveAttribute("data-teema", theme);
    await expect(page.locator("#note-meta .meta-words")).toHaveText("19 sõna");
    await expect(page.locator(".links-block")).toHaveCount(3);
    // The note lists depend on what other tests made: a fixed box, masked. So does the Postkast
    // count (the baselines show none).
    await page.addStyleTag({ content: "#left .side-lists { height: 20rem; overflow: hidden; } #postkast-arv { display: none !important; }" });
    await expect(page).toHaveScreenshot(`shell-note-${theme}.png`, { mask: [page.locator("#left .side-lists")] });
  });
}
