// Aktsiad shell (lead): the tabs, the top-bar link and Seaded → Aktsiad.
import { expect, test } from "@playwright/test";

import { login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-shell-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad");
  await page.context().storageState({ path: STATE });
  await page.close();
});

test("the tabs lead to each page and mark the current one", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad");
  await waitReady(page);
  await expect(page.locator("#nav a[aria-current=page]")).toHaveText("Aktsiad");
  const tabs = page.locator(".ak-sakid a");
  await expect(tabs).toHaveText(["Jälgin", "Portfell", "Tehingud", "Avasta", "Allikad"]);
  await tabs.filter({ hasText: "Portfell" }).click();
  await waitReady(page);
  await expect(page).toHaveURL(/\/aktsiad\/portfell$/);
  await expect(page.locator(".ak-sakid a[aria-current=page]")).toHaveText("Portfell");
  await page.goto("/aktsiad/NVDA");
  await waitReady(page);
  await expect(page.locator(".ak-sakid a[aria-current=page]")).toHaveText("NVDA");
  expect(problems).toEqual([]);
});

test("Seaded → Aktsiad saves and refuses a bad tax line", async ({ page }) => {
  await page.goto("/seaded");
  await waitReady(page);
  const form = page.locator(".ak-seaded");
  await expect(form.getByLabel("ntfy teema")).toHaveValue("stock-oracle");
  await form.getByLabel("Investeerimiskonto maksumäärad").fill("2026 kakskümmend");
  await form.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toContainText("kirjuta „aasta: määr“");
  await form.getByLabel("Investeerimiskonto maksumäärad").fill("2025: 0.22\n2026: 0,22");
  await form.getByLabel("Uuringu mudel").selectOption("opus");
  await form.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("Aktsiate seaded salvestatud.");
  await page.reload();
  await waitReady(page);
  await expect(form.getByLabel("Uuringu mudel")).toHaveValue("opus");
  await expect(form.getByLabel("Investeerimiskonto maksumäärad")).toHaveValue("2025: 0.22\n2026: 0.22");
  await form.getByLabel("Uuringu mudel").selectOption("sonnet");
  await form.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("Aktsiate seaded salvestatud.");
});

test("the Turuandmed card saves keys masked and keeps what an empty field leaves", async ({ page }) => {
  await page.goto("/seaded/uhendused");
  await waitReady(page);
  const card = page.locator('.uh-kaart[data-key="stocks"]');
  await expect(card.locator("h2")).toHaveText("Turuandmed");
  await expect(card).toContainText("finnhub.io");
  await card.getByLabel("SEC User-Agent").fill("ilma kontaktita");
  await card.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toContainText("e-posti aadressi");
  await card.getByLabel("Finnhubi võti").fill("vt-finnhub-voti-4321");
  await card.getByLabel("SEC User-Agent").fill("Markmik proov@example.com");
  await card.getByRole("button", { name: "Salvesta" }).click();
  await expect(card).toContainText("Salvestatud: ••••4321");
  await expect(card.getByLabel("Finnhubi võti")).toHaveValue("");
  expect(await page.content()).not.toContain("vt-finnhub-voti-4321");
  page.once("dialog", (d) => d.accept());
  await card.getByRole("button", { name: "Katkesta" }).click();
  await expect(card.locator(".uh-olek")).toHaveText("Pole ühendatud");
});
