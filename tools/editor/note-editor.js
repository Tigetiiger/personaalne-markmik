// The note editor as the rest of the app sees it (docs/CONTRACTS.md §6): one Muya per element,
// joined to the note's co-editing session. openNote on the same element again switches notes in
// place: the old session is saved and left, the editor gets the new text, no page load.
import { mountCaret, mountEditor, mountFind, mountFrontMenuKeys, mountGallery, mountLinks, mountToolbar } from "./editor.js";
import { attachCollab, http, joinCollab, prepare } from "./collab.js";
import { mountWikilink } from "./wikilink.js";

import { toast } from "/static/app.js";

const FIND = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="6.5" cy="6.5" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m10 10 4.5 4.5" stroke="currentColor" stroke-width="1.8"/></svg>';
const SCAFFOLD = `
<div class="mt-bar">
  <div class="mt-toolbar"></div>
  <div class="mt-group mt-right"><button type="button" class="mt-find-toggle" title="Otsi ja asenda (Ctrl+F, Ctrl+R)" aria-label="Otsi">${FIND}</button></div>
</div>
<div class="mt-find" hidden>
  <input type="search" placeholder="Otsi" aria-label="Otsi">
  <span class="mt-count"></span>
  <button type="button" class="mt-prev" title="Eelmine (Shift+Enter)">↑</button>
  <button type="button" class="mt-next" title="Järgmine (Enter)">↓</button>
  <label><input type="checkbox" class="mt-case"> Aa</label>
  <input type="text" class="mt-replace" placeholder="Asenda" aria-label="Asenda">
  <button type="button" class="mt-one mt-replace">Asenda</button>
  <button type="button" class="mt-all mt-replace">Asenda kõik</button>
  <button type="button" class="mt-close" title="Sulge (Esc)">✕</button>
</div>
<dialog class="mt-gallery" aria-label="Pildid">
  <div class="mt-gallery-head">
    <b>Pildid</b>
    <label class="mt-upload">Lisa uus pilt<input type="file" accept="image/*" multiple hidden></label>
    <button type="button" class="mt-close" title="Sulge (Esc)">Sulge</button>
  </div>
  <p class="mt-gallery-empty" hidden>Pilte veel pole. Lisa uus või kleebi pilt otse teksti.</p>
  <ul class="mt-gallery-grid"></ul>
</dialog>
<div class="mt-doc"><div class="mt-cursors" aria-hidden="true"></div><div class="mt-editor"></div></div>`;

// Keys a read-only viewer may still use: moving, selecting, copying.
const VIEW_KEYS = /^(Arrow\w+|Home|End|PageUp|PageDown|Tab|Escape|Shift|Control|Alt|Meta)$/;
const RETRY_MS = 3000;
const ERROR_TOAST_MS = 30_000;

const instances = new WeakMap(); // element -> editor state

// Muya's stylesheet also styles <body> (font, size): it goes before our own stylesheets, so
// ours win, and only once an editor is opened.
let css = null;
function loadCss() {
  css ||= new Promise((resolve) => {
    const link = Object.assign(document.createElement("link"), { rel: "stylesheet", href: new URL("core.css", import.meta.url).href });
    link.onload = link.onerror = resolve;
    document.head.insertBefore(link, document.head.querySelector('link[rel="stylesheet"]'));
  });
  return css;
}

function mount(inst, markdown) {
  const { el } = inst;
  el.classList.add("mt");
  el.innerHTML = SCAFFOLD;
  const q = (selector) => el.querySelector(selector);
  const signal = inst.abort.signal;
  const muya = mountEditor(q(".mt-editor"), { markdown });
  prepare(muya);
  const gallery = mountGallery(muya, q(".mt-gallery"));
  mountFrontMenuKeys(muya, signal);
  mountCaret(muya);
  mountToolbar(muya, q(".mt-toolbar"), { onImage: gallery.open, signal });
  const find = mountFind(muya, q(".mt-find"));
  mountLinks(muya, (slug) => inst.onOpenNote(slug));
  mountWikilink(muya, { signal });
  q(".mt-find-toggle").addEventListener("click", () => find.open(false));
  el.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "s") inst.collab?.save().catch((err) => toast(err.message, "error"));
      else if (key === "f" && !e.shiftKey) find.open(false);
      else if (key === "r" && !e.shiftKey) find.open(true);
      else return;
      e.preventDefault();
    },
    { signal },
  );
  // Read-only (a view share): no typing, pasting or dropping; moving and copying still work.
  const refuse = (e) => {
    if (!inst.readOnly) return;
    e.preventDefault();
    e.stopImmediatePropagation();
  };
  for (const type of ["beforeinput", "paste", "cut", "drop"]) muya.domNode.addEventListener(type, refuse, true);
  muya.domNode.addEventListener("keydown", (e) => {
    if (!VIEW_KEYS.test(e.key) && !((e.ctrlKey || e.metaKey) && /^[ac]$/i.test(e.key))) refuse(e);
  }, true);
  inst.muya = muya;
}

