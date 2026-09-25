// The note page (WP-C): new note, switching notes in place, links and the Viited panel.
import { expect, test } from "@playwright/test";

import { api, caretAtEnd, createNote, editor, enter, openNote, opened, saveState, unique, waitReady, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

test("+ Uus puts the caret in the title; typing saves; a reload shows the text", async ({ page }) => {
  await enter(page, "/");
  await expect(page.locator("#note-empty")).toHaveText("Vali vasakult märge või loo uus.");
  await page.click(".side-new");
  await expect(page).toHaveURL(/\/m\/marge(-\d+)?$/);
  const slug = new URL(page.url()).pathname.slice(3);
  await opened(page, slug);
  const title = unique("Esimene ");
  await page.keyboard.type(title);
  await page.keyboard.press("Enter");
  await page.keyboard.type("Esimene lõik.");
  await expect(saveState(page)).toHaveText("Salvestamata…");
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await expect(page).toHaveTitle(`${title} · Märkmik`);
  await expect(page.locator("#note-meta .meta-words")).toHaveText("4 sõna");
  await expect(page.locator(`#left a.side-note[aria-current="page"]`).first()).toHaveText(title);
  await page.reload();
  await waitReady(page);
  await opened(page, slug);
  await expect(editor(page).locator("h1")).toContainText(title);
  await expect(editor(page)).toContainText("Esimene lõik.");
  expect((await api(page, `/api/notes/${slug}`)).data.body).toBe(`# ${title}\n\nEsimene lõik.\n`);
});

test("switching notes by sidebar, Ctrl+click and back keeps one editor and the right text", async ({ page }) => {
  await enter(page, "/");
  const b = await createNote(page, unique("Bee "), "Bee sisu.");
  const a = await createNote(page, unique("Aa "), `Aa sisu.\n\nVaata [seda](/m/${b.slug}) ka.`);
  await openNote(page, a.slug);
  const loads = [];
  page.on("load", () => loads.push(page.url()));

  // Type into a, then switch away through the sidebar and back: the text is kept.
  await caretAtEnd(page, "p", 0);
  await page.keyboard.type(" Lisatud.");
  await page.locator("#left a.side-note", { hasText: b.title }).first().click();
  await opened(page, b.slug);
  await expect(page).toHaveURL(`/m/${b.slug}`);
  await expect(editor(page)).toContainText("Bee sisu.");
  await expect(editor(page)).not.toContainText("Aa sisu.");
  await page.goBack();
  await opened(page, a.slug);
  await expect(editor(page)).toContainText("Aa sisu. Lisatud.");

  // Ctrl+click on the link in a opens b; back returns to a.
  await editor(page).locator("span.mu-link", { hasText: "seda" }).click({ modifiers: ["Control"] });
  await opened(page, b.slug);
  await expect(editor(page)).toContainText("Bee sisu.");
  await page.goBack();
  await opened(page, a.slug);
  await page.goForward();
  await opened(page, b.slug);

  expect(loads).toEqual([]); // never a page load
  await expect(page.locator(".mu-editor")).toHaveCount(1);
  await expect(page.locator(".mt-bar")).toHaveCount(1);
  expect((await api(page, `/api/notes/${a.slug}`)).data.body).toContain("Aa sisu. Lisatud.");
});

test("an unknown slug says so", async ({ page }) => {
  await enter(page, "/m/sellist-marget-pole");
  await expect(page.locator("#note")).toHaveAttribute("data-open", "puudub");
  await expect(page.locator("#note-empty h1")).toHaveText("Märget ei leitud");
  await expect(page.locator("#editor")).toBeHidden();
  await expect(page).toHaveTitle("Märget ei leitud · Märkmik");

  // From an open note through a link to a note that does not exist, and back.
  const a = await createNote(page, unique("Viitab tühjusse "), "Vaata [puuduvat](/m/sellist-pole-ka) märget.");
  await openNote(page, a.slug);
  await editor(page).locator("span.mu-link", { hasText: "puuduvat" }).click({ modifiers: ["Control"] });
  await expect(page.locator("#note")).toHaveAttribute("data-open", "puudub");
  await expect(page.locator("#note-empty h1")).toHaveText("Märget ei leitud");
  await expect(page.locator("#panel-tabs button")).toHaveCount(0);
  await page.goBack();
  await opened(page, a.slug);
  await expect(editor(page).locator("p")).toContainText("puuduvat");
  await expect(page.locator(".mu-editor")).toHaveCount(1);
});

test("metadata row: tags, pin and folder change the note and the sidebar", async ({ page }) => {
  await enter(page, "/");
  const n = await createNote(page, unique("Silditud "));
  await openNote(page, n.slug);
  const meta = page.locator("#note-meta");
  await meta.locator(".chip-add").click();
  await page.keyboard.type("#kool");
  await page.keyboard.press("Enter");
  await expect(meta.locator(".chip")).toHaveText(["#kool×", "+ silt"]);
  await meta.locator(".meta-pin").click();
  await expect(meta.locator(".meta-pin")).toHaveAttribute("aria-pressed", "true");
  await meta.locator(".meta-crumb").click();
  await page.keyboard.type("kool/matemaatika");
  await page.keyboard.press("Enter");
  await expect(meta.locator(".meta-crumb")).toHaveText("kool/matemaatika");
  expect((await api(page, `/api/notes/${n.slug}`)).data).toMatchObject({ tags: ["kool"], pinned: true, folder: "kool/matemaatika" });

  const side = page.locator("#left");
  await expect(side.locator(".side-section", { hasText: "Kinnitatud" }).locator("a.side-note", { hasText: n.title })).toBeVisible();
  await side.locator(".side-tag", { hasText: "#kool" }).click();
  await expect(side.locator(".side-tags a.side-note", { hasText: n.title })).toBeVisible();
  await side.locator("summary[data-folder='kool']").click();
  await side.locator("summary[data-folder='kool/matemaatika']").click();
  await expect(side.locator(".side-tree a.side-note", { hasText: n.title })).toBeVisible();

  await meta.locator(".chip-x").click();
  await expect(meta.locator(".chip")).toHaveText(["+ silt"]);
  expect((await api(page, `/api/notes/${n.slug}`)).data.tags).toEqual([]);
});

test("dragging a note onto a folder moves it", async ({ page }) => {
  await enter(page, "/");
  const folder = unique("kaust");
  await createNote(page, unique("Kaustas "), undefined).then((n) => api(page, `/api/notes/${n.slug}`, { method: "PATCH", body: { folder } }));
  const n = await createNote(page, unique("Liigub "));
  await openNote(page, n.slug);
  const side = page.locator("#left");
  await side.locator(".side-section", { hasText: "Hiljutised" }).locator("a.side-note", { hasText: n.title }).dragTo(side.locator(`summary[data-folder='${folder}']`));
  await expect(page.locator("#toast")).toHaveText(`Teisaldatud kausta ${folder}.`);
  await expect(page.locator("#note-meta .meta-crumb")).toHaveText(folder);
  expect((await api(page, `/api/notes/${n.slug}`)).data.folder).toBe(folder);
});

test("search in the sidebar marks the hits", async ({ page }) => {
  await enter(page, "/");
  const word = unique("otsisõna");
  const n = await createNote(page, unique("Otsitav "), `Siin on ${word} keset lauset.`);
  await page.reload();
  await waitReady(page);
  await page.fill(".side-search", word);
  const hit = page.locator(".side-results li", { hasText: n.title });
  await expect(hit.locator("mark")).toHaveText(word);
  await hit.locator("a").click();
  await opened(page, n.slug);
});

test("a [[ link makes a backlink in the target's Viited; an unlinked mention gets linked with Seo", async ({ page }) => {
  await enter(page, "/");
  const target = await createNote(page, unique("Sihtmärk "));
  const source = await createNote(page, unique("Allikas "));
  const mentioner = await createNote(page, unique("Mainija "), `Siin räägitakse teemast ${target.title} lihtsalt tekstina.`);
  await openNote(page, source.slug);
  await caretAtEnd(page, "h1");
  await page.keyboard.press("Enter");
  await page.keyboard.type(`Vaata [[${target.title.slice(0, 10)}`);
  await page.locator(".mt-wikilink .mt-wikilink-item", { hasText: target.title }).click();
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await expect.poll(async () => (await api(page, `/api/notes/${source.slug}`)).data.body).toContain(`(/m/${target.slug})`);

  // Viited of the source lists the link out.
  const panel = page.locator('#panel-body .panel[data-panel="viited"]');
  await page.locator('#panel-tabs button[data-panel="viited"]').click();
  await expect(panel.locator(".links-block", { hasText: "Lingid siit" }).locator("a", { hasText: target.title })).toBeVisible();

  // The target: the source as a backlink, the mentioner as an unlinked mention.
  await page.locator(".side-section", { hasText: "Hiljutised" }).locator("a.side-note", { hasText: target.title }).click();
  await opened(page, target.slug);
  const back = panel.locator(".links-block", { hasText: "Tagasiviited" });
  await expect(back.locator("a", { hasText: source.title })).toBeVisible();
  await expect(back.locator(".links-snippet")).toContainText("Vaata");
  const mentions = panel.locator(".links-block", { hasText: "Mainimised" });
  await expect(mentions.locator("li", { hasText: mentioner.title }).locator(".links-snippet")).toContainText("lihtsalt tekstina");
  await mentions.locator("li", { hasText: mentioner.title }).getByRole("button", { name: "Seo" }).click();
  await expect(page.locator("#toast")).toHaveText("Seotud.");
  await expect(back.locator("a", { hasText: mentioner.title })).toBeVisible();
  await expect(mentions.locator("li", { hasText: mentioner.title })).toHaveCount(0);
  expect((await api(page, `/api/notes/${mentioner.slug}`)).data.body).toContain(`[${target.title}](/m/${target.slug})`);
});

test("Seo on a note open elsewhere says it is busy", async ({ page, browser }) => {
  await enter(page, "/");
  const target = await createNote(page, unique("Hõivatud "));
  const mentioner = await createNote(page, unique("Avatud "), `Tekstis on ${target.title} mainitud.`);
  // The mentioner is open in a second window.
  const other = await page.context().newPage();
  await other.goto(`/m/${mentioner.slug}`);
  await opened(other, mentioner.slug);
  await openNote(page, target.slug);
  await page.locator('#panel-tabs button[data-panel="viited"]').click();
  const row = page.locator(".links-block", { hasText: "Mainimised" }).locator("li", { hasText: mentioner.title });
  await row.getByRole("button", { name: "Seo" }).click();
  await expect(page.locator("#toast")).toHaveText("Märget muudetakse teises aknas.");
  await expect(row.getByRole("button", { name: "Seo" })).toBeEnabled();
  await other.close();
});

test("Sisukord lists the headings and scrolls to one", async ({ page }) => {
  await enter(page, "/");
  const filler = Array.from({ length: 40 }, (_, i) => `Rida ${i}.`).join("\n\n");
  const n = await createNote(page, unique("Sisukorraga "), `## Algus\n\n${filler}\n\n### Lõpupeatükk\n\nViimane.`);
  await openNote(page, n.slug);
  await page.locator('#panel-tabs button[data-panel="sisukord"]').click();
  const outline = page.locator('#panel-body .panel[data-panel="sisukord"] .outline button');
  await expect(outline).toHaveText([n.title, "Algus", "Lõpupeatükk"]);
  await outline.nth(2).click();
  await expect(editor(page).locator("h3")).toBeInViewport();
  // The last tab chosen is remembered for the next note.
  await page.reload();
  await waitReady(page);
  await expect(page.locator('#panel-tabs button[data-panel="sisukord"]')).toHaveAttribute("aria-selected", "true");
  await page.locator('#panel-tabs button[data-panel="viited"]').click();
});

test("other packages' meta actions and panels mount per note with the note and editor", async ({ page }) => {
  await enter(page, "/");
  const a = await createNote(page, unique("Pistik A "));
  const b = await createNote(page, unique("Pistik B "));
  await openNote(page, a.slug);
  // What WP-F/G/H do from their own modules: register, then the shell mounts them for each note.
  await page.evaluate(async () => {
    const app = await import("/static/app.js");
    window.seen = [];
    app.on("editor:ready", ({ note, editor }) => window.seen.push(["ready", note.slug, typeof editor.getMarkdown]));
    app.registerMetaAction("proov", { order: 5, mount: (el, ctx) => { el.textContent = `✦ ${ctx.note.slug}`; } });
    app.registerPanel("proov", { title: "Proov", order: 99, mount: (el, ctx) => { el.textContent = `paneel ${ctx.note.slug}`; return () => window.seen.push(["cleanup", ctx.note.slug]); } });
  });
  await page.locator("#left a.side-note", { hasText: b.title }).first().click();
  await opened(page, b.slug);
  await expect(page.locator('#note-meta .meta-action[data-action="proov"]')).toHaveText(`✦ ${b.slug}`);
  await page.locator('#panel-tabs button[data-panel="proov"]').click();
  await expect(page.locator('#panel-body .panel[data-panel="proov"]')).toHaveText(`paneel ${b.slug}`);
  await page.goBack();
  await opened(page, a.slug);
  // The remembered tab mounts right away for the next note; the old one was cleaned up.
  await expect(page.locator('#panel-body .panel[data-panel="proov"]')).toHaveText(`paneel ${a.slug}`);
  expect(await page.evaluate(() => window.seen)).toEqual([["ready", b.slug, "function"], ["cleanup", b.slug], ["ready", a.slug, "function"]]);
  await page.locator('#panel-tabs button[data-panel="viited"]').click();
});

test("a plugin's editor handle goes dead when its note is left; late and left meta actions clean up", async ({ page }) => {
  await enter(page, "/");
  const a = await createNote(page, unique("Vana "), "Vana tekst.");
  const b = await createNote(page, unique("Uus "), "Uus tekst.");
  await openNote(page, a.slug);
  await page.evaluate(async () => {
    const app = await import("/static/app.js");
    window.log = [];
    window.handles = {};
    app.registerMetaAction("aeglane", {
      mount: async (el, ctx) => {
        window.handles[ctx.note.slug] = ctx.editor;
        if (!window.slowDone) await new Promise((r) => setTimeout(r, 1500)); // resolves after the switch
        window.slowDone = true;
        return () => window.log.push(`cleanup ${ctx.note.slug}`);
      },
    });
  });
  await page.locator("#left a.side-note", { hasText: b.title }).first().click(); // mounts for b (slow)
  await opened(page, b.slug);
  await page.locator("#left a.side-note", { hasText: a.title }).first().click(); // before b's mount is done
  await opened(page, a.slug);
  await expect.poll(() => page.evaluate(() => window.log)).toEqual([`cleanup ${b.slug}`]);
  // b's handle is dead: it reads nothing and edits nothing in a, which is now in the editor.
  expect(await page.evaluate((s) => window.handles[s].getMarkdown(), b.slug)).toBeNull();
  expect(await page.evaluate((s) => window.handles[s].applyBlockEdit([0], "x"), b.slug)).toBe(false);
  expect(await page.evaluate((s) => window.handles[s].getMarkdown(), a.slug)).toContain("Vana tekst.");
  // A missing note shows no actions and cleans up the open note's.
  await page.goto(`/m/${a.slug}`); // a fresh page: the slow action is gone
  await waitReady(page);
  await opened(page, a.slug);
  await page.evaluate(async () => {
    const app = await import("/static/app.js");
    window.log = [];
    app.registerMetaAction("korras", { mount: (el, ctx) => () => window.log.push(`cleanup ${ctx.note.slug}`) });
  });
  await page.locator("#left a.side-note", { hasText: b.title }).first().click();
  await opened(page, b.slug);
  await page.evaluate(() => {
    history.pushState({}, "", "/m/sellist-pole-olemas");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.locator("#note")).toHaveAttribute("data-open", "puudub");
  await expect.poll(() => page.evaluate(() => window.log)).toEqual([`cleanup ${b.slug}`]);
  await expect(page.locator("#note-meta .meta-action")).toHaveCount(0);
});

test("switching away before the autosave: the left note is saved, the metadata row follows the new one", async ({ page }) => {
  await enter(page, "/");
  const b = await createNote(page, unique("Teine "), "Teise sisu.");
  const a = await createNote(page, unique("Esimene "), "Esimese sisu.");
  await openNote(page, a.slug);
  const saved = [];
  await page.exposeFunction("sawSaved", (slug) => saved.push(slug));
  await page.evaluate(async () => (await import("/static/app.js")).on("note:saved", ({ note }) => window.sawSaved(note.slug)));
  await caretAtEnd(page, "p", 0);
  await page.keyboard.type(" Lisatud.");
  await page.locator("#left a.side-note", { hasText: b.title }).first().click(); // well before the autosave
  await opened(page, b.slug);
  await expect.poll(() => saved).toContain(a.slug);
  await page.waitForTimeout(500);
  await expect(page).toHaveTitle(`${b.title} · Märkmik`);
  await expect(page.locator(`#left a.side-note[aria-current="page"]`).first()).toHaveText(b.title);
  await page.click("#note-meta .meta-pin");
  await expect(page.locator("#note-meta .meta-pin")).toHaveAttribute("aria-pressed", "true");
  expect((await api(page, `/api/notes/${b.slug}`)).data.pinned).toBe(true);
  expect((await api(page, `/api/notes/${a.slug}`)).data).toMatchObject({ pinned: false, body: expect.stringContaining("Esimese sisu. Lisatud.") });
});

test("back to / while the first note is still opening leaves no editor session behind", async ({ page }) => {
  await enter(page, "/");
  const n = await createNote(page, unique("Aeglane "), "Sisu.");
  await page.reload();
  await waitReady(page);
  await page.route("**/api/collab/join", async (route) => {
    await new Promise((r) => setTimeout(r, 1200));
    await route.continue();
  });
  const collab = [];
  page.on("request", (r) => r.url().includes("/api/collab/") && collab.push(r.url().split("/").slice(-1)[0]));
  const joining = page.waitForRequest("**/api/collab/join");
  await page.locator("#left a.side-note", { hasText: n.title }).first().click();
  await joining; // the join is under way (and held up) when the user goes back
  await page.goBack();
  await expect(page.locator("#note-empty")).toHaveText("Vali vasakult märge või loo uus.");
  await expect.poll(() => collab).toContain("leave");
  const settled = collab.length;
  await page.waitForTimeout(3000);
  expect(collab.slice(settled)).toEqual([]); // no more syncing: the session was left
  await expect(page.locator("#editor")).toBeHidden();
  await expect(page.locator(".mu-editor")).toHaveCount(0);
  // The note is not held busy: a server-side write to it goes through.
  await page.goto("/");
  await waitReady(page);
});

test("a note that fails to open leaves the address on the note still shown", async ({ page }) => {
  await enter(page, "/");
  const a = await createNote(page, unique("Näidatud "));
  const b = await createNote(page, unique("Katki "));
  await page.addInitScript(() => import("/static/app.js").then((app) => app.on("editor:ready", ({ editor }) => (window.shownEditor = editor))));
  await openNote(page, a.slug);
  await page.route(`**/api/notes/${b.slug}`, (route) => route.fulfill({ status: 500, json: { error: "Serveri viga." } }));
  await page.locator("#left a.side-note", { hasText: b.title }).first().click();
  await expect(page.locator("#toast")).toHaveText("Serveri viga.");
  await expect(page).toHaveURL(`/m/${a.slug}`);
  await expect(page.locator("#note")).toHaveAttribute("data-open", a.slug);
  // The note still shown is fully alive: its plugin handle reads, its save state follows typing.
  expect(await page.evaluate(() => window.shownEditor.getMarkdown())).toContain(a.title);
  await caretAtEnd(page, "h1");
  await page.keyboard.type(" veel");
  await expect(saveState(page)).toHaveText("Salvestamata…");
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  // So is it after the editor itself could not open the other note.
  await page.unroute(`**/api/notes/${b.slug}`);
  await page.route("**/api/collab/join", (route) =>
    route.request().postDataJSON().slug === b.slug ? route.fulfill({ status: 500, json: { error: "Liitumine ebaõnnestus." } }) : route.continue());
  await page.locator("#left a.side-note", { hasText: b.title }).first().click();
  await expect(page.locator("#toast")).toHaveText("Liitumine ebaõnnestus.");
  await expect(page).toHaveURL(`/m/${a.slug}`);
  await expect.poll(() => page.evaluate(() => window.shownEditor.getMarkdown())).toContain(`${a.title} veel`);
  await caretAtEnd(page, "h1");
  await page.keyboard.type("!");
  await expect(saveState(page)).toHaveText("Salvestamata…");
  await expect(saveState(page)).toHaveText("Salvestatud ✓", { timeout: 10_000 });
  await expect.poll(async () => (await api(page, `/api/notes/${a.slug}`)).data.body).toContain(`# ${a.title} veel!`);
});
