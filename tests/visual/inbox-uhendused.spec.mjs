// Seaded → Ühendused with the owners' helpers faked on the server (/_test/inbox/fakes).
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const card = (page, key) => page.locator(`.uh-kaart[data-key="${key}"]`);

// One login per file, reused through the saved session: /login allows 20 requests a minute per
// address, and in a full run every package's specs share that budget.
const STATE = "tests/visual/results/inbox-uhendused-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/seaded");
  await page.context().storageState({ path: STATE });
  await page.close();
});

test.beforeEach(async ({ page }) => {
  await page.goto("/seaded/uhendused");
  await waitReady(page);
  expect((await api(page, "/_test/inbox/fakes", { method: "POST" })).status).toBe(204);
  await page.reload();
  await waitReady(page);
});

test.afterEach(async ({ page }) => {
  await api(page, "/_test/inbox/fakes", { method: "DELETE" });
});

test("every card renders with its instructions and no page errors", async ({ page }) => {
  const problems = [];
  page.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  await page.reload();
  await waitReady(page);
  await expect(page.locator(".uh-kaart h2")).toHaveText(["Claude", "eKool", "Google'i kontod", "Brain repo", "ntfy teavitused", "Kalendrivood (ICS)", "Telefoni kalender"]);
  for (const key of ["ekool", "google", "brain", "ntfy", "ics"]) {
    await expect(card(page, key).locator(".uh-olek")).toHaveText("Pole ühendatud");
  }
  await expect(card(page, "brain")).toContainText("Fine-grained tokens");
  await expect(card(page, "brain").getByLabel("Repo aadress")).toHaveValue("https://github.com/Tigetiiger/brain.git");
  await expect(card(page, "phone").locator(".uh-aadress")).toHaveValue(/^http:\/\/127\.0\.0\.1:\d+\/ics\/vt-privaatne-token\.ics$/);
  await card(page, "phone").getByRole("button", { name: "Kopeeri" }).click();
  await expect(page.locator("#toast")).toHaveText("Aadress kopeeritud.");
  expect(problems).toEqual([]);
});

test("saving the ntfy card and pressing Testi shows the result; secrets stay masked", async ({ page }) => {
  const ntfy = card(page, "ntfy");
  await expect(ntfy.getByLabel("Serveri aadress")).toHaveValue("http://100.122.97.7:8090");
  await ntfy.getByLabel("Teema").fill("markmik proov");
  await ntfy.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toContainText("Teema nimes võivad olla ainult");

  await ntfy.getByLabel("Teema").fill("markmik-proov-1234");
  await ntfy.getByLabel("Token").fill("tk_salajanetoken9876");
  await ntfy.getByRole("button", { name: "Salvesta" }).click();
  await expect(ntfy.locator(".uh-olek")).toHaveText("Seadistatud, pole veel testitud");
  await expect(ntfy.getByLabel("Token")).toHaveValue("");
  await expect(ntfy).toContainText("Salvestatud: ••••9876");
  await expect(ntfy).toContainText("Salvestatud: ••••1234");
  expect(await page.content()).not.toContain("tk_salajanetoken9876");

  await ntfy.getByRole("button", { name: "Testi" }).click();
  await expect(ntfy.locator(".uh-olek")).toHaveAttribute("data-state", "ok");
  await expect(ntfy.locator(".uh-olek")).toContainText("Ühendatud · Proovisõnum saadetud (võlts)");

  await page.reload();
  await waitReady(page);
  await expect(card(page, "ntfy").locator(".uh-olek")).toContainText("Ühendatud · Proovisõnum saadetud (võlts)");
});

test("eKool: Värskenda and source health; a failing test shows Viga; Katkesta removes the secrets", async ({ page }) => {
  const ekool = card(page, "ekool");
  await ekool.getByLabel("Kasutajanimi").fill("mari.maasikas");
  await ekool.getByLabel("Parool").fill("salasona-123");
  await ekool.getByRole("button", { name: "Salvesta" }).click();
  await expect(ekool.locator(".uh-olek")).toHaveText("Seadistatud, pole veel testitud");
  await expect(ekool.locator(".uh-allikad li")).toContainText("eKool");
  await expect(ekool.locator(".uh-allikad li")).toContainText("pole veel andmeid toonud");
  await ekool.getByRole("button", { name: "Värskenda" }).click();
  await expect(page.locator("#toast")).toHaveText("Värskendatud (2 kirjet).");
  await expect(ekool.locator(".uh-allikad li")).toContainText("viimati korras");
  await expect(ekool.getByLabel("Parool")).toHaveAttribute("autocomplete", "new-password");

  await ekool.getByRole("button", { name: "Testi" }).click();
  await expect(ekool.locator(".uh-olek")).toHaveAttribute("data-state", "viga");
  await expect(ekool.locator(".uh-olek")).toContainText("Viga: Vale kasutajanimi või parool (võlts)");

  page.once("dialog", (d) => d.accept());
  await ekool.getByRole("button", { name: "Katkesta" }).click();
  await expect(ekool.locator(".uh-olek")).toHaveText("Pole ühendatud");
  await expect(ekool.getByRole("button", { name: "Katkesta" })).toHaveCount(0);
});

