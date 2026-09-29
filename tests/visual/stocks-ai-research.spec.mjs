// A stock page's "Öine hinnang" and "Uuring" sections (stocks-ai, site/stocks/research.js), and
// Uuri kohe with the fake model (AI_FAKE) and fake pages from the package's test-only routes.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-ai-research-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad");
  await page.context().storageState({ path: STATE });
  await page.close();
});
test.afterEach(async ({ page }) => {
  await api(page, "/_test/stocks-ai/fake", { method: "DELETE" });
});

const Q2 = "https://investor.naide.test/q2";
const RISK = "https://uudised.naide.test/risk";
const LONG = "The company reported record data centre revenue and raised its outlook. ".repeat(6);

async function seed(page, body) {
  const r = await api(page, "/_test/stocks-ai/seed", { method: "POST", body });
  expect(r.status).toBe(204);
}

async function fake(page, body) {
  const r = await api(page, "/_test/stocks-ai/fake", { method: "POST", body });
  expect(r.status).toBe(204);
}

async function open(page, symbol) {
  await page.goto(`/aktsiad/${symbol}`);
  await waitReady(page);
}

const NVDA = { symbol: "NVDA", name: "NVIDIA Corporation" };

test("the latest call and note, with the earlier ones folded away", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad");
  await seed(page, {
    instruments: [NVDA],
    verdicts: [
      { symbol: "NVDA", as_of: "2026-09-28", verdict: "buy", conviction: "medium", summary: "Nõudlus on tugev.", reasons_for: ["Andmekeskuste tellimused kasvavad"], reasons_against: ["Hind on kõrge"], what_would_change: ["Tellimuste langus"] },
      { symbol: "NVDA", as_of: "2026-09-27", verdict: "sell", conviction: "low", summary: "Liiga kallis." },
    ],
    analyses: [
      {
        symbol: "NVDA", as_of: "2026-09-28", kind: "deep", summary: "Andmekeskuste nõudlus kasvab.", what_changed: "Prognoos tõusis.",
        bull: [{ text: "Juhtkond tõstis prognoosi.", citation: 0 }], bear: [{ text: "Kahtlane allikas.", citation: 1 }],
        catalysts: [{ event: "Q3 tulemused", date: "2026-11-18" }], risks: ["Ekspordipiirangud"], what_would_change_my_mind: ["Tellimuste langus"],
        citations: [{ url: Q2, title: "Q2 tulemused", quote: "raised its outlook", published_at: "2026-08-27" }, { url: "javascript:alert(1)", title: "Paha", quote: "x" }],
      },
      { symbol: "NVDA", as_of: "2026-09-24", kind: "on_demand", summary: "Varasem vaade.", bull: [{ claim: "Imporditud väide", citation: 0 }], citations: [{ url: RISK, quote: "q" }] },
    ],
  });
  await open(page, "NVDA");

  const verdict = page.locator(".ak-hinnang-jaotis");
  await expect(verdict.getByRole("heading", { name: "Öine hinnang" })).toBeVisible();
  await expect(verdict.locator(".ak-otsus .ak-hinnang")).toHaveText("▲ Ostaks");
  await expect(verdict.locator(".ak-otsus")).toContainText("keskmine kindlus");
  await expect(verdict).toContainText("Nõudlus on tugev.");
  await expect(verdict).toContainText("Andmekeskuste tellimused kasvavad");
  await expect(verdict).toContainText("Hind on kõrge");
  await expect(verdict).toContainText("Tellimuste langus");
  await verdict.getByText("Varasemad hinnangud (1)").click();
  await expect(verdict.locator(".ak-varasemad-loend li")).toContainText("▼ Müüks");
  await expect(verdict.locator(".ak-varasemad-loend li")).toContainText("Liiga kallis.");

  const research = page.locator(".ak-uuring-jaotis");
  await expect(research.getByRole("heading", { name: "Uuring", exact: true })).toBeVisible();
  const latest = research.locator(".ak-uuring").first();
  await expect(latest.locator(".ak-muutus")).toHaveText("Mis muutus: Prognoos tõusis.");
  await expect(latest).toContainText("Andmekeskuste nõudlus kasvab.");
  const cite = latest.locator(".ak-viide a");
  await expect(cite).toHaveCount(1);
  await expect(cite).toHaveAttribute("href", Q2);
  await expect(cite).toHaveAttribute("rel", /noopener/);
  await expect(latest.locator(".ak-viide").nth(1)).toHaveText("[2]"); // a javascript: citation is never a link
  await expect(latest).toContainText("18.11.2026: Q3 tulemused");
  await expect(latest.locator(".ak-allikad-loend")).toHaveAttribute("open", "");
  await expect(latest.locator(".ak-allikad-loend a")).toHaveText("Q2 tulemused");
  await expect(research.getByRole("button", { name: "Uuri kohe" })).toBeEnabled();

  const earlier = research.locator(".ak-varasem-uuring");
  await expect(earlier).toHaveCount(1);
  await earlier.locator(":scope > summary").click();
  await expect(earlier).toContainText("Imporditud väide");
  await expect(earlier.locator(":scope > summary")).toContainText("24.9.2026 · Uuri kohe");
  expect(problems).toEqual([]);
});

