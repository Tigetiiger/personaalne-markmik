// Left pane: "+ Uus", search, then Täna · Kinnitatud · Hiljutised · Kaustad · Sildid. Notes are
// dragged onto a folder to move them. Rebuilt when a note is saved or its metadata changes.
import { api, emit, on, registerPage, toast } from "/static/app.js";
import { debounce, h, noteHref, plainClick, snippet, store, stored, todayIso } from "./dom.js";
import { currentNote, go, newNote } from "./note.js";

const RECENT = 10;
const DRAG_TYPE = "text/x-markmik-slug";

const openFolders = new Set(JSON.parse(stored("shell:folders", "[]")));
let openTag = null;

function mount() {
  const left = document.getElementById("left");
  const search = h("input", { type: "search", class: "side-search", placeholder: "Otsi märkmetest", "aria-label": "Otsi märkmetest" });
  const results = h("ul", { class: "side-results", hidden: true, "aria-label": "Otsingu tulemused" });
  const lists = h("div", { class: "side-lists" });
  left.append(
    h("div", { class: "side" },
      h("button", { type: "button", class: "side-new", title: "Uus märge (n, Alt+N või Ctrl+N)", onclick: newNote }, "+ Uus märge"),
      search,
      results,
      lists),
  );

  let searchGen = 0;
  const runSearch = debounce(async () => {
    const q = search.value.trim();
    const gen = ++searchGen;
    results.hidden = !q;
    lists.hidden = Boolean(q);
    if (!q) return;
    try {
      const found = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (gen !== searchGen) return;
      results.replaceChildren(
        ...(found.length
          ? found.map((r) => h("li", {}, noteLink(r.slug, r.title), h("p", { class: "side-snippet" }, snippet(r.snippet))))
          : [h("li", { class: "side-empty" }, "Midagi ei leitud.")]),
      );
    } catch (err) {
      toast(err.message, "error");
    }
  }, 200);
  search.addEventListener("input", runSearch);
  search.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    search.value = "";
    runSearch();
  });

  const refresh = debounce(() => render(lists).catch((err) => console.warn("sidebar:", err)), 150);
  on("note:saved", refresh);
  on("note:changed", refresh);
  on("note:open", () => highlight(left));
  return render(lists);
}

registerPage("notes", mount);
registerPage("today", mount);

async function render(lists) {
  const [tree, tags] = await Promise.all([api("/api/notes/tree"), api("/api/tags")]);
  const pinned = tree.filter((n) => n.pinned);
  const recent = [...tree].sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id - a.id).slice(0, RECENT);
  lists.replaceChildren(
    ...[
      h("button", { type: "button", class: "side-today linklike", onclick: openToday }, "Täna"),
      pinned.length && section("Kinnitatud", h("ul", {}, pinned.map(noteItem))),
      section("Hiljutised", recent.length ? h("ul", {}, recent.map(noteItem)) : h("p", { class: "side-empty" }, "Märkmeid veel pole.")),
      section("Kaustad", folderTree(tree), { drop: "" }),
      tags.length && section("Sildid", tagList(tags)),
    ].filter(Boolean),
  );
  highlight(lists);
}

function section(title, content, { drop } = {}) {
  const heading = h("h3", {}, title);
  if (drop !== undefined) dropTarget(heading, drop);
  return h("section", { class: "side-section" }, heading, content);
}

function noteLink(slug, title) {
  return h("a", {
    href: noteHref(slug),
    class: "side-note",
    draggable: "true",
    dataset: { slug },
    onclick: (e) => {
      if (!plainClick(e)) return;
      e.preventDefault();
      go(slug);
    },
    ondragstart: (e) => {
      e.dataTransfer.setData(DRAG_TYPE, slug);
      e.dataTransfer.effectAllowed = "move";
    },
  }, title);
}

const noteItem = (n) => h("li", {}, noteLink(n.slug, n.title));

/** Folders as nested <details>, open state remembered; notes without a folder at the end. */
function folderTree(tree) {
  const root = { children: new Map(), notes: [] };
  for (const note of tree) {
    let node = root;
    let path = "";
    for (const part of note.folder ? note.folder.split("/") : []) {
      path = path ? `${path}/${part}` : part;
      if (!node.children.has(part)) node.children.set(part, { path, children: new Map(), notes: [] });
      node = node.children.get(part);
    }
    node.notes.push(note);
  }
  const build = (node) => [
    ...[...node.children.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "et"))
      .map(([name, child]) => {
        const summary = h("summary", { dataset: { folder: child.path } }, name);
        dropTarget(summary, child.path);
        const details = h("details", { class: "side-folder", open: openFolders.has(child.path) }, summary, h("ul", {}, build(child)));
        details.addEventListener("toggle", () => {
          if (details.open) openFolders.add(child.path);
          else openFolders.delete(child.path);
          store("shell:folders", JSON.stringify([...openFolders]));
        });
        return h("li", {}, details);
      }),
    ...node.notes.map(noteItem),
  ];
  return h("ul", { class: "side-tree" }, build(root));
}

function dropTarget(el, folder) {
  el.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add("drop");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop"));
  el.addEventListener("drop", async (e) => {
    el.classList.remove("drop");
    const slug = e.dataTransfer.getData(DRAG_TYPE);
    if (!slug) return;
    e.preventDefault();
    try {
      const note = await api(`/api/notes/${encodeURIComponent(slug)}`, { method: "PATCH", body: { folder } });
      if (folder) openFolders.add(folder);
      toast(folder ? `Teisaldatud kausta ${folder}.` : "Teisaldatud kaustast välja.");
      emit("note:changed", { note });
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

function tagList(tags) {
  return h("ul", { class: "side-tags" },
    tags.map(({ tag, notes }) => {
      const open = openTag === tag;
      const button = h("button", { type: "button", class: "linklike side-tag", "aria-expanded": String(open) }, `#${tag}`, h("span", { class: "side-count" }, String(notes.length)));
      const list = h("ul", { hidden: !open }, notes.map(noteItem));
      button.addEventListener("click", () => {
        openTag = openTag === tag ? null : tag;
        for (const other of button.closest(".side-tags").querySelectorAll(".side-tag")) other.setAttribute("aria-expanded", "false");
        for (const other of button.closest(".side-tags").querySelectorAll(":scope > li > ul")) other.hidden = true;
        button.setAttribute("aria-expanded", String(openTag === tag));
        list.hidden = openTag !== tag;
      });
      return h("li", {}, button, list);
    }));
}

async function openToday() {
  try {
    const note = await api(`/api/daily/${todayIso()}`);
    await go(note.slug);
  } catch (err) {
    toast(err.message, "error");
  }
}

function highlight(scope) {
  const note = currentNote();
  for (const a of scope.querySelectorAll("a.side-note")) {
    if (note && a.dataset.slug === note.slug) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  const today = scope.querySelector(".side-today");
  if (note?.is_daily && note.daily_date === todayIso()) today?.setAttribute("aria-current", "page");
  else today?.removeAttribute("aria-current");
}