test("Google: upload the client, add an account through the pasted address, remove it", async ({ page, context }) => {
  const google = card(page, "google");
  const add = google.getByRole("button", { name: "Lisa Google'i konto" });
  await expect(add).toBeDisabled();

  const client = { installed: { client_id: "1.apps.googleusercontent.com", client_secret: "GOCSPX-vt", redirect_uris: ["http://localhost"] } };
  await google.locator("input[type=file]").setInputFiles({ name: "client_secret.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(client)) });
  await google.getByRole("button", { name: "Laadi üles" }).click();
  await expect(google.locator(".uh-korras")).toHaveText("OAuth-klient on üles laaditud.");

  const consent = context.waitForEvent("page");
  await add.click();
  const tab = await consent;
  await expect(tab.locator("p")).toHaveText("Google'i nõusolekuleht (võlts)");
  await tab.close();

  const pasted = google.getByLabel("Aadress, mis algab http://127.0.0.1");
  await pasted.fill("http://127.0.0.1:53682/?state=vt");
  await google.getByRole("button", { name: "Ühenda konto" }).click();
  await expect(page.locator("#toast")).toHaveText("Aadressist ei leitud Google'i koodi.");
  await pasted.fill("http://127.0.0.1:53682/?state=vt&code=4/0Avt&scope=https://www.googleapis.com/auth/gmail.readonly");
  await google.getByRole("button", { name: "Ühenda konto" }).click();
  await expect(page.locator("#toast")).toHaveText("Ühendatud: opilane@example.com");
  await expect(google.locator(".uh-kontod li")).toHaveCount(1);
  await expect(google.locator(".uh-kontod")).toContainText("opilane@example.com");

  await google.getByRole("button", { name: "Testi" }).click();
  await expect(google.locator(".uh-olek")).toContainText("Ühendatud · opilane@example.com · 4 kirja (võlts)");

  page.once("dialog", (d) => d.accept());
  await google.locator(".uh-kontod").getByRole("button", { name: "Eemalda" }).click();
  await expect(google.locator(".uh-kontod")).toHaveCount(0);

  const senders = google.getByLabel("Saatjad, kelle kirju lugeda");
  await senders.fill("gag.ee\nOpetaja@Kool.example");
  await google.getByRole("button", { name: "Salvesta filter" }).click();
  await expect(page.locator("#toast")).toHaveText("Filter salvestatud.");
  await expect(senders).toHaveValue("gag.ee\nopetaja@kool.example");
});

test("ICS feeds are added and removed; only the host is shown", async ({ page }) => {
  const ics = card(page, "ics");
  await ics.getByLabel("Kalendri aadress").fill("webcal://kool.example/kalender/private-xyz.ics");
  await ics.getByLabel("Nimi").fill("Kooli üritused");
  await ics.getByRole("button", { name: "Lisa", exact: true }).click();
  await expect(ics.locator(".uh-vood li")).toHaveCount(1);
  await expect(ics.locator(".uh-vood li .uh-nimi")).toHaveText("Kooli üritused (kool.example)");
  await expect(ics.locator(".uh-vood li")).toContainText("pole veel andmeid toonud");
  expect(await page.content()).not.toContain("private-xyz");

  page.once("dialog", (d) => d.accept());
  await ics.getByRole("button", { name: "Eemalda" }).click();
  await expect(ics.locator(".uh-vood")).toHaveCount(0);
});

test("Uus aadress on the phone card: after a confirmation the old address returns 404", async ({ page }) => {
  // The real feed this time: without the fakes the card shows the app's own private address.
  expect((await api(page, "/_test/inbox/fakes", { method: "DELETE" })).status).toBe(204);
  await page.reload();
  await waitReady(page);
  const phone = card(page, "phone");
  const address = phone.locator(".uh-aadress");
  await expect(address).toHaveValue(/\/ics\/[\w-]+\.ics$/);
  const old = await address.inputValue();
  expect((await page.request.get(old)).status()).toBe(200);

  const asked = [];
  page.once("dialog", (d) => {
    asked.push(d.message());
    return d.dismiss();
  });
  await phone.getByRole("button", { name: "Uus aadress" }).click();
  await expect.poll(() => asked).toEqual(["Teha uus aadress? Vana aadress lakkab töötamast."]);
  await expect(address).toHaveValue(old);
  expect((await page.request.get(old)).status()).toBe(200);

  page.once("dialog", (d) => d.accept());
  await phone.getByRole("button", { name: "Uus aadress" }).click();
  await expect(page.locator("#toast")).toHaveText("Uus aadress tehtud. Lisa see telefoni kalendrisse.");
  await expect(address).not.toHaveValue(old);
  const now = await address.inputValue();
  expect(now).toMatch(/\/ics\/[\w-]+\.ics$/);
  expect((await page.request.get(old)).status()).toBe(404);
  const feed = await page.request.get(now);
  expect(feed.status()).toBe(200);
  expect(await feed.text()).toContain("BEGIN:VCALENDAR");
});

test("Seaded links to Ühendused and checks the password form", async ({ page }) => {
  await page.goto("/seaded");
  await waitReady(page);
  await expect(page.locator("a.uh-viide")).toHaveAttribute("href", "/seaded/uhendused");
  await page.getByLabel("Praegune parool").fill("vale-parool");
  await page.getByLabel("Uus parool", { exact: true }).fill("uus-parool-1");
  await page.getByLabel("Uus parool uuesti").fill("uus-parool-2");
  await page.getByRole("button", { name: "Muuda parool" }).click();
  await expect(page.locator("#toast")).toHaveText("Uued paroolid ei klapi.");
  // A wrong current password is refused by the server; the shared test password stays as it is.
  await page.getByLabel("Uus parool uuesti").fill("uus-parool-1");
  await page.getByRole("button", { name: "Muuda parool" }).click();
  await expect(page.locator("#toast")).toHaveText("Vana parool on vale.");
});
