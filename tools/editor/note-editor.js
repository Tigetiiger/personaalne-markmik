// The note editor as the rest of the app sees it (docs/CONTRACTS.md §6): one Muya per element,
// joined to the note's co-editing session. openNote on the same element again switches notes in
// place: the old session is saved and left, the editor gets the new text, no page load.
import { mountCaret, mountEditor, mountFind, mountFrontMenuKeys, mountGallery, mountLinks, mountToolbar } from "./editor.js";
import { attachCollab, http, joinCollab, keepCopy, prepare } from "./collab.js";
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
const RETRY_MS = 3000; // first wait before joining again after a failed rejoin; doubles each time
const MAX_RETRY_MS = 60_000;
const STABLE_MS = 30_000; // a session this old has worked: the next rejoin need not wait
const ERROR_TOAST_MS = 30_000;

const instances = new WeakMap(); // element -> editor state
// element -> a detach still saving and tearing down; the next openNote on it waits for that.
const closings = new WeakMap();

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

function mount(inst, markdown, guest) {
  const { el } = inst;
  el.classList.add("mt");
  el.innerHTML = SCAFFOLD;
  const q = (selector) => el.querySelector(selector);
  const signal = inst.abort.signal;
  const muya = mountEditor(q(".mt-editor"), { markdown });
  prepare(muya);
  // A guest may upload into their note, but not browse the owner's pictures or notes.
  const gallery = mountGallery(muya, q(".mt-gallery"), { listing: !guest });
  mountFrontMenuKeys(muya, signal);
  mountCaret(muya);
  mountToolbar(muya, q(".mt-toolbar"), { onImage: gallery.open, signal });
  const find = mountFind(muya, q(".mt-find"));
  mountLinks(muya, (slug) => inst.onOpenNote(slug));
  if (!guest) mountWikilink(muya, { signal });
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
    /** The Muya instance, for packages that extend the editor (WP-F's inline AI). */
    get muya() {
      return inst.muya;
    },
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
        for (const plugin of Object.values(muya._uiPlugins)) plugin.destroy?.(); // their popups live in <body>
        muya.destroy();
        inst.el.replaceChildren();
        inst.el.classList.remove("mt", "mt-readonly");
      };
      // No session any more but unsent changes: they stay in this browser, offered next time.
      if (old?.stopped && old.pending) old.keepCopy();
      const closing = (old ? old.save().catch(() => {}).finally(() => old.leave()) : Promise.resolve()).then(done);
      closings.set(inst.el, closing);
    },
  };
}

/**
 * Open a note in `el` (docs/CONTRACTS.md §6). Again on the same element: switch notes.
 * onOpenNote(slug): the writer followed a link to another note.
 * onState({ saved, participants, seq }): for a status line.
 */
export async function openNote(el, { slug, readOnly = false, onOpenNote = () => {}, onState = () => {} }) {
  const closing = closings.get(el);
  if (closing) {
    await closing;
    if (closings.get(el) === closing) closings.delete(el);
  }
  let inst = instances.get(el);
  if (!inst) {
    inst = { el, gen: 0, muya: null, collab: null, abort: new AbortController(), lastError: 0 };
    inst.handle = makeHandle(inst);
    instances.set(el, inst);
  }
  clearTimeout(inst.retryTimer);
  const gen = ++inst.gen;
  const prev = { slug: inst.slug, readOnly: inst.readOnly, onOpenNote: inst.onOpenNote, onState: inst.onState };
  Object.assign(inst, { slug, readOnly, onOpenNote, onState });

  // The note open so far stays connected (what is typed meanwhile still goes to its session)
  // until the new one is joined; then it is saved and left. If the new one cannot be opened,
  // the editor simply stays where it was. When its session ended under it (the same note
  // again), it goes on collecting what is typed, carried over into the new session.
  const old = inst.collab;
  const rejoin = Boolean(old && old.stopped && prev.slug === slug);
  const saving = old && !rejoin ? old.save().catch((err) => toast(`Eelmine märge jäi salvestamata: ${err.message}`, "error")) : null;
  let joined;
  try {
    [joined] = await Promise.all([joinCollab(slug), loadCss(), saving]);
  } catch (err) {
    if (gen === inst.gen) Object.assign(inst, prev);
    throw err;
  }
  let rescued = null;
  if (rejoin) rescued = old.rescue();
  else if (old && gen === inst.gen) await old.save().catch(() => {}); // typed during the join
  if (gen !== inst.gen) {
    // Another note was asked for meanwhile: this session is not needed.
    http.beacon(`/api/collab/${joined.session}/leave`, { client: joined.client });
    return inst.handle;
  }
  old?.leave();
  inst.collab = null;
  if (!inst.muya) mount(inst, joined.base, joined.guest);
  else {
    prepare(inst.muya)(); // anything pending belongs to the note just left
    inst.muya.setContent(joined.base);
  }
  el.classList.toggle("mt-readonly", readOnly);

  inst.collab = attachCollab(inst.muya, joined, {
    cursorLayer: el.querySelector(".mt-cursors"),
    readOnly,
    onState: (state) => inst.onState(state),
    onReset: () => gen === inst.gen && reopen(inst),
    onError: (err) => {
      console.warn("collab:", err);
      if (err.status === 413) {
        toast("Muudatus oli teistega jagamiseks liiga suur. Tekst on selles brauseris alles.", "error");
        return;
      }
      if (Date.now() - inst.lastError < ERROR_TOAST_MS) return;
      inst.lastError = Date.now();
      toast(`Ühendus katkes (${err.message}). Proovin uuesti…`, "error");
    },
  });
  inst.noteId = joined.note;
  const collab = inst.collab;
  clearTimeout(inst.stableTimer);
  inst.stableTimer = setTimeout(() => {
    if (inst.collab === collab && !collab.stopped) inst.retryDelay = 0;
  }, STABLE_MS);
  if (rescued) carryOver(inst, rescued);
  if (!readOnly) offerCopy(inst);
  return inst.handle;
}

