// Aktsiad → Jälgin (stocks-market): the table, search and add, note and tags, remove. Yahoo is a
// fake market inside the test server.
import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";
import { SPY, daysAgo, fakeMarket, seed } from "./stocks-market-helpers.mjs";

const STATE = "tests/visual/results/stocks-market-watchlist-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad");
  await page.context().storageState({ path: STATE });
  await page.close();
});

test.beforeEach(async ({ page }) => {
  await page.goto("/aktsiad");
  await waitReady(page);
  await fakeMarket(page, { symbols: [{ symbol: "NVDA", name: "NVIDIA Corporation", days: 1300 }] });
  await seed(page, {
    instruments: [
      SPY,
      { symbol: "AAPL", name: "Apple Inc.", note: "Pikk hoidmine", tags: ["tehnoloogia"] },
      { symbol: "VWCE.DE", name: "Vanguard FTSE All-World", kind: "etf", exchange: "XETRA", currency: "EUR", first: 90, drift: 0.0002, seed: 3 },
    ],
    analyses: [{ symbol: "AAPL", as_of: daysAgo(3) }],
    verdicts: [{ symbol: "AAPL", as_of: daysAgo(1), verdict: "buy", conviction: "high" }],
  });
  await page.reload();
  await waitReady(page);
});

test("the table shows prices, changes with a sign, a sparkline and the verdict", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.reload();
  await waitReady(page);
  await expect(page.locator("h2.sec")).toHaveText(["Aktsiad", "ETF-id"]);
  const aapl = page.locator("tr[data-symbol=AAPL]");
  await expect(aapl.locator(".ak-symbol")).toHaveAttribute("href", "/aktsiad/AAPL");
  await expect(aapl).toContainText("Apple Inc.");
  await expect(aapl).toContainText("Pikk hoidmine");
  await expect(aapl).toContainText("#tehnoloogia");
  await expect(aapl.locator("td").nth(1)).toContainText("USD");
  // Every change carries its sign in the text and a data-suund; colour only repeats it.
  for (const cell of await aapl.locator("[data-suund]:not(svg)").all()) {
    await expect(cell).toHaveText(/^([+−]\d|0,0 %)/);
  }
  await expect(aapl.locator("svg.ak-spark")).toHaveAttribute("aria-label", /^90 päeva: [+−]/);
  await expect(aapl.locator(".ak-hinnang")).toHaveText("▲ Ostaks");
  await expect(aapl.locator(".ak-kp")).not.toBeEmpty();
  await expect(page.locator("tr[data-symbol=VWCE\\.DE] td").nth(1)).toContainText("EUR");
  await expect(page.locator("tr[data-symbol=SPY]")).toHaveCount(0); // the benchmark is not followed
  await page.screenshot({ path: "tests/visual/results/stocks-market-watchlist-light.png", fullPage: true });
  expect(problems).toEqual([]);
});

test("search, add, edit the note and tags, remove", async ({ page }) => {
  await page.getByLabel("Lisa jälgimisse").fill("nvid");
  await page.getByRole("button", { name: "Otsi", exact: true }).click();
  const match = page.locator(".ak-leiud li[data-symbol=NVDA]");
  await expect(match).toContainText("NVIDIA Corporation");
  await expect(match.getByLabel("NVDA valuuta")).toHaveValue("USD");
  await match.getByRole("button", { name: "Lisa" }).click();
  await expect(page.locator("#toast")).toHaveText("NVDA lisatud.");
  await expect(match.getByRole("button", { name: "Jälgid" })).toBeDisabled();
  // The backfill runs on a thread; the row fills in once its bars are stored.
  const row = page.locator("tr[data-symbol=NVDA]");
  await expect(row.locator("td").nth(1)).toContainText("USD", { timeout: 15_000 });
  await expect(row.locator("svg.ak-spark")).toBeVisible();

  const aapl = page.locator("tr[data-symbol=AAPL]");
  await aapl.getByRole("button", { name: "Muuda AAPL märkust ja silte" }).click();
  const form = page.locator("form.ak-muuda");
  await form.getByLabel("Märkus").fill("Ootan tulemusi");
  await form.getByLabel("Sildid").fill("Tehnoloogia, #USA");
  await form.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("AAPL salvestatud.");
  await expect(aapl).toContainText("Ootan tulemusi");
  await expect(aapl.locator(".ak-silt")).toHaveText(["#tehnoloogia", "#usa"]);

  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: "Eemalda NVDA jälgimisest" }).click();
  await expect(page.locator("#toast")).toHaveText("NVDA eemaldatud.");
  await expect(row).toHaveCount(0);
  await page.reload();
  await waitReady(page);
  await expect(page.locator("tr[data-symbol=NVDA]")).toHaveCount(0);
  await expect(page.locator("tr[data-symbol=AAPL] .ak-markus")).toHaveText("Ootan tulemusi");
});

test("a failing search says so in Estonian", async ({ page }) => {
  await fakeMarket(page, { failure: "throttle" });
  await page.getByLabel("Lisa jälgimisse").fill("apple");
  await page.getByRole("button", { name: "Otsi", exact: true }).click();
  await expect(page.locator(".ak-leiud .error")).toHaveText("Sümbolite otsing ei tööta praegu. Proovi minuti pärast uuesti.");
});

test("an empty watchlist says what to do", async ({ page }) => {
  await seed(page, { instruments: [] });
  await page.reload();
  await waitReady(page);
  await expect(page.locator(".ak-tuhi-leht")).toHaveText("Sa ei jälgi veel ühtki aktsiat. Otsi ülalt sümbolit või nime.");
});

test.describe("dark theme", () => {
  test.use({ colorScheme: "dark" });
  test("the table uses the dark tokens", async ({ page }) => {
    await page.evaluate(() => localStorage.removeItem("teema"));
    await page.reload();
    await waitReady(page);
    await expect(page.locator("html")).toHaveAttribute("data-teema", "tume");
    const up = page.locator("tr[data-symbol] [data-suund=up]").first();
    if (await up.count()) await expect(up).toHaveCSS("color", "rgb(95, 211, 141)"); // --ok, dark
    await page.screenshot({ path: "tests/visual/results/stocks-market-watchlist-dark.png", fullPage: true });
  });
});