test("Uuri kohe asks first, streams its steps and shows the new note", async ({ page }) => {
  await page.goto("/aktsiad");
  await seed(page, { instruments: [NVDA] });
  await fake(page, {
    delay: 0.4,
    pages: { [Q2]: `<html><body><h1>Q2</h1><p>${LONG}</p></body></html>`, [RISK]: `<html><body><p>${LONG}</p></body></html>` },
    answers: {
      discover: { candidates: [{ url: Q2, title: "Q2", why: "Tulemused." }, { url: RISK, title: "Risk", why: "Riskid." }] },
      research: {
        summary: "Värske uuring: nõudlus kasvab.", changes_since_last: "Esimene märge.", bull: [{ text: "Tulemused paranesid.", citation: 0 }],
        bear: [{ text: "Väljamõeldud.", citation: 1 }], catalysts: [], risks: [], what_would_change_my_mind: [],
        citations: [{ url: Q2, title: "Q2", quote: "raised", published_at: null }, { url: "https://made-up.naide.test/x", title: "x", quote: "x", published_at: null }],
      },
    },
  });
  await open(page, "NVDA");
  const research = page.locator(".ak-uuring-jaotis");
  await expect(research).toContainText("Uuringut pole veel.");

  // Declined: nothing runs.
  page.once("dialog", (d) => d.dismiss());
  await research.getByRole("button", { name: "Uuri kohe" }).click();
  await expect(research.locator(".ak-ai-olek")).toHaveText("");

  let question = "";
  page.once("dialog", (d) => {
    question = d.message();
    d.accept();
  });
  await research.getByRole("button", { name: "Uuri kohe" }).click();
  await expect(research.locator(".ak-ai-olek")).toHaveText(/Loen [12]\/2 lehte…/);
  expect(question).toContain("Claude'i tellimust");
  await expect(research.getByRole("button", { name: "Uuri kohe" })).toBeDisabled();
  await expect(research.locator(".ak-ai-olek")).toHaveText("Valmis.", { timeout: 15_000 });
  await expect(research.locator(".ak-uuring")).toContainText("Värske uuring: nõudlus kasvab.");
  await expect(research.locator(".ak-ai-meta").first()).toContainText("Uuri kohe");
  await expect(research.locator(".ak-uuring")).toContainText("Tulemused paranesid.");
  await expect(research.locator(".ak-uuring")).not.toContainText("Väljamõeldud.");
  await expect(research.getByRole("button", { name: "Uuri kohe" })).toBeEnabled();
});

test("a run that fails says why, and another run waits its turn", async ({ page }) => {
  await page.goto("/aktsiad");
  await seed(page, { instruments: [NVDA] });
  await fake(page, { answers: { discover: { candidates: [] }, research: { summary: "", changes_since_last: "", bull: [], bear: [], catalysts: [], risks: [], what_would_change_my_mind: [], citations: [] } } });
  await open(page, "NVDA");
  const research = page.locator(".ak-uuring-jaotis");
  page.once("dialog", (d) => d.accept());
  await research.getByRole("button", { name: "Uuri kohe" }).click();
  await expect(research.locator(".ak-ai-olek.error")).toHaveText("AI vastus on vigane (kokkuvõte puudub)");
  await expect(research.getByRole("button", { name: "Uuri kohe" })).toBeEnabled();

  await seed(page, { instruments: [NVDA], claim: true });
  await open(page, "NVDA");
  await expect(research.getByRole("button", { name: "Uuri kohe" })).toBeDisabled();
  await expect(research.locator(".ak-ai-olek")).toHaveText("Üks AI-uuring juba käib.");
  expect((await api(page, "/api/stocks/research/NVDA", { method: "POST", body: {} })).status).toBe(409);
});

test("an ETF gets neither a call nor Uuri kohe", async ({ page }) => {
  await page.goto("/aktsiad");
  await seed(page, { instruments: [{ symbol: "VWCE.DE", name: "Vanguard FTSE All-World", kind: "etf" }] });
  await open(page, "VWCE.DE");
  await expect(page.locator(".ak-hinnang-jaotis")).toContainText("ETF-idele öist hinnangut ei anta.");
  await expect(page.locator(".ak-uuring-jaotis")).toContainText("Uuringut pole veel. Süvauuring tehakse seatud öödel.");
  await expect(page.locator(".ak-uuring-jaotis").getByRole("button", { name: "Uuri kohe" })).toBeHidden();
});
