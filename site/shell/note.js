// The note page: one editor for the whole visit, the crumb row (folder; Jaga, Viited, ✦ AI) and the
// metadata row above it, and switching notes in place (sidebar, links, palette, back/forward)
// through history.pushState — no page loads.
import { api, emit, listMetaActions, muyaUrl, on, registerMetaAction, registerPage, toast } from "/static/app.js";
import { h, noteHref } from "./dom.js";
import { closeOverlays, paneButton } from "./layout.js";
import { clearPanels, selectedTab, showPanels, toggleOthers } from "./panels.js";

let page = null; // the note page's state once it has mounted
let editorModule = null;
const loadEditor = () => (editorModule ||= import(`${muyaUrl()}/note-editor.js`));

export const currentNote = () => page?.note || null;

/** Open a note: in place on the note page, by loading /m/<slug> anywhere else. */
export function go(slug) {
  closeOverlays();
  if (!page) {
    location.href = noteHref(slug);
    return Promise.resolve();
  }
  if (location.pathname === noteHref(slug) && page.note?.slug === slug) return Promise.resolve();
  history.pushState({ slug }, "", noteHref(slug));
  return open(slug);
}

/** A new empty note, opened with the caret in its title. */
export async function newNote() {
  try {
    const note = await api("/api/notes", { method: "POST", body: {} });
    emit("note:changed", { note });
    await go(note.slug);
  } catch (err) {
    toast(err.message, "error");
  }
}

// Viited opens the right panel on its other tabs (✦ AI, the AI package's, on Abiline). One button,
// moved into each note's crumb row.
let viited = null;
registerMetaAction("viited", {
  order: 30,
  mount(el) {
    viited ||= paneButton("right", { class: "meta-panel", title: "Viited, sisukord, seotud (Ctrl+Shift+\\)", onclick: toggleOthers, expanded: () => selectedTab() !== "assistant" }, "Viited");
    el.append(viited);
  },
});

const slugFromPath = () => decodeURIComponent(location.pathname.match(/^\/m\/([^/]+)\/?$/)?.[1] || "");
// A note that is nothing but an empty "# " line is new: the caret goes into its title.
const isBlank = (note) => /^#\s*$/.test(note.body.trim());

