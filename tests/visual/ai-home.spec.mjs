// The home chat on / (docs/KODU-PLAN.md) with the fake model: streaming, the kept thread and
// "Uus vestlus", pictures, reminder cards and Täna's list, proposals for any note reviewed on the
// home page, and on the note's own page when it is open elsewhere.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });

// A 24×16 red PNG.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAABgAAAAQCAIAAACDRijCAAAAGklEQVR4nGP8z0AdwEQlc0YNGjVo1KARaxAA2D0BHwIRnKYAAAAASUVORK5CYII=", "base64");
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const fake = (page, script) => api(page, "/_test/ai-fake", { method: "POST", body: script });
const tomorrow = () => {
  const d = new Date(Date.now() + 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

async function home(page) {
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, "/");
  const chat = page.locator(".ai-home");
  await expect(chat).toBeVisible();
  await chat.getByRole("button", { name: "Uus vestlus" }).click();
  await expect(chat.getByRole("button", { name: "Saada" })).toBeEnabled();
  await expect(chat.locator(".ai-msg")).toHaveCount(0);
  return chat;
}

async function send(chat, text) {
  await chat.locator(".ai-form textarea").fill(text);
  await chat.locator(".ai-form textarea").press("Enter");
  await expect(chat.locator(".ai-streaming")).toHaveCount(0);
}

test.afterEach(async ({ page }) => {
  if (!page.url().startsWith("http")) return; // a skipped test opened no page
  await fake(page, {});
  expect(page.errors || []).toEqual([]);
});

test("/ shows the chat; the thread stays after a reload; Uus vestlus empties it", async ({ page, isMobile }) => {
  const chat = await home(page);
  if (!isMobile) await expect(page.locator(".pane.left")).toBeVisible();
  await fake(page, { reply: "Tere! Täna on vaikne päev." });
  await send(chat, "Mis mul täna on?");
  await expect(chat.locator(".ai-assistant").last()).toHaveText("Tere! Täna on vaikne päev.");
  await expect(chat.locator(".ai-suggest")).toBeHidden();
  await page.reload();
  await expect(page.locator(".ai-home .ai-user")).toHaveText("Mis mul täna on?");
  await page.locator(".ai-home").getByRole("button", { name: "Uus vestlus" }).click();
  await expect(page.locator(".ai-home .ai-msg")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".ai-home .ai-suggest")).toBeVisible();
  await expect(page.locator(".ai-home .ai-msg")).toHaveCount(0);
});

test("a picture goes with the message and stays in the thread", async ({ page }) => {
  const chat = await home(page);
  await chat.locator("input[type=file]").setInputFiles({ name: "tahvel.png", mimeType: "image/png", buffer: PNG });
  await expect(chat.locator(".ai-tray .ai-thumb")).toHaveCount(1);
  await fake(page, { reply: "Pildil on punane ruut." });
  const request = page.waitForRequest((r) => r.url().endsWith("/api/ai/chat"));
  await send(chat, "Mis pildil on?");
  const sent = (await request).postDataJSON();
  expect(sent.images).toHaveLength(1);
  expect(sent.slug).toBeUndefined();
  await expect(chat.locator(".ai-tray .ai-thumb")).toHaveCount(0);
  await expect(chat.locator(".ai-user .ai-pictures img")).toHaveAttribute("src", `/pildid/${sent.images[0]}`);
  await page.reload();
  await expect(page.locator(".ai-home .ai-user .ai-pictures img")).toHaveAttribute("src", `/pildid/${sent.images[0]}`);
  // A picture of the thread goes back into the next message.
  await page.locator(".ai-home .ai-user .ai-thumb").click();
  await expect(page.locator(".ai-home .ai-tray .ai-thumb")).toHaveCount(1);
});

test("a reminder from the chat is a card; Täna lists it with ✓ and ✕", async ({ page }) => {
  const chat = await home(page);
  const text = unique("Helista vanaemale");
  await fake(page, { reply: "Saadan homme kell 18 meeldetuletuse.", tool: { name: "loo_meeldetuletus", input: { text, date: tomorrow(), time: "18:00" } } });
  await send(chat, "tuleta homme kell 18 meelde helistada vanaemale");
  const card = chat.locator(".ai-reminder").filter({ hasText: text });
  await expect(card).toContainText("kell 18.00");
  await page.goto(`/tana?paev=${tomorrow()}`);
  const row = page.locator(".today-reminders li").filter({ hasText: text });
  await expect(row.locator(".agenda-time")).toHaveText("18:00");
  await expect(page.locator(".today-block").filter({ hasText: "Meeldetuletused" })).toContainText("ntfy pole seadistatud");
  await row.locator("input[type=checkbox]").check();
  await expect(row).toHaveClass(/done/);
  await row.getByRole("button", { name: "Kustuta meeldetuletus" }).click();
  await expect(row).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".today-reminders li").filter({ hasText: text })).toHaveCount(0);
});

test("a proposal for another note is reviewed and applied on the home page", async ({ page }) => {
  const chat = await home(page);
  const title = unique("Poe nimekiri");
  const note = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\n- piim\n` } })).data;
  await fake(page, { reply: "Lisasin leiva.", tool: { name: "lisa_markmesse", input: { slug: note.slug, text: "- leib" } } });
  await send(chat, `lisa leib nimekirja ${title}`);
  const dialog = page.locator("dialog.ai-review");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".ai-review-ins, ins.ai-ins").first()).toContainText("leib");
  await dialog.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(chat.locator(".ai-proposal")).toContainText("Rakendatud");
  expect((await api(page, `/api/notes/${note.slug}`)).data.body).toBe(`# ${title}\n\n- piim\n- leib\n`);
  await page.reload();
  await expect(page.locator(".ai-home .ai-proposal")).toContainText("Rakendatud");
});

test("a note open elsewhere is reviewed on its own page", async ({ page, context, isMobile }) => {
  test.skip(isMobile, "two windows");
  const chat = await home(page);
  const title = unique("Retsept");
  const note = (await api(page, "/api/notes", { method: "POST", body: { title, body: `# ${title}\n\nJahu 200 g.\n` } })).data;
  const other = await context.newPage();
  await other.goto(`/m/${note.slug}`);
  await expect(other.locator("#editor .mu-editor")).toBeVisible();
  await fake(page, { reply: "Lisasin.", tool: { name: "lisa_markmesse", input: { slug: note.slug, text: "Suhkur 50 g." } } });
  await send(chat, "lisa suhkur retsepti");
  await page.locator("dialog.ai-review").getByRole("button", { name: "Aktsepteeri" }).click();
  const go = chat.getByRole("link", { name: "Ava märge ja vaata seal üle" });
  await expect(go).toBeVisible();
  await other.close();
  await go.click();
  await expect(page).toHaveURL(new RegExp(`/m/${note.slug}$`));
  const dialog = page.locator("dialog.ai-review");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Aktsepteeri" }).click();
  await expect.poll(async () => (await api(page, `/api/notes/${note.slug}`)).data.body).toContain("Suhkur 50 g.");
});

test("phone: the composer sits at the bottom and nothing scrolls sideways", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone only");
  const chat = await home(page);
  const box = await chat.locator(".ai-form").boundingBox();
  const height = page.viewportSize().height;
  expect(box.y + box.height).toBeLessThanOrEqual(height + 1);
  expect(box.y + box.height).toBeGreaterThan(height - 40);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
