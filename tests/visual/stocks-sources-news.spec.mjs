// A stock page's "Uudised" section (stocks-sources, site/stocks/news.js). The stock page itself
// belongs to stocks-market; until its ticker.js mounts the registered sections, this test mounts
// the section by hand from its registration, as the page will.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-sources-news-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad/NVDA");
  await page.context().storageState({ path: STATE });
  await page.close();
});

const ago = (hours) => new Date(Date.now() - hours * 3600_000).toISOString().replace(/\.\d+Z$/, "Z");

async function mountNews(page, symbol) {
  await page.evaluate(async (symbol) => {
    const { listTickerSections } = await import("/static/stocks/common.js");
    await import("/static/stocks/news.js");
    const section = listTickerSections().find((s) => s.id === "news");
    const el = document.createElement("div");
    el.id = "uudised-test";
    document.getElementById("aktsiad").append(el);
    await section.mount(el, { symbol });
  }, symbol);
}

test("Uudised lists the stock's documents with the model's reading", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/NVDA");
  await waitReady(page);
  const seed = {
    instruments: [{ symbol: "NVDA", name: "NVIDIA" }, { symbol: "AMD", name: "AMD" }],
    docs: [
      { symbol: "NVDA", title: "Nvidia unveils new Rubin chips", source: "finnhub", publisher: "Reuters", published_at: ago(5),
        url: "https://www.reuters.com/technology/nvidia-rubin", signal: { summary: "Uus kiibipõlvkond tuleb turule 2027. aastal.", sentiment: "0.42", event_type: "product" } },
      { symbol: "NVDA", title: "NVIDIA 4: HUANG JEN HSUN sold 76,500 shares", source: "edgar", published_at: ago(30),
        url: "https://www.sec.gov/Archives/x.xml", signal: { summary: "Juht müüs plaanipäraselt aktsiaid.", sentiment: "-0.3", event_type: "insider" } },
      { symbol: "NVDA", title: "Nvidia stock: what analysts say", source: "yahoo_rss", published_at: ago(50), url: "javascript:alert(1)" },
      { symbol: "AMD", title: "Not about Nvidia", source: "finnhub", published_at: ago(1) },
    ],
  };
  expect((await api(page, "/_test/stocks-sources/seed", { method: "POST", body: seed })).status).toBe(204);
  await mountNews(page, "NVDA");

  const box = page.locator("#uudised-test");
  await expect(box.getByRole("heading", { name: "Uudised" })).toBeVisible();
  const items = box.locator(".ak-uudis");
  await expect(items).toHaveCount(3);
  const first = items.first();
  const link = first.getByRole("link", { name: "Nvidia unveils new Rubin chips" });
  await expect(link).toHaveAttribute("href", "https://www.reuters.com/technology/nvidia-rubin");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);
  await expect(first).toContainText("Finnhub uudised · Reuters");
  await expect(first.locator(".ak-meelsus")).toHaveText("▲ positiivne +0,42");
  await expect(first).toContainText("toode");
  await expect(first.locator(".ak-uudis-kokku")).toHaveText("Uus kiibipõlvkond tuleb turule 2027. aastal.");
  await expect(items.nth(1).locator(".ak-meelsus")).toHaveText("▼ negatiivne −0,30");
  await expect(items.nth(1)).toContainText("siseringi tehing");
  // Not scored yet: no reading; a javascript: link is never made a link.
  const unscored = items.nth(2);
  await expect(unscored.locator(".ak-meelsus")).toHaveCount(0);
  await expect(unscored.getByRole("link")).toHaveCount(0);
  await expect(unscored).toContainText("Nvidia stock: what analysts say");
  expect(problems).toEqual([]);
});

test("Uudised says so when there is nothing yet", async ({ page }) => {
  await page.goto("/aktsiad/NVDA");
  await waitReady(page);
  expect((await api(page, "/_test/stocks-sources/seed", { method: "POST", body: { instruments: [{ symbol: "NVDA", name: "NVIDIA" }] } })).status).toBe(204);
  await mountNews(page, "NVDA");
  await expect(page.locator("#uudised-test")).toContainText("Uudiseid pole veel. Allikaid loetakse igal ööl.");
});