registerPage("notes", async () => {
  const root = document.getElementById("note");
  const meta = {
    crumb: h("button", { type: "button", class: "meta-crumb linklike", title: "Muuda kausta" }),
    tags: h("span", { class: "meta-tags" }),
    addTag: h("button", { type: "button", class: "chip chip-add linklike" }, "+ silt"),
    pin: h("button", { type: "button", class: "meta-pin linklike", "aria-pressed": "false" }),
    hide: h("button", { type: "button", class: "meta-hide linklike", "aria-pressed": "false" }),
    actions: h("span", { class: "meta-actions" }),
    people: h("span", { class: "meta-people", hidden: true }),
    save: h("span", { class: "meta-save", role: "status" }),
    words: h("span", { class: "meta-words" }),
  };
  page = {
    root,
    metaEl: root.querySelector("#note-meta"),
    editorEl: root.querySelector("#editor"),
    empty: root.querySelector("#note-empty"),
    meta,
    note: null,
    handle: null,
    gen: 0, // the latest open asked for
    shown: 0, // the open whose note the editor holds; state reports and plugin handles follow it
    dirty: false,
    wantEditor: false,
    dropped: new WeakSet(),
    actionCleanups: [],
  };
  page.metaEl.append(
    h("div", { class: "crumb note-crumb" }, meta.crumb, h("span", { class: "crumb-actions" }, meta.actions)),
    h("div", { class: "note-meta-row" }, meta.tags, meta.pin, meta.hide, meta.words, meta.people, meta.save),
  );

  meta.crumb.addEventListener("click", () =>
    ask(meta.crumb, { value: page.note.folder, label: "Kaust, nt kool/matemaatika", submit: (folder) => folder !== page.note.folder && patch({ folder }) }),
  );
  meta.addTag.addEventListener("click", () =>
    ask(meta.addTag, { label: "Uus silt", submit: (tag) => (tag = tag.replace(/^#+/, "").trim()) && patch({ tags: [...page.note.tags, tag] }) }),
  );
  meta.pin.addEventListener("click", () => patch({ pinned: !page.note.pinned }));
  meta.hide.addEventListener("click", () => patch({ hidden: !page.note.hidden }));

  on("note:changed", ({ note }) => {
    // Someone else changed the open note's metadata (the sidebar moved it to a folder, ...).
    if (!page.note || note?.slug !== page.note.slug || note === page.note) return;
    page.note = { ...page.note, ...note };
    renderMeta();
  });
  window.addEventListener("popstate", () => {
    const slug = slugFromPath();
    if (slug) open(slug);
    else showHome();
  });

  const slug = slugFromPath() || root.dataset.slug;
  if (slug) await open(slug);
  else {
    showHome();
    (window.requestIdleCallback || setTimeout)(() => loadEditor().catch(() => {}));
  }
});

async function open(slug) {
  const gen = ++page.gen;
  page.wantEditor = true;
  delete page.root.dataset.open;
  let note;
  try {
    note = await api(`/api/notes/${encodeURIComponent(slug)}`);
  } catch (err) {
    if (gen !== page.gen) return;
    if (err.status === 404) showEmpty(null);
    else failed(err);
    return;
  }
  if (gen !== page.gen) return;
  page.editorEl.hidden = false;
  page.empty.hidden = true;
  leaveHome();
  // Unsaved text of the note being left is saved by the switch; say so once that is through.
  // A save report arriving meanwhile is not about the next note.
  const leaving = page.dirty ? page.note?.slug : null;
  page.dirty = false;
  let handle;
  try {
    const { openNote } = await loadEditor();
    handle = await openNote(page.editorEl, { slug, onOpenNote: (s) => go(s), onState: (s) => onState(gen, s) });
  } catch (err) {
    if (gen === page.gen) failed(err);
    return;
  } finally {
    if (leaving) afterSave(leaving);
  }
  if (gen !== page.gen) return dropIfUnwanted(handle);
  Object.assign(page, { note, handle, shown: gen });
  page.metaEl.hidden = false;
  page.meta.save.textContent = "Salvestatud ✓";
  page.meta.save.classList.remove("unsaved");
  document.title = `${note.title} · Märkmik`;
  renderMeta();
  const ctx = { note, editor: scoped(handle, gen) };
  mountActions(ctx, gen);
  countWords();
  page.root.dataset.open = slug;
  showPanels(ctx); // first: the last note's panels clean up before anyone hears of this one
  emit("note:open", { note });
  emit("editor:ready", ctx);
  if (isBlank(note)) handle.focusTitle();
}

/** The note could not be opened: the address goes back to the note still shown (its handle and
 *  save state stay live: they follow page.shown, not page.gen). */
function failed(err) {
  toast(err.message, "error");
  if (!page.note) return;
  history.replaceState({ slug: page.note.slug }, "", noteHref(page.note.slug));
  page.root.dataset.open = page.note.slug;
}

/** The editor handle for panels and meta actions of one opening: once another note is open, it
 *  does nothing (an edit meant for the note left must not land in the next one). */
function scoped(handle, gen) {
  const stale = { applyBlockEdit: false, toggleTask: false };
  const wrapped = {};
  for (const [name, value] of Object.entries(handle)) {
    // Properties such as the `muya` getter pass through, read live: the instance can be replaced.
    if (typeof value === "function") wrapped[name] = (...args) => (gen === page.shown ? value(...args) : (stale[name] ?? null));
    else Object.defineProperty(wrapped, name, { get: () => handle[name], enumerable: true });
  }
  return wrapped;
}

/** No note in the editor: `message`, or "Märget ei leitud" when null. */
// "/" with no note: the home chat (site/ai/index.js, docs/KODU-PLAN.md) takes the empty state's
// place; without the AI package the hint stays.
function showHome() {
  showEmpty("Vali vasakult märge või loo uus.");
  page.empty.classList.add("home");
  page.root.dataset.home = "";
  emit("home:show", { el: page.empty });
}

function leaveHome() {
  if (!("home" in page.root.dataset)) return;
  delete page.root.dataset.home;
  page.empty.classList.remove("home");
  emit("home:hide", {});
}

function showEmpty(message) {
  leaveHome();
  page.gen++;
  page.shown = page.gen; // no opening's: every handle given out so far goes dead
  page.wantEditor = false;
  dropIfUnwanted(page.handle);
  Object.assign(page, { note: null, handle: null, dirty: false });
  clearActions();
  page.editorEl.hidden = true;
  page.metaEl.hidden = true;
  page.empty.replaceChildren(...(message ? [h("p", {}, message)] : [h("h1", {}, "Märget ei leitud"), h("p", {}, "Seda märget pole või see on kustutatud.")]));
  page.empty.hidden = false;
  page.root.dataset.open = message ? "" : "puudub";
  document.title = message ? "Märkmik" : "Märget ei leitud · Märkmik";
  clearPanels();
}

/** Close the editor (save, leave the session) unless a note is wanted in it after all: an open
 *  that finishes after the page moved on to "no note" must not stay joined, hidden and busy. */
function dropIfUnwanted(handle) {
  if (!handle || page.wantEditor || page.dropped.has(handle)) return;
  page.dropped.add(handle);
  handle.detach();
  // detach() empties its element once the last save is through; the next note gets a new one
  // so that late emptying cannot take the next editor with it.
  const fresh = h("div", { class: "note-editor", id: "editor", hidden: true });
  page.editorEl.replaceWith(fresh);
  page.editorEl = fresh;
}

function onState(gen, { saved, participants }) {
  if (gen !== page.shown) return;
  const { save, people } = page.meta;
  save.textContent = saved ? "Salvestatud ✓" : "Salvestamata…";
  save.classList.toggle("unsaved", !saved);
  people.hidden = participants.length < 2;
  people.textContent = participants.length < 2 ? "" : `Koos: ${participants.join(", ")}`;
  countWords();
  if (!saved) page.dirty = true;
  else if (page.dirty && page.note) {
    page.dirty = false;
    afterSave(page.note.slug);
  }
}

// The server derives the title (and links, tasks) on save: fetch the note to follow it. Only the
// note still open is taken over; a note just left is only announced.
async function afterSave(slug) {
  try {
    const note = await api(`/api/notes/${encodeURIComponent(slug)}`);
    if (page.note?.slug === slug) {
      page.note = note;
      document.title = `${note.title} · Märkmik`;
    }
    emit("note:saved", { note, rev: note.rev });
  } catch (err) {
    console.warn("after save:", err);
  }
}

// Not a debounce: onState comes on every sync while others are in the note (4 a second), which
// would put a debounced count off for good.
let wordsTimer = null;
const countWords = () => (wordsTimer ||= setTimeout(showWords, 300));

function showWords() {
  wordsTimer = null;
  if (!page.handle) return;
  const md = page.handle.getMarkdown();
  const words = md.replace(/\]\([^)]*\)/g, "]").replace(/^\s*[-*+]\s+\[[ xX]\]/gm, "").match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu)?.length || 0;
  page.meta.words.textContent = `${words} sõna`;
  const title = md.match(/^ {0,3}# +(.*?)[ #]*$/m)?.[1];
  if (title) document.title = `${title} · Märkmik`;
}

function renderMeta() {
  const { note, meta } = page;
  const parts = note.folder ? note.folder.split("/") : ["Kaustata"];
  meta.crumb.replaceChildren(...parts.flatMap((part, i) => (i ? [h("span", { class: "sep", "aria-hidden": "true" }, "/"), part] : [part])));
  meta.crumb.classList.toggle("none", !note.folder);
  meta.tags.replaceChildren(
    ...note.tags.map((tag) =>
      h("span", { class: "chip" }, `#${tag}`,
        h("button", { type: "button", class: "chip-x", "aria-label": `Eemalda silt ${tag}`, title: "Eemalda", onclick: () => patch({ tags: note.tags.filter((t) => t !== tag) }) }, "×")),
    ),
    meta.addTag,
  );
  meta.pin.setAttribute("aria-pressed", String(note.pinned));
  meta.pin.textContent = note.pinned ? "★ Kinnitatud" : "☆ Kinnita";
  meta.hide.setAttribute("aria-pressed", String(Boolean(note.hidden)));
  meta.hide.textContent = note.hidden ? "Peidetud · näita nimekirjas" : "Peida nimekirjast";
  meta.hide.title = note.hidden ? "Märge on külgriba nimekirjadest peidetud" : "Peida see märge külgriba nimekirjadest";
}

function clearActions() {
  for (const cleanup of page.actionCleanups.splice(0)) cleanup();
  page.meta.actions.replaceChildren();
}

function mountActions(ctx, gen) {
  clearActions();
  for (const action of listMetaActions()) {
    const el = h("span", { class: "meta-action", dataset: { action: action.id } });
    page.meta.actions.append(el);
    Promise.resolve()
      .then(() => action.mount(el, ctx))
      .then((cleanup) => {
        if (typeof cleanup !== "function") return;
        if (gen === page.shown) page.actionCleanups.push(cleanup);
        else cleanup(); // the note changed while it mounted
      })
      .catch((err) => console.error(`meta action ${action.id} failed`, err));
  }
}

async function patch(fields) {
  const slug = page.note.slug;
  try {
    const note = await api(`/api/notes/${encodeURIComponent(slug)}`, { method: "PATCH", body: fields });
    if (page.note?.slug === slug) {
      page.note = note;
      renderMeta();
    }
    emit("note:changed", { note });
  } catch (err) {
    toast(err.message, "error");
  }
}

/** Swap `anchor` for a one-line input; Enter submits, Esc or leaving cancels. */
function ask(anchor, { value = "", label, submit }) {
  const input = h("input", { type: "text", class: "meta-input", value, "aria-label": label, placeholder: label });
  let done = false;
  const finish = (ok) => {
    if (done) return;
    done = true;
    input.replaceWith(anchor);
    if (ok) submit(input.value.trim());
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "Escape") {
      e.preventDefault();
      finish(e.key === "Enter");
    }
  });
  input.addEventListener("blur", () => finish(false));
  anchor.replaceWith(input);
  input.focus();
  input.select();
}
