// The Abiline panel with the fake model (AI_FAKE=1): streamed answers, the thread kept per note,
// event cards with Eemalda, the "Kasuta valikut" chip and edits to the open note.
import { DatabaseSync } from "node:sqlite";

import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

const DB = `.vt-data-${process.env.VT_PORT || 8780}/test.db`;
const unique = (name) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test.describe.configure({ mode: "serial" });
// The harness page lays the panel out in the desktop's right pane, which a phone hides; the phone's
// bottom sheet with the real panel is covered by ai-shell.spec.mjs.
test.skip(({ isMobile }) => isMobile, "desktop harness layout");

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

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.expectedStatus === "skipped") return;
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

  await card.getByRole("button", { name: "Eemalda" }).click();
  await expect(card).toContainText("Eemaldatud");
  const after = new DatabaseSync(DB, { readOnly: true });
  expect(after.prepare("SELECT status FROM events WHERE id = ?").get(row.id).status).toBe("eemaldatud");
  after.close();
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

test("text proposed for the open note goes in only on Lisa, through the editor", async ({ page }) => {
  const slug = await openNote(page, "# Märge\n\nEsimene lõik.\n");
  const proposal = "Uus **lõik** [allikas](https://example.org/?d=PIN4711).";
  await fake(page, { reply: "Pakun.", tool: { name: "lisa_markmesse", input: { slug, text: proposal } } });
  await ask(page, "Lisa märkmesse lõik");
  const card = page.locator(".ai-proposal").last();
  // Shown as the source that goes in: a rendered link would hide its target.
  await expect(card.locator("pre")).toHaveText(proposal);
  await expect(card.locator("a, strong")).toHaveCount(0);
  expect(await markdown(page)).toBe("# Märge\n\nEsimene lõik.\n");
  await card.getByRole("button", { name: "Lisa" }).click();
  await expect(card).toContainText("Lisatud");
  await expect.poll(() => markdown(page)).toBe(`# Märge\n\nEsimene lõik.\n\n${proposal}\n`);
  // An ordinary edit: saved by the editor like typing.
  await expect.poll(() => api(page, `/api/notes/${slug}`).then((r) => r.data.body)).toContain(proposal);

  await fake(page, { reply: "Veel.", tool: { name: "lisa_markmesse", input: { slug, text: "Teine ettepanek." } } });
  await ask(page, "Lisa veel");
  const second = page.locator(".ai-proposal").last();
  await second.getByRole("button", { name: "Loobu" }).click();
  await expect(second).toContainText("Loobutud");
  expect(await markdown(page)).not.toContain("Teine ettepanek.");

  // Refused (another note): no proposal now, and none after a reload.
  await fake(page, { reply: "Ei saa.", tool: { name: "lisa_markmesse", input: { slug: "muu-marge", text: "Kolmas." } } });
  await ask(page, "Lisa teise märkmesse");
  await expect(page.locator(".ai-assistant").last()).toHaveText("Ei saa.");
  await expect(page.locator(".ai-proposal")).toHaveCount(2);
  await page.reload();
  await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
  await expect(page.locator(".ai-assistant")).toHaveCount(3);
  await expect(page.locator(".ai-tool", { hasText: "Pakkus teksti märkmesse" })).toHaveCount(2);
});

test("warns while guests can edit the note through a link", async ({ page }) => {
  const slug = await openNote(page);
  const warning = page.locator(".ai-guests");
  const reload = async () => {
    const checked = page.waitForResponse((r) => r.url().includes("/api/shares?slug="));
    await page.reload();
    await expect(page.locator("body")).toHaveAttribute("data-ai", "ready");
    await checked;
  };
  await api(page, "/api/shares", { method: "POST", body: { slug, mode: "view" } });
  await reload();
  await expect(warning).toBeHidden(); // guests may only look
  const edit = await api(page, "/api/shares", { method: "POST", body: { slug, mode: "edit" } });
  await reload();
  await expect(warning).toBeVisible();
  await expect(warning).toHaveText("Seda märget saavad muuta ka lingiga külalised. Ära lase AI-l täita märkmes olevaid juhiseid pimesi.");
  // Revoked in the Jaga dialog, which announces it: the warning goes without a reload.
  await api(page, `/api/shares/${edit.data.id}`, { method: "DELETE" });
  await page.evaluate((slug) => import("/static/app.js").then(({ emit }) => emit("shares:changed", { slug })), slug);
  await expect(warning).toBeHidden();
});

test("the model's Markdown becomes text and safe elements only", async ({ page }) => {
  await openNote(page);
  const html = await page.evaluate(async () => {
    const { renderMarkdown } = await import("/static/ai/index.js");
    const box = document.createElement("div");
    box.append(renderMarkdown([
      "<script>window.pwned = 1</script> <img src=x onerror=\"window.pwned = 2\">",
      "**<b>bold</b>** `<i>code</i>` *<svg onload=alert(1)>*",
      "- [ok](/m/teine) [web](https://example.com/x) [js](javascript:alert(1)) [data](data:text/html,x)",
      "- [slash](//evil.example) [back](/\\evil.example) [BACK](/%5C%5Cx)",
      "```",
      "<iframe src=//evil></iframe>",
      "```",
    ].join("\n")));
    return {
      tags: [...box.querySelectorAll("*")].map((e) => e.tagName.toLowerCase()),
      links: [...box.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href"), a.target]),
      text: box.textContent,
      pwned: window.pwned,
    };
  });
  expect(html.pwned).toBeUndefined();
  expect(new Set(html.tags)).toEqual(new Set(["p", "br", "strong", "code", "em", "ul", "li", "a", "pre"]));
  expect(html.links).toEqual([["ok", "/m/teine", ""], ["web", "https://example.com/x", "_blank"], ["BACK", "/%5C%5Cx", ""]]);
  expect(html.text).toContain("<script>window.pwned = 1</script>");
  expect(html.text).toContain("<iframe src=//evil></iframe>");
  expect(html.text).toContain("js"); // dropped links keep their label as text
});
