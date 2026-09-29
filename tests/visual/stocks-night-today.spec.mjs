// Täna's "Turg" card (stocks-night): the day's move of the holdings, earnings today or tomorrow,
// the verdicts last night changed; hidden while nothing is followed. Data through the package's
// test-only seed route (MARKMIK_TEST_PAGES), which first empties every stocks table.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-night-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/tana");
  await page.context().storageState({ path: STATE });
  await page.close();
});

const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
const plus = (days) => {
  const d = new Date(`${today}T12:00`);
  d.setDate(d.getDate() + days);
  return new Intl.DateTimeFormat("sv-SE").format(d);
};

async function seed(page, body) {
  const r = await api(page, "/_test/stocks-night/seed", { method: "POST", body });
  expect(r.status).toBe(200);
}

const SEED = {
  instruments: [
    { symbol: "NVDA", name: "NVIDIA Corp", held: 10, bars: [{ date: plus(-2), close: "100" }, { date: plus(-1), close: "103.25" }] },
    { symbol: "VWCE.DE", name: "Vanguard FTSE All-World", currency: "EUR", held: 5, bars: [{ date: plus(-2), close: "120" }, { date: plus(-1), close: "117.6" }] },
    { symbol: "AAPL", name: "Apple Inc." },
  ],
  earnings: [
    { symbol: "AAPL", date: today, time_of_day: "amc" },
    { symbol: "NVDA", date: plus(1) },
    { symbol: "NVDA", date: plus(5) },
  ],
  verdicts: [
    { symbol: "AAPL", as_of: plus(-1), verdict: "hold", conviction: "medium" },
    { symbol: "AAPL", as_of: today, verdict: "buy", conviction: "high" },
    { symbol: "NVDA", as_of: today, verdict: "hold", conviction: "low" },
  ],
};

test("the Turg card on Täna: moves, earnings, changed verdicts, links", async ({ page }) => {
  await page.goto("/tana");
  await waitReady(page);
  await seed(page, SEED);
  await page.reload();
  await waitReady(page);
  const card = page.locator("#tana-lisad .today-block.turg");
  await expect(card.locator("h2")).toHaveText("Turg");
  const rows = card.locator(".turg-read > li");
  await expect(rows).toHaveCount(5);
  // Holdings by the size of the move; the direction is a shape and a sign, not colour alone.
  await expect(rows.nth(0)).toHaveText(/▲ \+3,25 %\s*NVDA\s*NVIDIA Corp\s*103,25 USD/);
  await expect(rows.nth(1)).toHaveText(/▼ −2,00 %\s*VWCE\.DE\s*Vanguard FTSE All-World\s*117,60 EUR/);
  await expect(rows.nth(0).locator("[data-suund]")).toHaveAttribute("data-suund", "up");
  await expect(rows.nth(1).locator("[data-suund]")).toHaveAttribute("data-suund", "down");
  await expect(rows.nth(2)).toHaveText(/täna\s*AAPL\s*Apple Inc\.\s*tulemused pärast sulgemist/);
  await expect(rows.nth(3)).toHaveText(/homme\s*NVDA\s*NVIDIA Corp\s*tulemused$/);
  await expect(rows.nth(4)).toHaveText(/hinnang\s*AAPL\s*Apple Inc\.\s*Hoiaks ◆ → Ostaks ▲/);
  await rows.nth(1).locator("a.turg-symbol").click();
  await expect(page).toHaveURL("/aktsiad/VWCE.DE");
});

test("the card is hidden while nothing is followed, and quiet when nothing happened", async ({ page }) => {
  await page.goto("/tana");
  await waitReady(page);
  await seed(page, {});
  await page.reload();
  await waitReady(page);
  await expect(page.locator("#tana-sisu .today-block").first()).toBeVisible();
  await expect(page.locator("#tana-lisad")).toBeEmpty();

  await seed(page, { instruments: [{ symbol: "AAPL", name: "Apple Inc." }] });
  await page.reload();
  await waitReady(page);
  await expect(page.locator("#tana-lisad .turg .today-empty")).toHaveText("Täna pole portfellis ega jälgitavate seas midagi erilist.");
});

test("not on Eile / Homme", async ({ page }) => {
  await page.goto("/tana");
  await waitReady(page);
  await seed(page, SEED);
  await page.goto(`/tana?paev=${plus(1)}`);
  await waitReady(page);
  await expect(page.locator("#tana-lisad")).toBeEmpty();
});

test.afterAll(async ({ browser }) => {
  // Full runs share one database: leave no stocks behind for the other specs' Täna.
  const page = await browser.newPage({ storageState: STATE });
  await page.goto("/tana");
  await waitReady(page);
  await seed(page, {});
  await page.close();
});
