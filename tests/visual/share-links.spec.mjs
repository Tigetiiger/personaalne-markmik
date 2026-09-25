// Share links (WP-G): the owner makes a link in the Jaga dialog of the note page (/m/<slug>), a
// separate browser that is not signed in opens it.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });

function watch(page) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  return page;
}

// One login per file: /login is rate-limited.
let cookies = null;
/** The owner on a new note's page (body: its Markdown after the title). */
async function ownerOn(browser, baseURL, title, body = "") {
  const page = watch(await (await browser.newContext({ baseURL })).newPage());
  if (cookies) {
    await page.context().addCookies(cookies);
    await page.goto("/tana");
    await waitReady(page);
  } else {
    await login(page, "/tana");
    cookies = await page.context().cookies();
  }
  const made = await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n${body}` } });
  expect(made.status).toBe(201);
  page.slug = made.data.slug;
  await page.goto(`/m/${page.slug}`);
  await waitReady(page);
  await expect(page.locator("#note")).toHaveAttribute("data-open", page.slug);
  await expect(page.getByRole("button", { name: "Jaga" })).toBeVisible();
  return page;
}

async function guestAt(browser, baseURL, url) {
  const page = watch(await (await browser.newContext({ baseURL })).newPage());
  await page.goto(url);
  await waitReady(page);
  return page;
}

async function newLink(owner, slug, mode) {
  const r = await api(owner, "/api/shares", { method: "POST", body: { slug, mode } });
  expect(r.status).toBe(201);
  return r.data.url;
}

async function close(...pages) {
  for (const page of pages) {
    expect(page.errors, "JavaScript errors on the page").toEqual([]);
    await page.context().close();
  }
}

const text = (page) => page.locator("#share-editor .mu-editor, #editor .mu-editor").innerText();

test("a view link made in the Jaga dialog shows the note and follows the owner's edits; revoked, it is gone", async ({ browser, baseURL }) => {
  const owner = await ownerOn(browser, baseURL, unique("vaade"));
  const slug = owner.slug;
  await owner.getByRole("button", { name: "Jaga" }).click();
  const dialog = owner.getByRole("dialog", { name: "Jaga märget" });
  await expect(dialog.locator(".share-empty")).toHaveText("Linke veel pole.");
  await dialog.getByLabel("Aegub").selectOption({ label: "7 päeva" });
  await dialog.getByRole("button", { name: "Loo vaatamise link" }).click();
  const url = await dialog.locator(".share-url").inputValue();
  expect(url).toMatch(new RegExp(`^${baseURL}/j/[A-Za-z0-9_-]{22}$`));
  const row = dialog.locator(".share-link");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Vaatamiseks");
  await expect(row).toContainText("aegub");
  await expect(row).toContainText("pole kasutatud");
  await dialog.getByRole("button", { name: "Kopeeri" }).click();
  await expect(dialog.locator(".share-status")).toHaveText("Link kopeeritud.");
  await dialog.getByRole("button", { name: "Sulge" }).click();
  await expect(dialog).toHaveCount(0);

  const guest = await guestAt(browser, baseURL, url);
  await expect(guest.locator("body")).toHaveAttribute("data-page", "share-view");
  await expect(guest.locator("#share-doc h1")).toHaveText(slug);
  await expect(guest.locator("#share-bar")).toHaveText("Ainult vaatamiseks");
  await expect(guest.locator("#nav")).toHaveCount(0);

  await owner.locator("#editor .mu-editor h1").click();
  await owner.keyboard.press("End");
  await owner.keyboard.press("Enter");
  await owner.keyboard.type("Omaniku uus lõik");
  await expect(guest.locator("#share-doc p")).toHaveText("Omaniku uus lõik", { timeout: 15_000 });

  await owner.getByRole("button", { name: "Jaga" }).click();
  await expect(row).toContainText("viimati kasutatud");
  owner.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: "Tühista" }).click();
  await expect(dialog.locator(".share-empty")).toHaveText("Linke veel pole.");
  await expect(dialog.locator(".share-status")).toHaveText("Link tühistatud.");
  // The open page notices at its next poll; opened again, the link is a calm 404.
  await expect(guest.locator("#share-bar")).toHaveText("See link ei kehti enam.", { timeout: 10_000 });
  const again = await guest.goto(url);
  expect(again.status()).toBe(404);
  await expect(guest.getByRole("heading", { name: "Linki ei leitud" })).toBeVisible();
  await expect(guest.locator("body")).not.toContainText(slug);
  await close(owner, guest);
});

test("an edit guest gives a name, types with the owner, both converge and the guest's caret is labelled", async ({ browser, baseURL }) => {
  const owner = await ownerOn(browser, baseURL, unique("koos"), "Esimene lõik.\n\nTeine lõik.\n\nVaata [teist](/m/teine).\n");
  const slug = owner.slug;
  const url = await newLink(owner, slug, "edit");

  const guest = await guestAt(browser, baseURL, url);
  await expect(guest.locator("body")).toHaveAttribute("data-page", "share-name");
  await guest.getByLabel("Sinu nimi").fill("Mari");
  await guest.getByRole("button", { name: "Alusta" }).click();
  await waitReady(guest);
  await expect(guest.locator("#share-me")).toHaveText("Mari");
  await expect.poll(() => text(guest)).toBe(await text(owner));

  await owner.locator("#editor .mu-editor p").nth(0).click();
  await owner.keyboard.press("End");
  await guest.locator("#share-editor .mu-editor p").nth(1).click();
  await guest.keyboard.press("End");
  await Promise.all([owner.keyboard.type(" AAA", { delay: 20 }), guest.keyboard.type(" BBB", { delay: 20 })]);
  await expect.poll(async () => (await text(owner)) === (await text(guest)), { timeout: 10_000 }).toBe(true);
  expect(await text(owner)).toMatch(/Esimene lõik\. AAA\s+Teine lõik\. BBB/);

  await guest.keyboard.press("Shift+Home");
  await expect(owner.locator(".mt-remote-caret span")).toHaveText(["Mari (külaline)"]);
  await expect(owner.locator(".meta-people")).toContainText("Mari (külaline)");
  await expect(guest.locator("#share-people")).toContainText("Omanik");
  await expect(guest.locator("#share-save")).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await expect.poll(async () => (await api(owner, `/api/notes/${slug}`)).data.body, { timeout: 10_000 })
    .toBe(`# ${slug}\n\nEsimene lõik. AAA\n\nTeine lõik. BBB\n\nVaata [teist](/m/teine).\n`);

  // A link to one of the owner's other notes does not open for the guest.
  await guest.locator("#share-editor .mu-editor p").nth(2).getByText("teist").click({ modifiers: ["Control"] });
  await expect(guest.locator("#toast")).toHaveText("See link viib märkmesse, mida pole sinuga jagatud.");
  expect(guest.url()).toBe(url);

  // The same browser opens a link to another note: this tab says why it stopped.
  const another = await api(owner, "/api/notes", { method: "POST", body: { title: unique("teine") } });
  const second = watch(await guest.context().newPage());
  await second.goto(await newLink(owner, another.data.slug, "view"));
  await waitReady(second);
  await expect(guest.locator(".share-bar")).toHaveText(/teine jagatud link/);
  await expect(guest.locator("#share-editor .mu-editor")).toHaveCount(0);
  await close(owner, second, guest);
});