/** Text an earlier tab of this browser could not save (keepCopy): restore it or let it go. */
function offerCopy(inst) {
  inst.el.querySelector(".mt-copy")?.remove();
  const key = `markmik:koopia:${inst.noteId}`;
  let copy = null;
  try {
    copy = JSON.parse(localStorage.getItem(key) || "null");
  } catch {}
  const forget = () => {
    try {
      localStorage.removeItem(key);
    } catch {}
  };
  if (!copy || typeof copy.text !== "string") return;
  if (copy.text === inst.handle.getMarkdown()) return forget();
  const bar = document.createElement("div");
  bar.className = "mt-copy";
  bar.setAttribute("role", "status");
  const when = new Date(copy.at).toLocaleString("et-EE", { dateStyle: "short", timeStyle: "short" });
  const action = (label, run) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", () => {
      run();
      forget();
      bar.remove();
    });
    return b;
  };
  bar.append(`Selles brauseris on selle märkme salvestamata tekst (${when}). `, action("Taasta", () => inst.handle.replaceMarkdown(copy.text)), " ", action("Loobu", () => {}));
  inst.el.querySelector(".mt-doc").before(bar);
}

/** What was typed but never reached the ended session goes into the new one. */
function carryOver(inst, rescued) {
  try {
    inst.collab.carry(rescued);
    toast("Sinu viimased muudatused taastati.");
  } catch (err) {
    console.warn("collab: could not carry over the last changes", err);
    keepCopy(inst.noteId, rescued.text);
    toast("Viimaseid muudatusi ei õnnestunud taastada; nende koopia on selles brauseris alles.", "error");
    offerCopy(inst);
  }
}

/** The session ended under us: join the same note again with its current text. One timer,
 *  waiting longer after every failure, and after every reset of a session that did not last
 *  (a change the server refuses would otherwise reset it in a loop). */
function reopen(inst) {
  clearTimeout(inst.retryTimer);
  const wait = inst.retryDelay || 0;
  inst.retryDelay = Math.min((inst.retryDelay || RETRY_MS / 2) * 2, MAX_RETRY_MS);
  inst.retryTimer = setTimeout(() => {
    const gen = inst.gen;
    openNote(inst.el, { slug: inst.slug, readOnly: inst.readOnly, onOpenNote: inst.onOpenNote, onState: inst.onState }).catch((err) => {
      console.warn("collab: rejoin failed", err);
      if (inst.gen !== gen + 1) return; // something else was opened meanwhile
      if (Date.now() - inst.lastError >= ERROR_TOAST_MS) {
        inst.lastError = Date.now();
        toast(`Märget ei saanud uuesti avada (${err.message}). Muudatused on alles; proovin uuesti…`, "error");
      }
      reopen(inst);
    });
  }, wait);
}
