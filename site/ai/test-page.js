// Test harness for tests/visual/ai-*.spec.mjs (served only with MARKMIK_TEST_PAGES=1): a note page
// wired the way the app shell (WP-C) wires one: the editor, "editor:ready", the metadata row's
// actions and the Abiline panel.
import { emit, listMetaActions, listPanels, muyaUrl } from "/static/app.js";

await import("/static/ai/index.js"); // registers the panel and the ✦ action (same module as app.js loads)
const host = document.getElementById("editor");
const slug = host.dataset.slug;
const { openNote } = await import(`${muyaUrl()}/note-editor.js`);
const data = await (await fetch(`/api/notes/${slug}`, { headers: { Accept: "application/json" } })).json();
const note = { id: data.id, slug: data.slug, title: data.title };
const editor = await openNote(host, { slug });
window.editor = editor;
const ctx = { note };
for (const action of listMetaActions()) {
  const slot = document.createElement("span");
  document.getElementById("ai-meta-row").append(slot);
  action.mount(slot, ctx);
}
listPanels().find((p) => p.id === "assistant").mount(document.getElementById("ai-panel"), ctx);
emit("editor:ready", { note, editor });
document.body.dataset.ai = "ready";
