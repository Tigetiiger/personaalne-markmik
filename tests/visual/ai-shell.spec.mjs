// The AI on the real note page (WP-C's shell): the Abiline tab, the ✦ in the metadata row, and an
// inline edit, accepted in the review window, through the shell's per-opening editor handle, saved
// like typing.
import { expect, test } from "@playwright/test";

import { api, createNote, enter, openNote, unique, watchErrors } from "./shell-helpers.mjs";

test.describe.configure({ mode: "serial" });
watchErrors(test);

const body = (page, slug) => api(page, `/api/notes/${slug}`).then((r) => r.data.body);
const fake = (page, script) => api(page, "/_test/ai-fake", { method: "POST", body: script });

test.afterEach(async ({ page }) => {
  await fake(page, {});
});

test("the Abiline tab answers about the open note and ✦ rewrites a selection", async ({ page, isMobile }) => {
  await enter(page, "/tana");
  const note = await createNote(page, unique("ai-kest"), "Esimene lause on siin. Teine lause.");
  await openNote(page, note.slug);

  if (isMobile) await page.locator("#note-meta .meta-panel").click(); // the phone's bottom sheet
  await page.locator('#panel-tabs button[data-panel="assistant"]').click();
  await fake(page, { reply: "Siin on **kaks** lauset." });
  await page.getByLabel("Sõnum Abilisele").fill("Mitu lauset siin on?");
  await page.getByLabel("Sõnum Abilisele").press("Enter");
  await expect(page.locator(".ai-assistant strong")).toHaveText("kaks");
  expect((await api(page, `/api/ai/threads/${note.slug}`)).data.map((m) => m.role)).toEqual(["user", "assistant"]);
  if (isMobile) await page.locator("#right .pane-close").click();

  await fake(page, { reply: "Lühike." });
  await page.locator("#editor .mu-editor p", { hasText: "Esimene lause" }).click();
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.querySelector("#editor .mu-editor"), NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = node.data.indexOf("Esimene lause on siin.");
      if (at < 0) continue;
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + "Esimene lause on siin.".length);
      document.getSelection().removeAllRanges();
      document.getSelection().addRange(range);
      return;
    }
  });
  await page.locator('.meta-action[data-action="ai"] .ai-meta').click();
  const box = page.locator(".ai-inline");
  await box.getByRole("button", { name: "Lühemaks" }).click();
  await box.getByRole("button", { name: "Asenda" }).click();
  await expect(box).toBeHidden();
  await page.getByRole("dialog", { name: "Vaata muudatused üle" }).getByRole("button", { name: "Aktsepteeri" }).click();
  await expect(page.locator("#editor .mu-editor")).toContainText("Lühike. Teine lause.");
  await expect.poll(() => body(page, note.slug), { timeout: 10_000 }).toContain("Lühike. Teine lause.");
});
