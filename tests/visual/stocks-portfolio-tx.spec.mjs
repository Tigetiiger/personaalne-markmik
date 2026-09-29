// Aktsiad → Tehingud (stocks-portfolio): accounts, the add form per kind, inline edit and delete,
// the replay refusal, #lisa and the CSV import.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/stocks-portfolio-tx-owner.json";
test.use({ storageState: STATE });
test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/aktsiad/tehingud");
  await page.context().storageState({ path: STATE });
  const r = await api(page, "/_test/stocks-portfolio/seed", {
    method: "POST",
    body: {
      reset: true,
      instruments: [
        { symbol: "ACME", name: "Acme Corp", currency: "USD", kind: "stock" },
        { symbol: "VWCE", name: "Vanguard FTSE All-World", currency: "EUR", kind: "etf", exchange: "XETRA" },
      ],
      fx: [["2023-01-02", "USD", "1.10"]],
    },
  });
  expect(r.status).toBe(201);
  await page.close();
});

test("an account first, then the form shows only the fields each kind uses", async ({ page }) => {
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto("/aktsiad/tehingud#lisa");
  await waitReady(page);
  // No account yet: #lisa lands on the account form.
  await expect(page.locator("#konto-nimi")).toBeFocused();
  await page.locator("#konto-nimi").fill("LHV IK");
  await page.locator("#konto-liik").selectOption("investment_account");
  await page.getByRole("button", { name: "Lisa konto" }).click();
  await expect(page.locator("#toast")).toHaveText("Konto „LHV IK“ lisatud.");

  const form = page.locator("#lisa-vorm");
  await expect(form).toHaveAttribute("data-kind", "buy");
  for (const f of ["instrument_id", "qty", "price", "fee"]) await expect(form.locator(`[data-field=${f}]`)).toBeVisible();
  for (const f of ["amount", "ratio", "tax_withheld"]) await expect(form.locator(`[data-field=${f}]`)).toBeHidden();
  await form.getByLabel("Liik").selectOption("dividend");
  await expect(form.locator("[data-field=amount] span").first()).toHaveText("Summa (bruto)");
  for (const f of ["instrument_id", "amount", "tax_withheld"]) await expect(form.locator(`[data-field=${f}]`)).toBeVisible();
  await expect(form.locator("[data-field=qty]")).toBeHidden();

  await form.getByLabel("Liik").selectOption("deposit");
  await form.getByLabel("Kuupäev").fill("02.01.2026");
  await form.locator("[name=amount]").fill("2 000,00");
  await form.getByRole("button", { name: "Lisa tehing" }).click();
  await expect(page.locator("#toast")).toHaveText(/^Sissemakse 2\.0?1\.2026 salvestatud\.$/);
  // The next entry keeps the kind and the day.
  await expect(form.getByLabel("Liik")).toHaveValue("deposit");
  await expect(form.getByLabel("Kuupäev")).toHaveValue("02.01.2026");

  await form.getByLabel("Liik").selectOption("buy");
  await form.locator("[name=instrument_id]").selectOption({ label: "ACME · Acme Corp (USD)" });
  await form.getByLabel("Kuupäev").fill("05.01.2026");
  await form.getByLabel("Kogus").fill("kümme");
  await form.getByLabel("Hind").fill("110");
  await form.getByRole("button", { name: "Lisa tehing" }).click();
  await expect(form.locator("[data-field=qty] .pf-viga")).toHaveText("Kirjuta arv, nt 12,50.");
  await expect(form.getByLabel("Kogus")).toBeFocused();
  await form.getByLabel("Kogus").fill("10");
  await form.getByRole("button", { name: "Lisa tehing" }).click();
  await expect(page.locator("#toast")).toHaveText(/^Ost 5\.0?1\.2026 salvestatud\.$/);
  await expect(page.locator("#tx-read tr")).toHaveCount(2);
  await expect(page.locator("#tx-read tr").first()).toContainText("10 × 110,00 USD");
  expect(problems).toEqual([]);
});

