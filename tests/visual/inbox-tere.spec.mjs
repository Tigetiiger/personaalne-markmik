// First-run wizard: the first login lands on /tere; the steps are walked with skips.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

test("first login lands on the wizard, which walks every card", async ({ page, context }) => {
  await login(page, "/seaded");
  expect((await api(page, "/_test/inbox/fakes", { method: "POST" })).status).toBe(204); // no onboarding answers
  // In test mode only this cookie turns the first-run redirect on (other packages start at /).
  await context.clearCookies();
  await context.addCookies([{ name: "vt_wizard", value: "1", url: page.url() }]);
  await login(page, "/");
  await expect(page).toHaveURL(/\/tere$/);

  const steps = page.locator("#tere-sammud li");
  const current = page.locator("#tere-samm .uh-kaart");
  const next = page.getByRole("button", { name: "Jätka" });
  const skip = page.getByRole("button", { name: "Jäta vahele" });
  await expect(steps).toHaveText(["Claude", "eKool", "Google'i kontod", "Brain repo", "ntfy teavitused", "Kalendrivood (ICS)", "Telefoni kalender"]);
  await expect(page.locator(".tere-loendur")).toHaveText("Samm 1/7");
  await expect(current.locator("h2")).toHaveText("Claude");
  await skip.click();

  await expect(page.locator(".tere-loendur")).toHaveText("Samm 2/7");
  await expect(steps.nth(1)).toHaveAttribute("aria-current", "step");
  await current.getByLabel("Kasutajanimi").fill("mari.maasikas");
  await current.getByLabel("Parool").fill("salasona-123");
  await current.getByRole("button", { name: "Salvesta" }).click();
  await expect(current.locator(".uh-olek")).toHaveText("Seadistatud, pole veel testitud");
  await next.click();

  await expect(current.locator("h2")).toHaveText("Google'i kontod");
  await skip.click();
  await expect(current.locator("h2")).toHaveText("Brain repo");
  await skip.click();

  await expect(current.locator("h2")).toHaveText("ntfy teavitused");
  await current.getByLabel("Teema").fill("markmik-tere-5678");
  await current.getByRole("button", { name: "Salvesta" }).click();
  await current.getByRole("button", { name: "Testi" }).click();
  await expect(current.locator(".uh-olek")).toContainText("Ühendatud · Proovisõnum saadetud (võlts)");
  await next.click();

  await expect(current.locator("h2")).toHaveText("Kalendrivood (ICS)");
  await skip.click();
  await expect(current.locator(".uh-aadress")).toHaveValue(/\/ics\/vt-privaatne-token\.ics$/);
  await next.click();

  await expect(page.locator(".tere-valmis")).toContainText("Valmis!");
  await expect(steps.nth(0)).toHaveAttribute("data-status", "skipped");
  await expect(steps.nth(1)).toHaveAttribute("data-status", "done");
  const { data } = await api(page, "/api/onboarding");
  expect(data.remaining).toBe(0);
  expect(data.steps.map((s) => s.status)).toEqual(["skipped", "done", "skipped", "skipped", "done", "skipped", "done"]);

  // Answered: the start page is the start page again.
  await page.goto("/");
  await waitReady(page);
  await expect(page).toHaveURL(/\/$/);
  await api(page, "/_test/inbox/fakes", { method: "DELETE" });
});

test("the wizard still offers every step when the cards cannot be loaded", async ({ page }) => {
  await login(page, "/seaded");
  expect((await api(page, "/_test/inbox/fakes", { method: "POST" })).status).toBe(204);
  // This test is about a failing endpoint, so it is stubbed.
  await page.route("**/api/connections", (r) => r.fulfill({ status: 500, contentType: "application/json", body: '{"error": "Tõrge"}' }));
  await page.goto("/tere");
  await waitReady(page);
  await expect(page.locator("#tere-sammud li")).toHaveCount(7);
  await expect(page.locator(".tere-puudub")).toContainText("„Claude“ kaarti ei õnnestunud laadida");
  await page.getByRole("button", { name: "Jäta vahele" }).click();
  await expect(page.locator(".tere-loendur")).toHaveText("Samm 2/7");
  expect((await api(page, "/api/onboarding")).data.steps[0].status).toBe("skipped");
  await api(page, "/_test/inbox/fakes", { method: "DELETE" });
});
