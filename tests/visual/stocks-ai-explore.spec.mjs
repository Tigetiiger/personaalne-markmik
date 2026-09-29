// Aktsiad → Avasta (stocks-ai, site/stocks/explore.js) with the fake model (AI_FAKE), fake pages
// from the package's test-only routes and the market package's fake market for the symbol search.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-ai-explore-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad/avasta");
  await page.context().storageState({ path: STATE });
  await page.close();
});
test.afterEach(async ({ page }) => {
  await api(page, "/_test/stocks-ai/fake", { method: "DELETE" });
});

const ASML = "https://www.asml.naide.test/investors/q2";
const NOVO = "https://uudised.naide.test/novo";
const LONG = "Orders for lithography systems rose again this quarter, the company said. ".repeat(6);

async function seed(page, body) {
  expect((await api(page, "/_test/stocks-ai/seed", { method: "POST", body })).status).toBe(204);
}

function idea(symbol, name, links, over = {}) {
  return {
    symbol, name, exchange: "NASDAQ", kind: "stock", what_it_is: `${name} teeb kiibitööstusele masinaid.`, why_interesting: "Tellimused kasvavad.",
    catalysts: [{ event: "Q3 tulemused", date: "2026-10-15" }], risks: ["Tsüklilisus"], horizon: "years", conviction: "medium",
    links: links.map((url) => ({ url, title: "Kvartali tulemused", why: "Näitab tellimusi." })), ...over,
  };
}

test("Avasta asks first, streams its steps, lists the checked ideas and adds one to the watchlist", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/avasta");
  await waitReady(page);
  await seed(page, { instruments: [{ symbol: "NVDA", name: "NVIDIA Corporation" }] });
  expect((await api(page, "/_test/stocks/fake-market", { method: "POST", body: { symbols: [{ symbol: "ASML", name: "ASML Holding N.V.", exchange: "NMS", days: 300 }] } })).status).toBe(204);
  expect((await api(page, "/_test/stocks-ai/fake", {
    method: "POST",
    body: {
      delay: 0.3,
      pages: { [ASML]: `<html><body><p>${LONG}</p></body></html>`, [NOVO]: `<html><body><p>${LONG}</p></body></html>` },
      answers: {
        explore_discover: { candidates: [{ url: ASML, title: "ASML Q2", symbol: "ASML", why: "Tellimused." }, { url: NOVO, title: "Novo", symbol: "NVO", why: "Ravim." }] },
        explore: {
          market_context: "Kiibitööstuse tellimused taastuvad.",
          ideas: [idea("ASML", "ASML Holding", [ASML, "https://made-up.naide.test/x"]), idea("NVDA", "NVIDIA", [ASML]), idea("MU", "Micron", ["https://made-up.naide.test/mu"])],
        },
      },
    },
  })).status).toBe(204);
  await page.goto("/aktsiad/avasta");
  await waitReady(page);
  await expect(page.getByText("Avastusi pole veel.")).toBeVisible();

  await page.getByLabel("Fookus (valikuline)").fill("pooljuhid");
  await page.getByLabel(/Ideid/).fill("4");
  let question = "";
  page.once("dialog", (d) => {
    question = d.message();
    d.accept();
  });
  await page.getByRole("button", { name: "Avasta" }).click();
  await expect(page.locator(".ak-ai-olek")).toHaveText(/Loen [12]\/2 lehte…/);
  expect(question).toContain("Claude'i tellimust");
  await expect(page.locator(".ak-ai-olek")).toHaveText("Valmis.", { timeout: 15_000 });

  const run = page.locator(".ak-avastus").first();
  await expect(run.getByRole("heading", { level: 2 })).toContainText("pooljuhid");
  await expect(run).toContainText("1 ideed 4-st küsitust");
  await expect(run).toContainText("Kiibitööstuse tellimused taastuvad.");
  const card = run.locator('.ak-idee[data-symbol="ASML"]');
  await expect(card).toContainText("ASML Holding");
  await expect(card).toContainText("horisont: aastad");
  await expect(card).toContainText("keskmine kindlus");
  await expect(card).toContainText("15.10.2026: Q3 tulemused");
  const link = card.getByRole("link", { name: "Kvartali tulemused" });
  await expect(link).toHaveAttribute("href", ASML);
  await expect(link).toHaveAttribute("rel", /noopener/);
  await expect(card.getByRole("link")).toHaveCount(1);
  await run.getByText("Kontrollis eemaldatud").click();
  await expect(run.locator(".ak-eemaldatud")).toContainText("NVDA: idee (juba jälgid)");
  await expect(run.locator(".ak-eemaldatud")).toContainText("MU: idee (ühtegi loetud allikat)");

  await card.getByRole("button", { name: "Lisa jälgimisse" }).click();
  const match = card.locator('.ak-leiud li[data-symbol="ASML"]');
  await expect(match).toContainText("ASML Holding N.V.");
  await match.getByRole("button", { name: "Lisa" }).click();
  await expect(card.getByRole("link", { name: "Ava ASML" })).toBeVisible();
  await expect(card.getByRole("button", { name: "Jälgid" }).first()).toBeDisabled();
  const watch = await api(page, "/api/stocks/watchlist");
  expect(watch.data.map((r) => r.symbol).sort()).toEqual(["ASML", "NVDA"]);
  expect(problems).toEqual([]);
});

test("earlier runs are listed and an idea count out of range is refused", async ({ page }) => {
  await page.goto("/aktsiad/avasta");
  await waitReady(page);
  await seed(page, {
    instruments: [{ symbol: "NVDA", name: "NVIDIA Corporation" }],
    explorations: [{ focus: null, requested_ideas: 6, market_context: "Varasem taust.", created_at: "2026-09-20T08:00:00Z",
                     ideas: [idea("NVDA", "NVIDIA", ["javascript:alert(1)"])], dropped: [{ kind: "link", symbol: "X", url: "https://x.naide.test", reason: "not fetched" }] }],
  });
  await page.goto("/aktsiad/avasta");
  await waitReady(page);
  const run = page.locator(".ak-avastus");
  await expect(run.getByRole("heading", { level: 2 })).toHaveText("20.9.2026, 11:00");
  await expect(run).toContainText("Varasem taust.");
  const card = run.locator('.ak-idee[data-symbol="NVDA"]');
  await expect(card.getByRole("link")).toHaveCount(0); // a javascript: link is never made a link
  await expect(card.getByRole("button", { name: "Jälgid" })).toBeDisabled();

  let asked = false;
  page.once("dialog", (d) => {
    asked = true;
    d.dismiss();
  });
  await page.getByLabel(/Ideid/).fill("11");
  await page.getByRole("button", { name: "Avasta" }).click();
  await expect(page.locator(".ak-ai-olek.error")).toHaveText("Ideede arv peab olema 3–10.");
  expect(asked).toBe(false);
});