test("selling more than held is refused; edit in place; delete", async ({ page }) => {
  await page.goto("/aktsiad/tehingud");
  await waitReady(page);
  const form = page.locator("#lisa-vorm");
  await form.getByLabel("Liik").selectOption("sell");
  await form.locator("[name=instrument_id]").selectOption({ label: "ACME · Acme Corp (USD)" });
  await form.getByLabel("Kuupäev").fill("01.02.2026");
  await form.getByLabel("Kogus").fill("11");
  await form.getByLabel("Hind").fill("120");
  await form.getByRole("button", { name: "Lisa tehing" }).click();
  await expect(form.locator(".pf-vormiviga")).toHaveText("Nii ei klapiks konto ajalugu. Müüd 11 ACME, aga 01.02.2026 seisuga on sul ainult 10.");
  await expect(page.locator("#tx-read tr")).toHaveCount(2);

  const buy = page.locator("#tx-read tr[data-kind=buy]");
  await buy.getByRole("button", { name: /^Muuda/ }).click();
  const editor = page.locator("#muuda-" + (await page.locator("#tx-read tr.pf-muudan").getAttribute("id")).slice(3));
  await expect(editor.getByLabel("Kogus")).toHaveValue("10");
  await editor.getByLabel("Kogus").fill("12");
  await editor.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("Tehing salvestatud.");
  await expect(page.locator("#tx-read tr[data-kind=buy]")).toContainText("12 × 110,00 USD");

  // Escape leaves the row as it was.
  await page.locator("#tx-read tr[data-kind=buy]").getByRole("button", { name: /^Muuda/ }).click();
  await page.keyboard.press("Escape");
  await expect(page.locator("#tx-read tr.pf-muudan")).toHaveCount(0);

  page.once("dialog", (d) => d.accept());
  await page.locator("#tx-read tr[data-kind=deposit]").getByRole("button", { name: /^Kustuta/ }).click();
  await expect(page.locator("#toast")).toHaveText("Tehing kustutatud.");
  await expect(page.locator("#tx-read tr")).toHaveCount(1);
});

test("#lisa focuses the add form", async ({ page }) => {
  await page.goto("/aktsiad/tehingud#lisa");
  await waitReady(page);
  await expect(page.locator("#lisa-vorm select[name=kind]")).toBeFocused();
});

test("CSV import: preview with row errors and duplicates, remap, confirm", async ({ page }) => {
  await page.goto("/aktsiad/tehingud");
  await waitReady(page);
  const csv = [
    "Kuupäev;Tehing;Sümbol;Kogus;Hind;Summa;Valuuta;Viide;Konto",
    "03.03.2026;Sissemakse;;;;500,00;EUR;r1;EE12",
    "04.03.2026;Ost;VWCE;2;100,50;;EUR;r2;EE12",
    "05.03.2026;Ost;UNKNOWN;1;1,00;;USD;r3;EE12",
    "06.03.2026;Sissemakse;;;;10,00;EUR;r1;EE12",
  ].join("\n");
  await page.locator("#import-fail").setInputFiles({ name: "eksport.csv", mimeType: "text/csv", buffer: Buffer.from(csv, "utf-8") });
  await page.getByRole("button", { name: "Eelvaata", exact: true }).click();
  await expect(page.locator("#import-valmis")).toHaveText("2 / 4 rida on valmis importima.");
  await expect(page.locator("#import-read tr[data-line='4'] td").last()).toContainText("tundmatu väärtpaber „UNKNOWN“");
  await expect(page.locator("#import-read tr[data-line='5'] td").last()).toContainText("kordub failis");
  await expect(page.locator("#map-date")).toHaveValue("Kuupäev");
  await expect(page.locator(".pf-eelvaade")).toContainText("Kasutamata veerud: Konto.");

  // Unmapping the reference makes the rows' own hashes the reference: no duplicate any more.
  await page.locator("#map-ref").selectOption("");
  await page.getByRole("button", { name: "Eelvaata uuesti" }).click();
  await expect(page.locator("#import-valmis")).toHaveText("3 / 4 rida on valmis importima.");
  await page.getByRole("button", { name: "Impordi 3 tehingut" }).click();
  await expect(page.locator(".pf-korras")).toHaveText("Imporditud 3 tehingut kontole LHV IK. 1 rida jäi vahele (vead või juba imporditud).");
  await expect(page.locator("#tx-read tr")).toHaveCount(4);
  await page.screenshot({ path: "tests/visual/results/stocks-portfolio-tx.png", fullPage: true });
});
