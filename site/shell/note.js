// The note page: one editor for the whole visit, the metadata row above it, and switching notes
// in place (sidebar, links, palette, back/forward) through history.pushState — no page loads.
import { api, emit, listMetaActions, muyaUrl, on, registerPage, toast } from "/static/app.js";
import { h, noteHref } from "./dom.js";
import { closeOverlays, paneButton } from "./layout.js";
import { clearPanels, showPanels } from "./panels.js";

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
    actions: h("span", { class: "meta-actions" }),
    people: h("span", { class: "meta-people", hidden: true }),
    save: h("span", { class: "meta-save", role: "status" }),
    words: h("span", { class: "meta-words" }),
    panel: paneButton("right", { class: "meta-panel" }, "Paneel"),
  };
  page = {
    root,
    metaEl: root.querySelector("#note-meta"),
    editorEl: root.querySelector("#editor"),
    empty: root.querySelector("#note-empty"),
    meta,
    note: null,
    handle: null,
    gen: 0,
    dirty: false,
    actionCleanups: [],
  };
  page.metaEl.append(meta.crumb, meta.tags, meta.pin, meta.actions, meta.people, meta.save, meta.words, meta.panel);

  meta.crumb.addEventListener("click", () =>
    ask(meta.crumb, { value: page.note.folder, label: "Kaust, nt kool/matemaatika", submit: (folder) => folder !== page.note.folder && patch({ folder }) }),
  );
  meta.addTag.addEventListener("click", () =>
    ask(meta.addTag, { label: "Uus silt", submit: (tag) => (tag = tag.replace(/^#+/, "").trim()) && patch({ tags: [...page.note.tags, tag] }) }),
  );
  meta.pin.addEventListener("click", () => patch({ pinned: !page.note.pinned }));

  on("note:changed", ({ note }) => {
    // Someone else changed the open note's metadata (the sidebar moved it to a folder, ...).
    if (!page.note || note?.slug !== page.note.slug || note === page.note) return;
    page.note = { ...page.note, ...note };
    renderMeta();
  });
  window.addEventListener("popstate", () => {
    const slug = slugFromPath();
    if (slug) open(slug);
    else showEmpty("Vali vasakult märge või loo uus.");
  });

  const slug = slugFromPath() || root.dataset.slug;
  if (slug) await open(slug);
  else (window.requestIdleCallback || setTimeout)(() => loadEditor().catch(() => {}));
});

async function open(slug) {
  const gen = ++page.gen;
  delete page.root.dataset.open;
  let note;
  try {
    note = await api(`/api/notes/${encodeURIComponent(slug)}`);
  } catch (err) {
    if (gen !== page.gen) return;
    if (err.status === 404) showEmpty(null);
    else toast(err.message, "error");
    return;
  }
  if (gen !== page.gen) return;
  page.editorEl.hidden = false;
  page.empty.hidden = true;
  let handle;
  try {
    const { openNote } = await loadEditor();
    handle = await openNote(page.editorEl, { slug, onOpenNote: (s) => go(s), onState: (s) => onState(gen, s) });
  } catch (err) {
    if (gen === page.gen) toast(err.message, "error");
    return;
  }
  if (gen !== page.gen) return;
  Object.assign(page, { note, handle, dirty: false });
  page.metaEl.hidden = false;
  page.meta.save.textContent = "Salvestatud ✓";
  page.meta.save.classList.remove("unsaved");
  document.title = `${note.title} · Märkmik`;
  renderMeta();
  const ctx = { note, editor: handle };
  mountActions(ctx);
  countWords();
  page.root.dataset.open = slug;
  showPanels(ctx); // first: the last note's panels clean up before anyone hears of this one
  emit("note:open", { note });
  emit("editor:ready", ctx);
  if (isBlank(note)) handle.focusTitle();
}

/** No note in the editor: `message`, or "Märget ei leitud" when null. */
function showEmpty(message) {
  page.gen++;
  if (page.handle) {
    page.handle.detach();
    // detach() empties its element once the last save is through; the next note gets a new one
    // so that late emptying cannot take the next editor with it.
    const fresh = h("div", { class: "note-editor", id: "editor" });
    page.editorEl.replaceWith(fresh);
    page.editorEl = fresh;
  }
  Object.assign(page, { note: null, handle: null });
  page.editorEl.hidden = true;
  page.metaEl.hidden = true;
  page.empty.replaceChildren(...(message ? [h("p", {}, message)] : [h("h1", {}, "Märget ei leitud"), h("p", {}, "Seda märget pole või see on kustutatud.")]));
  page.empty.hidden = false;
  page.root.dataset.open = message ? "" : "puudub";
  document.title = message ? "Märkmik" : "Märget ei leitud · Märkmik";
  clearPanels();
}

function onState(gen, { saved, participants }) {
  if (gen !== page.gen) return;
  const { save, people } = page.meta;
  save.textContent = saved ? "Salvestatud ✓" : "Salvestamata…";
  save.classList.toggle("unsaved", !saved);
  people.hidden = participants.length < 2;
  people.textContent = participants.length < 2 ? "" : `Koos: ${participants.join(", ")}`;
  countWords();
  if (!saved) page.dirty = true;
  else if (page.dirty) {
    page.dirty = false;
    afterSave(gen);
  }
}

// The server derives the title (and links, tasks) on save: fetch the note to follow it.
async function afterSave(gen) {
  try {
    const note = await api(`/api/notes/${encodeURIComponent(page.note.slug)}`);
    if (gen !== page.gen) return;
    page.note = note;
    document.title = `${note.title} · Märkmik`;
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
}

function mountActions(ctx) {
  for (const cleanup of page.actionCleanups.splice(0)) cleanup();
  page.meta.actions.replaceChildren();
  for (const action of listMetaActions()) {
    const el = h("span", { class: "meta-action", dataset: { action: action.id } });
    page.meta.actions.append(el);
    Promise.resolve()
      .then(() => action.mount(el, ctx))
      .then((cleanup) => typeof cleanup === "function" && page.actionCleanups.push(cleanup))
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
