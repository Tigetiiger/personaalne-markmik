// Võistluste otsija: the Postkast button and the palette start a search (fake teaduskool CSV, fake
// pages, fake model through /_test/voistlused/fake); one card per competition with its origin,
// link and registration deadline; Seaded → Võistlused.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const STATE = "tests/visual/results/voistlused-owner.json";
test.use({ storageState: STATE });
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: { cookies: [], origins: [] } });
  await login(page, "/postkast");
  await page.context().storageState({ path: STATE });
  await page.close();
});
test.afterAll(async ({ browser }) => {
  const page = await browser.newPage({ storageState: STATE });
  await page.goto("/postkast");
  await waitReady(page);
  await api(page, "/_test/voistlused/fake", { method: "DELETE" });
  await api(page, "/api/voistlused/settings", { method: "PUT", body: { huvid: ["reaalained", "loodusained", "robootika"], markus: "", iganadal: true, klass: 12 } });
  await page.close();
});

/** A day `offset` days from now: {iso: "2026-11-27", et: "27.11.2026"}. */
function day(offset) {
  const d = new Date(Date.now() + offset * 86_400_000);
  return { iso: d.toISOString().slice(0, 10), et: `${d.getUTCDate()}.${d.getUTCMonth() + 1}.${d.getUTCFullYear()}` };
}

/** A teaduskool row and a web page with its registration deadline, both about `name`. */
function world(name, { delay = 1 } = {}) {
  const tk = day(50);
  const start = day(60);
  const end = day(62);
  const reg = day(40);
  const url = `https://${name.toLowerCase()}.example/2026`;
  const quote = `${name} toimub ${start.et} Tallinnas.`;
  return {
    url,
    body: {
      csv: `Võistlused,,,\ntitle,start,end,url\nKeemia lahtine võistlus ${name},${tk.iso},,https://teaduskool.ut.ee/et/olumpiaadid/lahtised/keemia\n`,
      candidates: [{ title: name, url, why: "Robootikavõistlus koolinoortele." }],
      pages: {
        [url]: {
          text: `${quote}\nRegistreerimine kuni ${reg.et}.`,
          events: [{ title: name, date_text: start.et, start_date: start.iso, end_date: end.iso, registration_text: reg.et, registration_deadline: reg.iso, location: "Tallinn", source_quote: quote, confidence: "high" }],
        },
      },
      delay,
    },
  };
}

async function fake(page, body) {
  expect((await api(page, "/_test/voistlused/fake", { method: "POST", body })).status).toBe(204);
  expect((await api(page, "/_test/inbox/seed", { method: "POST", body: [] })).status).toBe(201); // an empty Postkast
}

function watchErrors(page) {
  const problems = [];
  page.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
}

