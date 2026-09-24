// Test harness for tests/visual/editor-*.spec.mjs: one editor opened the way the app does it.
// Served only with MARKMIK_TEST_PAGES=1 (app/views/collab.py).
const root = document.getElementById("test-root");
const muyaUrl = document.querySelector('meta[name="muya"]').content;
const { openNote } = await import(`${muyaUrl}/note-editor.js`);

const host = document.createElement("div");
host.id = "editor";
const status = document.createElement("p");
status.id = "status";
root.append(host, status);

window.opened = []; // onOpenNote calls
async function open(slug, options = {}) {
  window.editor = await openNote(host, {
    slug,
    onOpenNote: (s) => window.opened.push(s),
    onState: (s) => {
      window.lastState = s;
      status.textContent = s.saved ? "Salvestatud" : "Salvestamata";
      status.dataset.participants = s.participants.join(",");
      status.dataset.seq = s.seq;
    },
    ...options,
  });
  document.body.dataset.slug = slug;
  return window.editor;
}
window.openNoteInHarness = open;

await open(root.dataset.slug);
document.body.dataset.editor = "ready";
