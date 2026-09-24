// Postkast: proposed events are confirmed, edited or rejected; the top-bar count follows.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const PROPOSED = [
  {
    title: "Matemaatika kontrolltöö",
    kind: "kontrolltöö",
    start: "2026-10-02T10:15",
    end: "2026-10-02T11:00",
    source_quote: "Reedel kell 10.15 on matemaatika kontrolltöö.",
    confidence: "low",
    description: "Kirjas on „laupäeval“, aga 2.10 on reede.",
  },
  { title: "Ekskursioon", start: "2026-10-09", all_day: true, source_quote: "9. oktoobril läheme ekskursioonile." },
  { title: "Kontsert", start: "2026-10-15T18:00", source_quote: "Kontsert toimub 15.10 kell 18." },
];

// One login per file, reused through the saved session: /login allows 20 requests a minute per
// address, and in a full run every package's specs share that budget.
const STATE = "tests/visual/results/inbox-postkast-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/seaded");
  await page.context().storageState({ path: STATE });
  await page.close();
});

async function seed(page, items) {
  expect((await api(page, "/_test/inbox/seed", { method: "POST", body: items })).status).toBe(201);
  await page.reload();
  await waitReady(page);
}

function watchErrors(page) {
  const problems = [];
  page.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
}

test("confirm, edit and reject proposed events; the badge follows", async ({ page }) => {
  const problems = watchErrors(page);
  await page.goto("/postkast");
  await waitReady(page);
  await seed(page, PROPOSED);

  const cards = page.locator(".pk-kaart");
  const badge = page.locator("#postkast-arv");
  await expect(cards.locator("h2")).toHaveText(["Kontsert", "Ekskursioon", "Matemaatika kontrolltöö"]);
  await expect(badge).toHaveText("3");

  const mate = cards.filter({ hasText: "Matemaatika" });
  await expect(mate.locator("blockquote")).toHaveText("Reedel kell 10.15 on matemaatika kontrolltöö.");
  await expect(mate.locator(".pk-meta")).toHaveText("Kontrolltöö · reede, 2. oktoober 2026 kell 10:15–11:00 · allikas: Gmail · kindlus: madal");
  await expect(mate.locator(".pk-markus")).toContainText("2.10 on reede");
  await mate.getByRole("button", { name: "Kinnita", exact: true }).click();
  await expect(cards).toHaveCount(2);
  await expect(badge).toHaveText("2");
  await expect(page.locator("#toast")).toHaveText("Lisatud kalendrisse.");

  const trip = cards.filter({ hasText: "Ekskursioon" });
  await trip.getByRole("button", { name: "Muuda" }).click();
  await trip.getByLabel("Pealkiri").fill("Ekskursioon Tartusse");
  await trip.getByLabel("Liik").selectOption("sündmus");
  await expect(trip.getByLabel("Algus")).toBeHidden();
  await trip.getByLabel("Terve päev").uncheck();
  await trip.getByLabel("Algus").fill("08:30");
  await trip.getByRole("button", { name: "Salvesta ja kinnita" }).click();
  await expect(cards).toHaveCount(1);
  await expect(badge).toHaveText("1");

  await cards.getByRole("button", { name: "Loobu" }).click();
  await expect(page.locator(".pk-tyhi")).toContainText("Postkast on tühi");
  await expect(badge).toBeHidden();
  await expect(page.getByRole("button", { name: "Kinnita kõik" })).toBeHidden();
  expect((await api(page, "/api/inbox")).data).toEqual([]);
  expect(problems).toEqual([]);
});

test("the badge shows on other pages, and Kinnita kõik empties the Postkast", async ({ page }) => {
  await page.goto("/seaded");
  await waitReady(page);
  await seed(page, PROPOSED.slice(0, 2));
  await expect(page.locator("#postkast-arv")).toHaveText("2");

  await page.getByRole("link", { name: /Postkast/ }).click();
  await waitReady(page);
  await expect(page.locator(".pk-kaart")).toHaveCount(2);
  await page.getByRole("button", { name: "Kinnita kõik" }).click();
  await expect(page.locator(".pk-tyhi")).toBeVisible();
  await expect(page.locator("#postkast-arv")).toBeHidden();
  expect((await api(page, "/api/inbox/count")).data).toEqual({ count: 0 });
});