test("Otsi võistlusi: one card per competition; Kinnita adds the deadline too; Loobu for good", async ({ page }) => {
  const problems = watchErrors(page);
  await page.goto("/postkast");
  await waitReady(page);
  const w = world("Robotex");
  await fake(page, w.body);
  await page.reload();
  await waitReady(page);

  const button = page.getByRole("button", { name: "Otsi võistlusi" });
  await button.click();
  await expect(page.locator("#pk-otsi")).toHaveText("Otsin…");
  await expect(page.locator("#pk-otsi")).toBeDisabled();
  const cards = page.locator(".pk-kaart");
  await expect(cards).toHaveCount(2, { timeout: 10_000 });
  await expect(page.locator("#toast")).toHaveText("2 uut võistlust (teaduskoolist 1, veebist 1).");
  await expect(button).toBeEnabled();
  await expect(page.locator("#postkast-arv")).toHaveText("2"); // cards, not rows: the deadline shares its card

  const web = cards.filter({ hasText: "Robotex" }).filter({ hasNotText: "Keemia" });
  await expect(web.locator(".pk-meta")).toContainText("Tallinn · allikas: Veeb · kindlus: kõrge");
  await expect(web.locator(".pk-meta")).toContainText(" – "); // three days
  await expect(web.locator(".pk-registreeri")).toContainText("Registreeri hiljemalt:");
  await expect(web.locator(".pk-link a")).toHaveText("Võistluse leht (robotex.example)");
  await expect(web.locator(".pk-link a")).toHaveAttribute("href", w.url);
  await expect(web.locator(".pk-link a")).toHaveAttribute("rel", "noopener noreferrer");
  await expect(web.locator("blockquote")).toContainText("Robotex toimub");
  await expect(web.locator(".pk-markus")).toContainText("Robootikavõistlus koolinoortele.");

  const tk = cards.filter({ hasText: "Keemia lahtine võistlus" });
  await expect(tk.locator(".pk-meta")).toContainText("allikas: Teaduskool");
  await expect(tk.locator(".pk-link a")).toHaveText("Võistluse leht (teaduskool.ut.ee)");
  await expect(tk.locator(".pk-registreeri")).toHaveCount(0);

  await web.getByRole("button", { name: "Kinnita", exact: true }).click();
  await expect(page.locator("#toast")).toHaveText("Lisatud kalendrisse koos registreerimise tähtajaga.");
  await expect(cards).toHaveCount(1);
  const r = await api(page, `/api/events?start=${day(0).iso}&end=${day(100).iso}`);
  const mine = r.data.filter((e) => e.title.includes("Robotex") && !e.title.includes("Keemia")).map((e) => [e.title, e.status, e.kind]);
  expect(mine.sort()).toEqual([["Registreeri: Robotex", "kinnitatud", "tähtaeg"], ["Robotex", "kinnitatud", "sündmus"]]);

  await tk.getByRole("button", { name: "Loobu" }).click();
  await expect(page.locator("#toast")).toHaveText("Loobutud. Seda võistlust enam ei pakuta.");
  await expect(page.locator(".pk-tyhi")).toBeVisible();

  // The same world again: nothing new, nothing back.
  await button.click();
  await expect(page.locator("#toast")).toHaveText("Uusi võistlusi ei leitud.", { timeout: 10_000 });
  await expect(cards).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("the palette starts a search from another page; Postkast waits for it", async ({ page }) => {
  await page.goto("/tana");
  await waitReady(page);
  await fake(page, world("Hackathon", { delay: 2 }).body);
  await page.keyboard.press("Control+k");
  await page.keyboard.type("otsi võistlusi");
  await page.locator("dialog.palette .palette-item").filter({ hasText: "Otsi võistlusi" }).click();
  await expect(page).toHaveURL("/postkast");
  await waitReady(page);
  await expect(page.locator("#pk-otsi")).toHaveText("Otsin…");
  await expect(page.locator(".pk-kaart")).toHaveCount(2, { timeout: 10_000 });
  await expect(page.locator("#pk-otsi")).toHaveText("Otsi võistlusi");
});

test("Seaded → Võistlused: interests, free text, weekly switch, the last run", async ({ page }) => {
  const problems = watchErrors(page);
  await page.goto("/seaded");
  await waitReady(page);
  await fake(page, world("Robomats", { delay: 0 }).body);
  expect((await api(page, "/api/voistlused/run", { method: "POST" })).status).toBe(202);
  await expect.poll(async () => (await api(page, "/api/voistlused/status")).data.running).toBe(false);
  await page.reload();
  await waitReady(page);

  const section = page.locator("#seaded-voistlused");
  await expect(section.locator("h2")).toHaveText("Võistlused");
  await expect(section.locator(".vo-viimati")).toContainText("Viimati otsitud");
  await expect(section.locator(".vo-viimati")).toContainText("2 uut võistlust (teaduskoolist 1, veebist 1).");
  await expect(section.getByLabel(/Reaalained/)).toBeChecked();
  await expect(section.getByLabel(/Loodusained/)).toBeChecked();
  await expect(section.getByLabel(/Robootika ja tehnoloogia/)).toBeChecked();
  await expect(section.getByLabel(/Humanitaarained/)).not.toBeChecked();
  await expect(section.getByLabel(/Otsi kord nädalas/)).toBeChecked();

  await section.getByLabel(/Humanitaarained/).check();
  await section.getByLabel(/Loodusained/).uncheck();
  await section.getByLabel("Lisasoovid").fill("9. klass, Tallinna Reaalkool");
  await section.getByLabel(/Otsi kord nädalas/).uncheck();
  const saved = page.waitForRequest((r) => r.url().endsWith("/api/voistlused/settings") && r.method() === "PUT");
  await section.getByRole("button", { name: "Salvesta" }).click();
  expect((await saved).postDataJSON()).toEqual({ huvid: ["reaalained", "robootika", "humanitaar"], markus: "9. klass, Tallinna Reaalkool", iganadal: false, klass: 12 });
  await expect(page.locator("#toast")).toHaveText("Võistluste otsingu seaded salvestatud.");

  await page.reload();
  await waitReady(page);
  await expect(section.getByLabel(/Loodusained/)).not.toBeChecked();
  await expect(section.getByLabel(/Humanitaarained/)).toBeChecked();
  await expect(section.getByLabel("Lisasoovid")).toHaveValue("9. klass, Tallinna Reaalkool");
  await expect(section.getByLabel(/Otsi kord nädalas/)).not.toBeChecked();

  // Nothing to look for: the button says so.
  for (const box of await section.locator("[name=huvid]").all()) await box.uncheck();
  await section.getByLabel("Lisasoovid").fill("");
  await section.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("Võistluste otsingu seaded salvestatud.");
  await page.goto("/postkast");
  await waitReady(page);
  await page.getByRole("button", { name: "Otsi võistlusi" }).click();
  await expect(page.locator("#toast")).toHaveText("Vali Seadetes vähemalt üks huviala.");
  expect(problems).toEqual([]);
});

// --- Part 2: can I take part? -------------------------------------------------------------------

/** Two days of one school year from now on, and the grade to set so that it is 12th on them. */
function season() {
  const now = new Date();
  const end = new Date(Date.UTC(now.getUTCMonth() >= 8 ? now.getUTCFullYear() + 1 : now.getUTCFullYear(), 7, 25));
  const room = (end - now) / 86_400_000;
  const at = (d) => ({ iso: d.toISOString().slice(0, 10), et: `${d.getUTCDate()}.${d.getUTCMonth() + 1}.${d.getUTCFullYear()}` });
  if (room > 40) return { klass: 12, first: day(10), second: day(30) };
  const year = end.getUTCFullYear();
  return { klass: 11, first: at(new Date(Date.UTC(year, 8, 10))), second: at(new Date(Date.UTC(year, 8, 30))) };
}

function olympiad(s) {
  const page = "https://teaduskool.ut.ee/et/olumpiaadid/eesti/keemiaolumpiaad";
  const quote = "Kooli- ja piirkonnavoorus võistlevad 8.–12. klasside ja lõppvoorus 9.–12. klasside õpilased.";
  return {
    csv:
      "Olümpiaadid,,,\ntitle,start,end,url\n" +
      `Keemiaolümpiaadi piirkonnavoor 8.-12. klassile (TÜ),${s.first.iso},,${page}\n` +
      `Keemiaolümpiaadi lõppvoor (TÜ),${s.second.iso},,${page}\n` +
      `Loodusteaduste olümpiaadi piirkonnavoor 7.-8. klassile (TÜ),${s.first.iso},,\n`,
    pages: {
      [page]: {
        text: `Keemiaolümpiaad\n${quote}\nPiirkonnavoor ${s.first.et}`,
        facts: { grade_min: 8, grade_max: 12, registration_by: "school", registration_how: "Õpetaja registreerib koolivooru kaudu.", team: "individual", fee: "tasuta", where: "Tartu", facts_quote: quote },
      },
    },
    delay: 0,
  };
}

test("Ei sobi apart with its reason; a final names its round and confirms it too", async ({ page }) => {
  const problems = watchErrors(page);
  await page.goto("/postkast");
  await waitReady(page);
  const s = season();
  await fake(page, olympiad(s));
  await api(page, "/api/voistlused/settings", { method: "PUT", body: { huvid: ["reaalained", "loodusained", "robootika"], markus: "", iganadal: true, klass: s.klass } });
  await page.reload();
  await waitReady(page);
  await page.getByRole("button", { name: "Otsi võistlusi" }).click();

  const main = page.locator("#pk-list .pk-kaart");
  await expect(main).toHaveCount(2, { timeout: 10_000 });
  await expect(page.locator("#postkast-arv")).toHaveText("2");
  await expect(page.locator("#pk-arv")).toHaveText("2 ootavad kinnitamist");

  const final = main.filter({ hasText: "Keemiaolümpiaadi lõppvoor" });
  await expect(final.locator(".pk-sobivus")).toContainText("Pääseb edasi: Keemiaolümpiaadi piirkonnavoor 8.-12. klassile (TÜ),");
  await expect(final.locator(".pk-tingimused")).toContainText("Kes: 8.–12. klass");
  await expect(final.locator(".pk-tingimused")).toContainText("Registreerib: kool");
  await expect(final.locator(".pk-tingimused")).toContainText("Registreerimine: Õpetaja registreerib koolivooru kaudu.");
  await expect(final.locator(".pk-tingimused")).toContainText("Osalemine: üksi");
  await expect(final.locator(".pk-tingimused")).toContainText("Osavõtutasu: tasuta");

  const box = page.locator("#pk-ei-sobi");
  await expect(box.locator("summary")).toHaveText("Ei sobi (1)");
  const low = box.locator(".pk-kaart");
  await expect(low).toBeHidden(); // folded
  await box.locator("summary").click();
  await expect(low.locator(".pk-sobivus")).toHaveText("Ei sobi: ainult 7.–8. klassile");
  await expect(low.locator(".pk-sobivus")).toBeVisible();

  await final.getByRole("button", { name: "Kinnita", exact: true }).click();
  await expect(page.locator("#toast")).toHaveText("Lisatud kalendrisse koos eelneva vooruga.");
  await expect(main).toHaveCount(0); // its qualifying round went along
  await expect(page.locator("#postkast-arv")).toBeHidden();

  // What does not fit can still be confirmed, one by one.
  await low.getByRole("button", { name: "Kinnita", exact: true }).click();
  await expect(page.locator("#toast")).toHaveText("Lisatud kalendrisse.");
  await expect(box).toBeHidden();
  const r = await api(page, `/api/events?start=${day(0).iso}&end=${day(380).iso}`);
  const titles = r.data.filter((e) => e.status === "kinnitatud").map((e) => e.title);
  expect(titles).toEqual(expect.arrayContaining(["Keemiaolümpiaadi piirkonnavoor 8.-12. klassile (TÜ)", "Keemiaolümpiaadi lõppvoor (TÜ)", "Loodusteaduste olümpiaadi piirkonnavoor 7.-8. klassile (TÜ)"]));
  expect(problems).toEqual([]);
});

test("Seaded → Võistlused: the grade", async ({ page }) => {
  await page.goto("/seaded");
  await waitReady(page);
  await api(page, "/api/voistlused/settings", { method: "PUT", body: { huvid: ["reaalained"], markus: "", iganadal: true, klass: 12 } });
  await page.reload();
  await waitReady(page);
  const section = page.locator("#seaded-voistlused");
  const grade = section.getByLabel("Klass");
  await expect(grade).toHaveValue("12");
  await expect(grade.locator("option")).toHaveCount(13);
  await grade.selectOption({ label: "8. klass" });
  const saved = page.waitForRequest((r) => r.url().endsWith("/api/voistlused/settings") && r.method() === "PUT");
  await section.getByRole("button", { name: "Salvesta" }).click();
  expect((await saved).postDataJSON()).toMatchObject({ klass: 8 });
  await page.reload();
  await waitReady(page);
  await expect(section.getByLabel("Klass")).toHaveValue("8");
  await grade.selectOption({ label: "Kool on lõpetatud" });
  await section.getByRole("button", { name: "Salvesta" }).click();
  await expect(page.locator("#toast")).toHaveText("Võistluste otsingu seaded salvestatud.");
  expect((await api(page, "/api/voistlused/settings")).data.klass).toBe(13);
});
