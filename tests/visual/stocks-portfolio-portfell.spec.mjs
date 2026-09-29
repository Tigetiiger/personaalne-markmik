// Aktsiad → Portfell (stocks-portfolio): the summary, positions, the value chart, allocation,
// dividends, the investment account panel, and a stock's "Sinu positsioon".
//
// EUR/USD 1.10 throughout. Deposit 2000 EUR; 10 ACME at 110 USD (1000 EUR); 5 VWCE at 100 EUR;
// an ACME dividend of 11 USD with 1.65 withheld (8.50 EUR net). Last closes ACME 132 USD (1200
// EUR), VWCE 110 EUR. Cash 508.50, value 2258.50; unrealised +250 EUR (ACME +200 of which +200
// price, 0 currency). Contribution balance 2008.50, deferred tax 22 % x 250 = 55.00.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-portfolio-portfell-owner.json";
test.use({ storageState: STATE });
test.describe.configure({ mode: "serial" });

const iso = (d) => d.toLocaleDateString("sv-SE", { timeZone: "Europe/Tallinn" });
const daysAgo = (n) => iso(new Date(Date.now() - n * 864e5));

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad/portfell");
  await page.context().storageState({ path: STATE });
  const seeded = await api(page, "/_test/stocks-portfolio/seed", {
    method: "POST",
    body: {
      reset: true,
      instruments: [
        { symbol: "ACME", name: "Acme Corp", currency: "USD", kind: "stock" },
        { symbol: "VWCE", name: "Vanguard FTSE All-World", currency: "EUR", kind: "etf", exchange: "XETRA" },
      ],
      bars: { ACME: [[daysAgo(2), "120"], [daysAgo(1), "132"]], VWCE: [[daysAgo(2), "105"], [daysAgo(1), "110"]] },
      fx: [["2023-01-02", "USD", "1.10"]],
    },
  });
  expect(seeded.status).toBe(201);
  const { ACME, VWCE } = seeded.data.ids;
  const account = (await api(page, "/api/stocks/accounts", { method: "POST", body: { name: "LHV IK", kind: "investment_account" } })).data.id;
  const add = async (body) => expect((await api(page, "/api/stocks/tx", { method: "POST", body: { account_id: account, ...body } })).status).toBe(201);
  const year = new Date().getFullYear();
  await add({ kind: "deposit", date: `${year}-01-02`, amount: "2000" });
  await add({ kind: "buy", date: `${year}-01-05`, instrument_id: ACME, qty: "10", price: "110" });
  await add({ kind: "buy", date: `${year}-02-02`, instrument_id: VWCE, qty: "5", price: "100" });
  await add({ kind: "dividend", date: daysAgo(3), instrument_id: ACME, amount: "11", tax_withheld: "1.65" });
  await page.close();
});

const squash = (s) => s.replace(/\s+/g, " ");

test("the summary, positions, allocation, dividends and the investment account", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/portfell");
  await waitReady(page);
  const summary = squash(await page.locator("#pf-kokkuvote").innerText());
  expect(summary).toMatch(/Väärtus 2 ?258,50 EUR/);
  expect(summary).toContain("Realiseerimata +250,00 EUR hinnast +250,00, valuutast 0,00");
  await expect(page.locator("#pf-kokkuvote [data-suund=up]").first()).toBeVisible();

  const acme = page.locator("#pf-positsioonid tr[data-symbol=ACME] td");
  await expect(acme.nth(1)).toHaveText("10");
  await expect(acme.nth(4)).toHaveText(/^1 ?200,00$/);
  const eurGain = acme.nth(7).locator("[data-suund]");
  await expect(eurGain.first()).toHaveText("+200,00");
  await expect(eurGain.first()).toHaveAttribute("data-suund", "up");
  await expect(acme.nth(7)).toContainText("hinnast +200,00, valuutast 0,00");

  const bars = squash(await page.locator("#pf-jaotus").innerText());
  expect(bars).toContain("ACME 68,6 %");
  expect(bars).toContain("ETF-id 31,4 %");
  await expect(page.locator("#pf-dividendid")).toContainText("11,00 USD");

  const ia = squash(await page.locator("#pf-ik").innerText());
  expect(ia).toMatch(/Sissemaksete jääk 2 ?008,50 EUR/);
  expect(ia).toContain("Edasilükkunud tulumaks (22 %) 55,00 EUR");
  await expect(page.locator("#pf-maksuhoiatus")).toContainText("EMTA");
  expect(problems).toEqual([]);
});

test("the value chart has a crosshair readout and a table view", async ({ page }) => {
  await page.goto("/aktsiad/portfell");
  await waitReady(page);
  const chart = page.locator(".pf-joonis svg");
  await expect(chart.locator(".pf-joon-vaartus")).toHaveCount(1);
  await expect(chart.locator(".pf-joon-sisse")).toHaveCount(1);
  // The chart can be redrawn once more after the page settles (its data arrives in parts); a box
  // read from a detached svg is null, so wait for one that stays.
  let box = null;
  await expect.poll(async () => (box = await chart.boundingBox())).not.toBeNull();
  await page.mouse.move(box.x + box.width - 20, box.y + box.height / 2);
  await expect(page.locator(".pf-naidik")).toBeVisible();
  await expect(page.locator(".pf-naidik")).toContainText("sissemakstud");
  await page.mouse.move(box.x - 50, box.y - 50);
  await expect(page.locator(".pf-naidik")).toBeHidden();
  await chart.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".pf-naidik")).toBeVisible();
  await page.locator(".pf-tabelina summary").click();
  await expect(page.locator(".pf-tabelina tbody tr").first()).toContainText(/2\s?258,50/);
  await page.locator(".pf-tabelina summary").click();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.screenshot({ path: "tests/visual/results/stocks-portfolio-light.png", fullPage: true });
  await page.evaluate(() => (document.documentElement.dataset.teema = "tume"));
  await page.screenshot({ path: "tests/visual/results/stocks-portfolio-dark.png", fullPage: true });
});

test("a stock's page shows the position", async ({ page }) => {
  await page.goto("/aktsiad/ACME");
  await waitReady(page);
  // The page mounts the registered sections once stocks-market's ticker.js is in; the stub does not.
  test.skip(await page.getByText("Tuleb peagi.").isVisible(), "stock page is still the stub");
  const section = page.locator(".pf-positsioon");
  await expect(section.getByRole("heading", { name: "Sinu positsioon" })).toBeVisible();
  await expect(section).toContainText("LHV IK 10");
  await page.goto("/aktsiad/VWCE");
  await waitReady(page);
  await expect(page.locator(".pf-positsioon")).toContainText("Kogus");
});

test("the position API answers null for a stock not held", async ({ page }) => {
  await page.goto("/aktsiad/portfell");
  await waitReady(page);
  expect((await api(page, "/api/stocks/position/ACME")).data.qty).toBe("10");
  expect((await api(page, "/api/stocks/position/NOPE")).data).toBeNull();
});