function makeHandle(inst) {
  const collab = () => inst.collab;
  return {
    getMarkdown: () => (collab() ? collab().getMarkdown() : inst.muya.getMarkdown()),
    selection: () => collab()?.selection() ?? null,
    applyBlockEdit: (path, text) => Boolean(collab()?.applyBlockEdit(path, text)),
    replaceMarkdown: (markdown) => collab()?.replaceMarkdown(markdown),
    toggleTask: (path, checked) => Boolean(collab()?.toggleTask(path, checked)),
    focusTitle() {
      const page = inst.muya.editor.scrollPage;
      let block = page.firstChild;
      while (block && !(block.blockName === "atx-heading" && block.meta.level === 1)) block = block.next;
      const content = (block || page.firstChild)?.firstContentInDescendant();
      if (!content) return;
      content.setCursor(content.text.length, content.text.length, true);
      inst.muya.domNode.focus();
    },
    detach() {
      inst.gen++;
      const old = inst.collab;
      inst.collab = null;
      instances.delete(inst.el);
      inst.abort.abort();
      const muya = inst.muya;
      const done = () => {
        muya.destroy();
        inst.el.replaceChildren();
        inst.el.classList.remove("mt", "mt-readonly");
      };
      if (!old) return done();
      old.save().catch(() => {}).finally(() => {
        old.leave();
        done();
      });
    },
  };
}

/**
 * Open a note in `el` (docs/CONTRACTS.md §6). Again on the same element: switch notes.
 * onOpenNote(slug): the writer followed a link to another note.
 * onState({ saved, participants, seq }): for a status line.
 */
export async function openNote(el, { slug, readOnly = false, onOpenNote = () => {}, onState = () => {} }) {
  let inst = instances.get(el);
  if (!inst) {
    inst = { el, gen: 0, muya: null, collab: null, closing: Promise.resolve(), abort: new AbortController(), lastError: 0 };
    inst.handle = makeHandle(inst);
    instances.set(el, inst);
  }
  const gen = ++inst.gen;
  const prev = { slug: inst.slug, readOnly: inst.readOnly, onOpenNote: inst.onOpenNote, onState: inst.onState };
  Object.assign(inst, { slug, readOnly, onOpenNote, onState });

  // The note open so far is saved and left before the editor takes the new text; a quicker
  // second switch waits for the first one's save too.
  const old = inst.collab;
  inst.collab = null;
  if (old) {
    inst.closing = old.save().catch((err) => toast(`Eelmine märge jäi salvestamata: ${err.message}`, "error")).finally(() => old.leave());
  }
  let joined;
  try {
    [joined] = await Promise.all([joinCollab(slug), loadCss(), inst.closing]);
  } catch (err) {
    // That note could not be opened: the editor goes back to the one it showed.
    if (gen === inst.gen && inst.muya && prev.slug) {
      Object.assign(inst, prev);
      reopen(inst, gen);
    }
    throw err;
  }
  if (gen !== inst.gen) {
    // Another note was asked for meanwhile: this session is not needed.
    http.beacon(`/api/collab/${joined.session}/leave`, { client: joined.client });
    return inst.handle;
  }

  if (!inst.muya) mount(inst, joined.base);
  else {
    prepare(inst.muya)(); // anything pending belongs to the note just left
    inst.muya.setContent(joined.base);
  }
  el.classList.toggle("mt-readonly", readOnly);

  inst.collab = attachCollab(inst.muya, joined, {
    cursorLayer: el.querySelector(".mt-cursors"),
    readOnly,
    onState: (state) => inst.onState(state),
    onReset: () => reopen(inst, gen),
    onError: (err) => {
      console.warn("collab:", err);
      if (Date.now() - inst.lastError < ERROR_TOAST_MS) return;
      inst.lastError = Date.now();
      toast(`Ühendus katkes (${err.message}). Proovin uuesti…`, "error");
    },
  });
  return inst.handle;
}

/** The session ended under us: join the same note again with its current text. */
async function reopen(inst, gen) {
  if (gen !== inst.gen) return;
  try {
    await openNote(inst.el, { slug: inst.slug, readOnly: inst.readOnly, onOpenNote: inst.onOpenNote, onState: inst.onState });
  } catch (err) {
    console.warn("collab: rejoin failed", err);
    setTimeout(() => reopen(inst, inst.gen), RETRY_MS);
  }
}
