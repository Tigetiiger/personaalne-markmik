// Right panel: one tab per registered panel (registerPanel), mounted the first time it is shown
// for the open note, the last tab chosen remembered. A note switch unmounts them all. The crumb
// row opens it on a tab: ✦ AI (the AI package's, through "panel:toggle") on Abiline, Viited on the
// tab last chosen among the others.
import { listPanels, on } from "/static/app.js";
import { h, store, stored } from "./dom.js";
import { isOpen, syncButtons, toggle } from "./layout.js";

const AI_TAB = "assistant";

const tabs = document.getElementById("panel-tabs");
const body = document.getElementById("panel-body");
let current = null; // {ctx, mounted: Map(id -> {el, cleanup})}
let selected = null;
let lastOther = stored("shell:panel") !== AI_TAB && stored("shell:panel") || "viited";

/** The tab shown in the right panel, or null. */
export const selectedTab = () => (current ? selected : null);

/** Show the right panel on tab `id`, or close it when it already shows that tab. */
export function togglePanel(id) {
  if (!current) return;
  if (isOpen("right") && selected === id) return toggle("right", false);
  toggle("right", true);
  select(id);
}

/** Viited: the right panel on the last tab chosen other than Abiline. */
export const toggleOthers = () => togglePanel(selected && selected !== AI_TAB && isOpen("right") ? selected : lastOther);

on("panel:toggle", ({ id }) => togglePanel(id));

/** Show the panels for ctx = {note, editor}. */
export function showPanels(ctx) {
  clearPanels();
  const list = listPanels();
  current = { ctx, mounted: new Map() };
  tabs.replaceChildren(...list.map((p) => h("button", { type: "button", role: "tab", "aria-selected": "false", dataset: { panel: p.id }, onclick: () => select(p.id) }, p.title)));
  const last = stored("shell:panel");
  const first = list.some((p) => p.id === last) ? last : list[0]?.id;
  if (first) select(first, false);
}

export function clearPanels() {
  if (current) for (const m of current.mounted.values()) m.cleanup?.();
  current = null;
  tabs?.replaceChildren();
  body?.replaceChildren();
}

function select(id, remember = true) {
  const panel = listPanels().find((p) => p.id === id);
  if (!current || !panel) return;
  if (remember) store("shell:panel", id);
  selected = id;
  if (id !== AI_TAB) lastOther = id;
  syncButtons();
  for (const b of tabs.children) b.setAttribute("aria-selected", String(b.dataset.panel === id));
  for (const [pid, m] of current.mounted) m.el.hidden = pid !== id;
  if (current.mounted.has(id)) return;
  const entry = { el: h("div", { class: "panel", role: "tabpanel", dataset: { panel: id } }), cleanup: null };
  const owner = current;
  owner.mounted.set(id, entry);
  body.append(entry.el);
  Promise.resolve()
    .then(() => panel.mount(entry.el, owner.ctx))
    .then((cleanup) => {
      if (typeof cleanup !== "function") return;
      if (current === owner) entry.cleanup = cleanup;
      else cleanup(); // the note changed while it mounted
    })
    .catch((err) => {
      console.error(`panel ${id} failed`, err);
      entry.el.replaceChildren(h("p", { class: "panel-empty" }, "Paneeli laadimine ebaõnnestus."));
    });
}
