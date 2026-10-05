// Data files in a note (docs/FAILID-PLAN.md), the sandbox's kernel running locally: the Failid tab
// adds a file by upload, by web address and from another note; a cell reads it; "Värskenda" moves
// the links to new content; a file dropped onto the note is uploaded and linked.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });
test.skip(({ isMobile }) => isMobile, "the panel and drops are for the desktop");

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const saved = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);

async function open(page, body) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, "/tana");
  expect((await api(page, "/_test/arvuta/kohalik", { method: "POST", body: {} })).status).toBe(204);
  const title = unique("Failid");
  const n = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n${body}` } })).data;
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator("#editor .mu-editor")).toBeVisible();
  // The right panel remembers being open: Viited only when it is closed.
  const tab = page.getByRole("tab", { name: "Failid" });
  if (!(await tab.isVisible())) await page.getByRole("button", { name: "Viited" }).click();
  await tab.click();
  return n.slug;
}

test.afterEach(async ({ page }) => expect(page.errors || []).toEqual([]));

test("an uploaded CSV is linked and a cell reads it", async ({ page }) => {
  const slug = await open(page, "```arvuta\nprint(fail('kulud.csv')['kulu'].sum())\n```\n");
  await expect(page.locator(".failid-tyhi")).toContainText("pole veel faile");
  await page.getByRole("button", { name: "Lisa fail" }).click();
  await page.locator(".failid-dialog input[type=file]").setInputFiles({ name: "kulud.csv", mimeType: "text/csv", buffer: Buffer.from("kuu,kulu\njaanuar,4\nveebruar,6\n") });
  await expect(page.locator(".failid-nimekiri a")).toHaveText("kulud.csv");
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("10");
  await expect.poll(() => saved(page, slug)).toMatch(/\[kulud\.csv\]\(\/failid\/[0-9a-f]{64}\.csv\)/);
});

test("a web address is fetched, picked again elsewhere, and Värskenda moves the links", async ({ page }) => {
  await login(page, "/tana");
  await api(page, "/_test/failid/aadress", { method: "POST", body: { content: "a\n1\n" } });
  const slug = await open(page, "```arvuta\nprint(fail('andmed.csv')['a'].sum())\n```\n");
  await page.getByRole("button", { name: "Lisa fail" }).click();
  await page.getByLabel("Faili veebiaadress").fill("https://example.com/andmed.csv");
  await page.getByRole("button", { name: "Lae alla" }).click();
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("1");
  await expect(page.locator(".failid-nimekiri")).toContainText("veebist");
  const before = await saved(page, slug);
  await api(page, "/_test/failid/aadress", { method: "POST", body: { content: "a\n5\n" } });
  await page.locator(".failid-nimekiri").getByRole("button", { name: "Värskenda" }).click();
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("5");
  await expect.poll(() => saved(page, slug)).not.toBe(before);
  // The same file from another note.
  const other = await open(page, "```arvuta\nprint(len(fail('andmed.csv')))\n```\n");
  await page.getByRole("button", { name: "Lisa fail" }).click();
  await page.getByLabel("Otsi olemasolevat faili").fill("andmed");
  await page.locator(".failid-leitud button", { hasText: "andmed.csv" }).first().click();
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("1");
  await expect.poll(() => saved(page, other)).toContain("[andmed.csv](/failid/");
});

test("a data file dropped onto the note is uploaded and linked", async ({ page }) => {
  const slug = await open(page, "Tekst.\n");
  await page.locator("#editor .mu-editor p").last().click();
  await page.evaluate(() => {
    const data = new DataTransfer();
    data.items.add(new File(["x\n1\n"], "lohistatud.csv", { type: "text/csv" }));
    const target = document.querySelector("#editor .mu-editor p:last-of-type");
    target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: data }));
  });
  await expect.poll(() => saved(page, slug)).toMatch(/\[lohistatud\.csv\]\(\/failid\/[0-9a-f]{64}\.csv\)/);
  await expect(page.locator(".failid-nimekiri a")).toHaveText("lohistatud.csv", { timeout: 10_000 });
});

test("a cell's valjund/ file becomes a download link under it", async ({ page }) => {
  const slug = await open(page, "```arvuta\nopen('valjund/tulemus.csv', 'w').write('a\\n1\\n')\n```\n");
  await expect.poll(() => saved(page, slug), { timeout: 15_000 }).toMatch(/📎 \[tulemus\.csv\]\(\/failid\/[0-9a-f]{64}\.csv\)/);
  await expect(page.locator("#editor .mu-editor p", { hasText: "tulemus.csv" })).toHaveCount(1);
});