test("HTML a guest writes into the note runs nothing in the owner's editor and restyles nothing", async ({ browser, baseURL }) => {
  const owner = await ownerOn(browser, baseURL, unique("xss"));
  const slug = owner.slug;
  const url = await newLink(owner, slug, "edit");
  const guest = await guestAt(browser, baseURL, url);
  await guest.getByLabel("Sinu nimi").fill("Paha");
  await guest.getByRole("button", { name: "Alusta" }).click();
  await waitReady(guest);
  await expect(guest.locator("#share-editor .mu-editor h1")).toBeVisible();

  const before = await owner.evaluate(() => getComputedStyle(document.body).display);
  await guest.locator("#share-editor .mu-editor h1").click();
  await guest.keyboard.press("End");
  await guest.keyboard.press("Enter");
  // Pasted, the HTML block reaches the owner in one op, so the owner's editor renders it.
  await guest.evaluate(() => {
    const data = new DataTransfer();
    data.setData("text/plain", "<div>\n<img src=x onerror=window.__xss=1><b>paks</b><style>body{display:none}</style>\n</div>\n\nLõpp");
    document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  });

  const preview = owner.locator("#editor .mu-html-preview");
  await expect(preview.locator("b")).toHaveText("paks", { timeout: 10_000 });
  await expect(preview.locator("img")).toHaveCount(1);
  await owner.waitForTimeout(500);
  expect(await preview.locator("img").getAttribute("onerror")).toBeNull();
  expect(await owner.evaluate(() => window.__xss)).toBeUndefined();
  expect(await owner.evaluate(() => document.querySelectorAll("#editor style").length)).toBe(0);
  expect(await owner.evaluate(() => getComputedStyle(document.body).display)).toBe(before);
  await expect(owner.locator("#editor .mu-editor h1")).toBeVisible();
  // The text is kept as written; only its rendering is defused.
  await expect.poll(async () => (await api(owner, `/api/notes/${slug}`)).data.body, { timeout: 10_000 }).toContain("<img src=x onerror=window.__xss=1>");
  await close(owner, guest);
});
