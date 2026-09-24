// The Abiline panel with the fake model (AI_FAKE=1): streamed answers, the thread kept per note,
// event cards with Eemalda, the "Kasuta valikut" chip and edits to the open note.
import { DatabaseSync } from "node:sqlite";

import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

const DB = `.vt-data-${process.env.VT_PORT || 8780}/test.db`;
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });

async function openNote(page, markdown) {
  const slug = unique("abiline");
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, `/_test/ai/${slug}`);
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  if (markdown) {
    await page.evaluate((md) => window.editor.replaceMarkdown(md), markdown);
    // Saved, so the chat's note context has it.
    await expect.poll(() => api(page, `/api/notes/${slug}`).then((r) => r.data.body)).toContain(markdown.split("\n")[2]);
  }
  return slug;
}

const fake = (page, script) => api(page, "/_test/ai-fake", { method: "POST", body: script });
const markdown = (page) => page.evaluate(() => window.editor.getMarkdown());

/** Select `text` in the editor as a user's drag would. */
async function selectText(page, text) {
  await page.evaluate((text) => {
    const walker = document.createTreeWalker(document.querySelector("#editor .mu-editor"), NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = node.data.indexOf(text);
      if (at < 0) continue;
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + text.length);
      document.getSelection().removeAllRanges();
      document.getSelection().addRange(range);
      return;
    }
    throw new Error(`"${text}" is not in the editor`);
  }, text);
}

async function ask(page, text) {
  await page.getByLabel("Sõnum Abilisele").fill(text);
  await page.getByLabel("Sõnum Abilisele").press("Enter");
}

test.afterEach(async ({ page }) => {
  await fake(page, {});
  expect(page.errors, "JavaScript errors on the page").toEqual([]);
});

test("streams the canned answer and keeps the thread with the note", async ({ page }) => {
  const slug = await openNote(page);
  await expect(page.locator(".ai-empty")).toBeVisible();
  await ask(page, "Mis mul reedel on?");
  const answer = page.locator(".ai-assistant").last();
  await expect(answer).toContainText("Proovivastus (AI_FAKE):");
  await expect(answer).not.toHaveClass(/ai-streaming/);
  await expect(page.locator(".ai-user")).toHaveText("Mis mul reedel on?");

  await fake(page, { reply: "Reedel on **matemaatika kontrolltöö**.\n\n- õpi valemid\n- maga" });
  await ask(page, "Ja veel?");
  await expect(page.locator(".ai-msg")).toHaveCount(4);
  const second = page.locator(".ai-assistant").last();
  await expect(second.locator("strong")).toHaveText("matemaatika kontrolltöö");
  await expect(second.locator("li")).toHaveText(["õpi valemid", "maga"]);

  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  await expect(page.locator(".ai-msg")).toHaveCount(4);
  await expect(page.locator(".ai-assistant").last().locator("strong")).toHaveText("matemaatika kontrolltöö");
  const thread = await api(page, `/api/ai/threads/${slug}`);
  expect(thread.data.map((m) => m.role)).toEqual(["user", "assistant", "user", "assistant"]);
});

test("an event the assistant adds shows as a card with Eemalda", async ({ page }) => {
  await openNote(page);
  await fake(page, {
    reply: "Lisasin kontrolltöö kalendrisse.",
    tool: { name: "loo_sundmus", input: { title: "Matemaatika kontrolltöö", date: "2026-10-02", time: "14:15", kind: "kontrolltöö" } },
  });
  await ask(page, "Lisa kalendrisse matemaatika kontrolltöö 2. oktoobril kell 14.15");
  const card = page.locator(".ai-event");
  await expect(card).toContainText("Matemaatika kontrolltöö");
  await expect(card).toContainText("2.10.2026 kell 14.15");
  const db = new DatabaseSync(DB, { readOnly: true });
  const row = db.prepare("SELECT id, source, status, start_at FROM events ORDER BY id DESC LIMIT 1").get();
  db.close();
  expect(row).toMatchObject({ source: "ai", status: "kinnitatud", start_at: "2026-10-02T11:15:00Z" });

  // DELETE /api/events/<id> belongs to the calendar (WP-D), a stub on this branch: answered here.
  let deleted = null;
  await page.route("**/api/events/*", (route) => {
    deleted = `${route.request().method()} ${new URL(route.request().url()).pathname}`;
    return route.fulfill({ status: 204 });
  });
  await card.getByRole("button", { name: "Eemalda" }).click();
  await expect(card).toContainText("Eemaldatud");
  expect(deleted).toBe(`DELETE /api/events/${row.id}`);
});

test("Kasuta valikut sends the editor's selection with the question", async ({ page }) => {
  await openNote(page, "# Valik\n\nKontrolltöö on reedel ruumis 204.\n");
  const chip = page.getByRole("button", { name: "Kasuta valikut" });
  await expect(chip).toBeHidden();
  await page.locator("#editor .mu-editor p").first().click();
  await selectText(page, "Kontrolltöö");
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  const request = page.waitForRequest("**/api/ai/chat");
  await ask(page, "Mis see on?");
  expect((await request).postDataJSON()).toMatchObject({ message: "Mis see on?", selection: "Kontrolltöö" });
  await expect(page.locator(".ai-assistant").last()).toContainText("Proovivastus");
});

test("text the assistant adds to the open note goes in through the editor", async ({ page }) => {
  const slug = await openNote(page, "# Märge\n\nEsimene lõik.\n");
  await fake(page, { reply: "Lisasin.", tool: { name: "lisa_markmesse", input: { slug, text: "Uus lõik abiliselt." } } });
  await ask(page, "Lisa märkmesse lõik");
  await expect.poll(() => markdown(page)).toContain("Esimene lõik.\n\nUus lõik abiliselt.");
  await expect(page.locator(".ai-tool")).toContainText("Lisas märkmesse");
  // An ordinary edit: saved by the editor like typing.
  await expect.poll(() => api(page, `/api/notes/${slug}`).then((r) => r.data.body)).toContain("Uus lõik abiliselt.");
});
