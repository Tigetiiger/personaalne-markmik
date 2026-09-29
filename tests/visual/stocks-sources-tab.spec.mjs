// Aktsiad → Allikad (stocks-sources): source health, the nights, the model runs, "Loe kohe".
// Data through the test-only seed route (MARKMIK_TEST_PAGES), which also swaps in a fake ingest
// that reads nothing from the internet.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-sources-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad/allikad");
  await page.context().storageState({ path: STATE });
  await page.close();
});

const ago = (hours) => new Date(Date.now() - hours * 3600_000).toISOString().replace(/\.\d+Z$/, "Z");

const SEED = {
  instruments: [{ symbol: "NVDA", name: "NVIDIA" }],
  docs: [
    { symbol: "NVDA", title: "Nvidia unveils new Rubin chips", source: "finnhub", publisher: "Reuters", published_at: ago(5), url: "https://www.reuters.com/technology/nvidia-rubin",
      signal: { summary: "Uus kiibipõlvkond tuleb turule 2027. aastal.", sentiment: "0.42", event_type: "product" } },
    { symbol: "NVDA", title: "NVIDIA 4: HUANG JEN HSUN sold 76,500 shares", source: "edgar", published_at: ago(30), url: "https://www.sec.gov/Archives/x.xml",
      signal: { summary: "Juht müüs plaanipäraselt aktsiaid.", sentiment: "-0.3", event_type: "insider" } },
    { symbol: "NVDA", title: "Nvidia stock: what analysts say", source: "yahoo_rss", published_at: ago(50), url: "javascript:alert(1)" },
  ],
  runs: [
    { source: "finnhub", started_at: ago(26), status: "succeeded", fetched: 12, new: 3 },
    { source: "finnhub", started_at: ago(25), status: "failed", error: "NVDA: SourceError: GET https://finnhub.io/api/v1/company-news -> HTTP 401" },
    { source: "finnhub", started_at: ago(1), status: "failed", error: "NVDA: SourceError: GET https://finnhub.io/api/v1/company-news -> HTTP 401" },
    { source: "yahoo_rss", started_at: ago(1), status: "partial", fetched: 8, new: 2, error: "AMD: SourceError: yahoo_rss: unparseable feed" },
  ],
  nights: [
    { run_date: "2026-09-21", started_at: "2026-09-20T22:30:00Z", finished_at: "2026-09-20T23:05:00Z", status: "partial",
      steps: { prices: { status: "succeeded" }, ingest: { status: "failed", detail: "boom" }, extract: { status: "skipped" } } },
  ],
  ai: [
    { purpose: "research", symbol: "NVDA", model: "sonnet", started_at: ago(3), status: "success", duration_ms: 95000 },
    { purpose: "extract", model: "haiku", started_at: ago(2), status: "unavailable", error: "You've hit your limit" },
  ],
};

test("Allikad shows each source, the nights and the model runs", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/allikad");
  await waitReady(page);
  expect((await api(page, "/_test/stocks-sources/seed", { method: "POST", body: SEED })).status).toBe(204);
  await page.reload();
  await waitReady(page);

  const rows = page.locator("#ak-allikad tbody tr");
  await expect(rows.first()).toHaveAttribute("data-allikas", "edgar");
  await expect(page.locator("tr[data-allikas=edgar]")).toContainText("SEC User-Agent puudub");
  const finnhub = page.locator("tr[data-allikas=finnhub]");
  await expect(finnhub).toContainText("võti puudub");
  await expect(finnhub).toContainText("2 korda järjest ebaõnnestunud");
  await expect(finnhub).toContainText("HTTP 401");
  const yahoo = page.locator("tr[data-allikas=yahoo_rss]");
  await expect(yahoo.locator(".ak-olek")).toHaveText("osaliselt");
  await expect(page.locator("tr[data-allikas=alphavantage]")).toContainText("täna 0/25 päringut");

  const steps = page.locator("#ak-oo .ak-samm");
  await expect(steps).toHaveText(["Hinnad", "Uudised", "Hindamine"]);
  await expect(steps.nth(1)).toHaveAttribute("data-olek", "failed");
  await expect(steps.nth(1)).toHaveAttribute("title", "ebaõnnestus: boom");
  await expect(page.locator("#ak-oo tbody tr")).toContainText("35 min 00 s");

  const ai = page.locator("#ak-mudel tbody tr");
  await expect(ai.first()).toContainText("Uudiste hindamine");
  await expect(ai.first()).toContainText("piirang");
  await expect(ai.nth(1)).toContainText("Uuring");
  await expect(ai.nth(1)).toContainText("1 min 35 s");
  await expect(ai.nth(1).getByRole("link", { name: "NVDA" })).toHaveAttribute("href", "/aktsiad/NVDA");
  await expect(page.locator(".ak-all-kokku")).toContainText("2 päringut: 1 õnnestus, 0 ebaõnnestus, 1 jäi piirangu taha");
  expect(problems).toEqual([]);
});

test("Loe kohe reads the sources on the server and the table follows", async ({ page }) => {
  await page.goto("/aktsiad/allikad");
  await waitReady(page);
  expect((await api(page, "/_test/stocks-sources/seed", { method: "POST", body: SEED })).status).toBe(204);
  await page.reload();
  await waitReady(page);
  await page.getByRole("button", { name: "Loe kohe" }).click();
  await expect(page.locator("#toast")).toContainText("Loen allikaid");
  // The fake ingest writes one successful run of each keyless source.
  await expect(page.locator("tr[data-allikas=google_news] .ak-olek")).toHaveText("õnnestus", { timeout: 10_000 });
  await expect(page.locator("tr[data-allikas=yahoo_rss] .ak-olek")).toHaveText("õnnestus");
  await expect(page.getByRole("button", { name: "Loe kohe" })).toBeEnabled();
});

test("Allikad on a phone scrolls its tables, not the page", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/aktsiad/allikad");
  await waitReady(page);
  expect((await api(page, "/_test/stocks-sources/seed", { method: "POST", body: SEED })).status).toBe(204);
  await page.reload();
  await waitReady(page);
  await expect(page.locator("#ak-allikad")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
