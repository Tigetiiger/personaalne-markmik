// The stock page /aktsiad/<SYMBOL> (stocks-market): chart, ranges and bar sizes, legend, the vs-SPY
// pane, readout, trend card, key stats, attribution. Yahoo is a fake market inside the test server.
import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";
import { SPY, daysAgo, fakeMarket, seed } from "./stocks-market-helpers.mjs";

const STATE = "tests/visual/results/stocks-market-ticker-owner.json";
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
  await fakeMarket(page, { symbols: [{ symbol: "AAPL", name: "Apple Inc.", days: 1300 }, { symbol: "NEWCO", name: "New Company", days: 600 }] });
  await seed(page, {
    instruments: [SPY, { symbol: "AAPL", name: "Apple Inc.", note: "Pikk hoidmine" }],
    earnings: [{ symbol: "AAPL", date: daysAgo(40) }],
    analyses: [{ symbol: "AAPL", as_of: daysAgo(100) }],
  });
});

test("the chart with its ranges, legend, vs-SPY pane, readout and cards", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/AAPL");
  await waitReady(page);
  await expect(page.locator("h1")).toContainText("AAPL");
  await expect(page.locator(".ak-pais .sub")).toHaveText("Apple Inc. · US · USD");
  await expect(page.locator(".ak-hind-suur dd")).toContainText("USD");
  await expect(page.locator(".ak-hind [data-suund]")).toHaveText(/^([+−]\d|0,00 %)/);
  await expect(page.locator(".ak-graafik-pind canvas").first()).toBeVisible();
  await expect(page.locator(".ak-graafik-pind")).toHaveAttribute("data-paanid", "2");

  const ranges = page.locator(".ak-vahemikud button");
  await expect(ranges).toHaveText(["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "2Y", "5Y"]);
  await expect(page.locator(".ak-vahemikud button[aria-pressed=true]")).toHaveText("1Y");
  const note = page.locator(".ak-tulbad");
  await expect(note).toHaveText("Päevatulbad");
  await expect(page.locator(".ak-lugem")).toContainText("SMA 200");
  await expect(page.locator(".ak-lugem")).toContainText("SPY vastu");
  await expect(page.locator(".ak-legend")).toContainText("E tulemused · R uuring");
  await expect(page.getByText(/^Alumine paan: tootlus võrreldes SPY-ga/)).toBeVisible();
  const credit = page.getByRole("link", { name: "TradingView Lightweight Charts™" });
  await expect(credit).toBeVisible();
  await expect(credit).toHaveAttribute("href", "https://www.tradingview.com/");

  await page.screenshot({ path: "tests/visual/results/stocks-market-ticker-light.png", fullPage: true });

  // The legend toggles the averages; the readout follows.
  await page.getByLabel("SMA 50").uncheck();
  await expect(page.locator(".ak-lugem")).not.toContainText("SMA 50");
  await page.getByLabel("SMA 50").check();
  await expect(page.locator(".ak-lugem")).toContainText("SMA 50");

  // The crosshair readout.
  const box = await page.locator(".ak-graafik-pind").boundingBox();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.3);
  await expect(page.locator(".ak-lugem")).toContainText(/^\d{1,2}\.\d{1,2}\.\d{4} · avam\. /);

  for (const [range, bars] of [["2Y", "Nädalatulbad"], ["5Y", "Kuutulbad"], ["3M", "Päevatulbad"], ["YTD", "Päevatulbad"]]) {
    await ranges.filter({ hasText: new RegExp(`^${range}$`) }).click();
    await expect(page.locator(".ak-vahemikud button[aria-pressed=true]")).toHaveText(range);
    await expect(note).toHaveText(new RegExp(`^${bars}`)); // 5Y shows it all: "… Salvestatud ajaloo algus."
  }
  await ranges.filter({ hasText: /^1D$/ }).click();
  await expect(note).toHaveText(/^5-minutilised tulbad, Tallinna aeg/);
  await expect(page.locator(".ak-lugem")).not.toContainText("SPY vastu");
  await expect(page.getByText(/^Alumine paan/)).toBeHidden();
  await ranges.filter({ hasText: /^5D$/ }).click();
  await expect(note).toHaveText(/^15-minutilised tulbad/);
  await ranges.filter({ hasText: /^1M$/ }).click();
  await expect(note).toHaveText(/^Tunnitulbad/);

  const trend = page.locator(".ak-trend");
  await expect(trend.locator(".ak-seis strong")).toHaveText(/^(▲ Tõusutrend|▼ Langustrend|◆ Segane)$/);
  await expect(trend).toContainText("SPY vastu, 1 a");
  await expect(trend.locator(".ak-vahemik")).toHaveAttribute("aria-label", /^Hind on 52 nädala vahemiku \d+ % kõrgusel$/);
  const stats = page.locator(".ak-statistika dt");
  await expect(stats).toHaveText(["52 nädala kõrgeim", "52 nädala madalaim", "5 päeva", "20 päeva", "60 päeva", "1 aasta",
    "Volatiilsus 20 p, aastas", "Volatiilsus 60 p, aastas"]);
  expect(problems).toEqual([]);
});

test("SPY's own page has no vs-SPY pane", async ({ page }) => {
  await page.goto("/aktsiad/SPY");
  await waitReady(page);
  await expect(page.locator(".ak-graafik-pind canvas").first()).toBeVisible();
  await expect(page.locator(".ak-graafik-pind")).toHaveAttribute("data-paanid", "1");
  await expect(page.getByText(/^Alumine paan/)).toHaveCount(0);
  await expect(page.locator(".ak-trend")).not.toContainText("SPY vastu");
});

test("a symbol not stored yet can be added from its page and its history loads", async ({ page }) => {
  await page.goto("/aktsiad/NEWCO");
  await waitReady(page);
  await expect(page.locator(".ak-tundmatu")).toContainText("Sümbolit NEWCO siin veel pole.");
  await page.getByRole("button", { name: "Lisa jälgimisse" }).click();
  await waitReady(page);
  await expect(page.locator("h1")).toContainText("NEWCO");
  // Either the backfill was already done or the page waits for it.
  await expect(page.locator(".ak-graafik-pind canvas").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".ak-pais .sub")).toHaveText("New Company · US · USD");
});

test.describe("dark theme", () => {
  test.use({ colorScheme: "dark" });
  test("the chart draws in the dark theme and follows a switch", async ({ page }) => {
    const problems = [];
    page.on("pageerror", (e) => problems.push(e.message));
    await page.goto("/aktsiad/AAPL");
    await page.evaluate(() => localStorage.removeItem("teema"));
    await page.reload();
    await waitReady(page);
    await expect(page.locator("html")).toHaveAttribute("data-teema", "tume");
    await expect(page.locator(".ak-graafik-pind canvas").first()).toBeVisible();
    await page.screenshot({ path: "tests/visual/results/stocks-market-ticker-dark.png", fullPage: true });
    expect(problems).toEqual([]);
  });
});
