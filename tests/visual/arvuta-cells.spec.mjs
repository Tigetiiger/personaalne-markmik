// Python cells in a note (docs/ARVUTUS-PLAN.md) with the sandbox's kernel running as a local
// process (/_test/arvuta/kohalik): automatic runs, the table and inline values, hiding the code,
// Mouser rows, the guest-link rule and making a note from a template.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });
test.skip(({ isMobile }) => isMobile, "toolbars are for the desktop editor; the phone shows outputs");

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const LINK = "https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=abc";
const PART = { nr: "595-NE555P", tootja_nr: "NE555P", tootja: "Texas Instruments", kirjeldus: "Timer", breaks: [[1, 0.44], [10, 0.342]], laos: 20, min: 1, kordne: 1 };

function body(title, extra = "") {
  return `# ${title}

## Komponendid

| Link | Kogus | Ühikuhind (€) | Summa (€) | Hoiatus |
| --- | --- | --- | --- | --- |
| ${LINK} | 25 |  |  |  |
| https://lcsc.com/x | 2 | 1,00 € |  |  |

Kokku: \`= kokku\`

\`\`\`python
print("tavaline kood ei jookse")
\`\`\`

\`\`\`arvuta
t = tabel("Komponendid")
m = mouser(t["Link"], t["Kogus"])
t["Ühikuhind (€)"] = m["hind"].fillna(t["Ühikuhind (€)"])
t["Hoiatus"] = m["hoiatus"]
t["Summa (€)"] = t["Kogus"] * t["Ühikuhind (€)"]
kokku = round(t["Summa (€)"].sum(), 2)
kokku
\`\`\`
${extra}`;
}

async function note(page, title, markdown, folder = "") {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, "/tana");
  expect((await api(page, "/_test/arvuta/kohalik", { method: "POST", body: { mouser: { [LINK]: PART } } })).status).toBe(204);
  const made = await api(page, "/api/notes", { method: "POST", body: { title, body: markdown, folder } });
  expect(made.status).toBe(201);
  return made.data;
}

const saved = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);

test.afterEach(async ({ page }) => expect(page.errors || []).toEqual([]));

test("a note's cell runs by itself: table, output and inline value fill in", async ({ page }) => {
  const n = await note(page, unique("Tellimus"), body("Tellimus"));
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator(".mu-code-block.arvuta-rakk")).toHaveCount(1);
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("10,55");
  await expect.poll(() => saved(page, n.slug), { timeout: 15_000 }).toContain("`= kokku ⇒ 10,55`");
  const md = await saved(page, n.slug);
  expect(md).toMatch(/\| 595-NE555P\b|NE555P\?qs=abc\s*\| 25\s*\| 0,342 €\s*\| 8,55 €\s*\| laos ainult 20 \|/);
  expect(md).toContain("```python\nprint(\"tavaline kood ei jookse\")\n```\n\n```arvuta");
  await expect(page.locator(".arvuta-riba .arvuta-olek")).toContainText("✓");
});

test("hiding the code keeps only the output, and showing brings it back", async ({ page }) => {
  const n = await note(page, unique("Peidus"), body("Peidus"));
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("10,55");
  await page.getByRole("button", { name: "Näita ainult väljundit" }).click();
  await expect(page.locator(".mu-code-block.arvuta-peidus")).toHaveCount(1);
  await expect(page.locator(".mu-code-block.arvuta-peidus .mu-codeblock-content")).toBeHidden();
  await expect.poll(() => saved(page, n.slug), { timeout: 15_000 }).toContain("```arvuta-peida\n");
  await page.getByRole("button", { name: "Näita koodi" }).click();
  await expect(page.locator(".mu-code-block.arvuta-peidus")).toHaveCount(0);
});

test("an error shows under its cell and in the toolbar", async ({ page }) => {
  const n = await note(page, unique("Viga"), "# Viga\n\n```arvuta\nx = 1 / 0\n```\n");
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("ZeroDivisionError (rida 1)");
  await expect(page.locator(".arvuta-olek")).toHaveText("✗ viga");
});

test("a note with an edit link runs only on ▶, after a warning", async ({ page }) => {
  const n = await note(page, unique("Jagatud"), body("Jagatud"));
  expect((await api(page, "/api/shares", { method: "POST", body: { slug: n.slug, mode: "edit" } })).status).toBeLessThan(300);
  await page.goto(`/m/${n.slug}`);
  await expect(page.locator(".arvuta-kulalised")).toBeVisible();
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toHaveCount(0);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Käivita kõik plokid" }).click();
  await expect(page.locator(".mu-code-block.arvuta-valjund")).toContainText("10,55");
});

test("a template makes a note with today's date in the template's folder", async ({ page }) => {
  const title = unique("Mouser tellimus");
  const t = await note(page, title, `# ${title}\n\nKuupäev: {{kuupäev}}\n`, "Mallid");
  await page.goto(`/m/${t.slug}`);
  await page.getByRole("button", { name: "Tee märge sellest mallist" }).click();
  const dialog = page.locator("dialog.mall-dialog");
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  await expect(dialog.locator("input[name=title]")).toHaveValue(`${title} ${today}`);
  await dialog.locator("input[name=folder]").fill("tellimused");
  await dialog.getByRole("button", { name: "Tee märge" }).click();
  // The template's own address matches /mouser-tellimus/ too: wait until it is another note's.
  await page.waitForURL((url) => url.pathname !== `/m/${t.slug}`);
  await waitReady(page);
  const slug = new URL(page.url()).pathname.slice(3);
  const made = (await api(page, `/api/notes/${slug}`)).data;
  expect(made.folder).toBe("tellimused");
  expect(made.body).toContain(`Kuupäev: ${today}`);
  // A note outside Mallid has no such button.
  await expect(page.getByRole("button", { name: "Tee märge sellest mallist" })).toHaveCount(0);
});
