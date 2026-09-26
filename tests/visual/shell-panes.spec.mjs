// Side panes (WP-C): the right panel from the crumb row, collapse keys that persist, full-width
// pages, light/dark look.
import { expect, test } from "@playwright/test";

import { api, createNote, enter, openNote, unique, waitReady, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);
test.skip(({ isMobile }) => isMobile, "desktop panes; the phone has its own spec");

test("the right panel starts closed; Viited and ✦ AI open it on their tabs; Ctrl+\\ and Ctrl+Shift+\\ collapse the panes, remembered", async ({ page }) => {
  await enter(page, "/");
  const n = await createNote(page, unique("Paneelid "));
  await openNote(page, n.slug);
  const app = page.locator("#app");
  const tab = (id) => page.locator(`#panel-tabs button[data-panel="${id}"]`);
  await expect(page.locator("#left")).toBeVisible();
  await expect(page.locator("#right")).toBeHidden();
  await expect(app).toHaveClass(/no-right/);

  // Viited opens the panel on Viited; ✦ AI switches it to Abiline, and closes it the second time.
  const viited = page.locator("#note-meta .meta-panel");
  const ai = page.locator("#note-meta .ai-meta");
  await viited.click();
  await expect(page.locator("#right")).toBeVisible();
  await expect(tab("viited")).toHaveAttribute("aria-selected", "true");
  await expect(viited).toHaveAttribute("aria-expanded", "true");
  await ai.click();
  await expect(tab("assistant")).toHaveAttribute("aria-selected", "true");
  await expect(viited).toHaveAttribute("aria-expanded", "false");
  await ai.click();
  await expect(page.locator("#right")).toBeHidden();
  // Viited comes back to the tab last chosen other than Abiline, and closes it the second time.
  await viited.click();
  await tab("sisukord").click();
  await ai.click();
  await viited.click();
  await expect(tab("sisukord")).toHaveAttribute("aria-selected", "true");
  await viited.click();
  await expect(page.locator("#right")).toBeHidden();

  // The keys, remembered both ways.
  await page.keyboard.press("Control+Shift+Backslash");
  await expect(page.locator("#right")).toBeVisible();
  await page.reload();
  await waitReady(page);
  await expect(app).not.toHaveClass(/no-left|no-right/);
  await page.keyboard.press("Control+Backslash");
  await page.keyboard.press("Control+Shift+Backslash");
  await expect(app).toHaveClass(/no-left/);
  await expect(app).toHaveClass(/no-right/);
  await expect(page.locator("#left")).toBeHidden();
  await expect(page.locator(".toggle-left")).toHaveAttribute("aria-expanded", "false");
  await page.reload();
  await waitReady(page);
  await expect(app).toHaveClass(/no-left/);
  await expect(app).toHaveClass(/no-right/);
  await page.keyboard.press("Control+Backslash");
  await expect(page.locator("#left")).toBeVisible();

  // Täna has the list but no right panel.
  await page.goto("/tana");
  await waitReady(page);
  await expect(app).toHaveClass(/no-right/);
  await expect(app).not.toHaveClass(/no-left/);
  await expect(page.locator("#note-meta")).toHaveCount(0);
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
