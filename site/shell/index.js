// App shell (WP-C): sidebar, note page, right-panel tabs, command palette, Täna, pane layout.
// docs/PLAN-OVERVIEW.md §4; contract in docs/CONTRACTS.md §5 row C and §6.
import { h } from "./dom.js";
import { hasLeft, paneButton } from "./layout.js";
import { newNote } from "./note.js";
import { openPalette } from "./palette.js";
import "./sidebar.js";
import "./links.js";
import "./outline.js";
import "./today.js";

// app.js marks "/" current only on "/" itself; every /m/<slug> is the Märkmed page too.
if (document.body.dataset.page === "notes") document.querySelector('#nav a[href="/"]')?.setAttribute("aria-current", "page");

const SEARCH_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>';
const PLUS_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
const LIST_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>';
// Constants, no user text: put in as markup.
const withIcon = (el, icon) => (el.insertAdjacentHTML("afterbegin", icon), el);

// g t / g m / g k / g g / g p go to a page, n makes a note (docs/KUJUNDUS.md). Single keys: never
// while typing in a field or the editor, nor with a dialog open.
const GO = { t: "/tana", m: "/", k: "/kalender", g: "/graaf", p: "/postkast" };

const slot = document.getElementById("topbar-slot"); // only the owner has one
if (slot) {
  const palette = withIcon(h("button", { type: "button", class: "palette-open", title: "Käsud ja otsing (Ctrl+K)", "aria-label": "Käsud ja otsing", onclick: openPalette },
    h("span", { class: "palette-open-text" }, "Otsi…"), h("kbd", {}, "Ctrl K")), SEARCH_ICON);
  const add = withIcon(h("button", { type: "button", class: "strong new-note", title: "Uus märge (n, Alt+N)", "aria-label": "Uus märge", onclick: newNote }, h("span", { class: "new-note-text" }, "Uus")), PLUS_ICON);
  slot.append(palette, add);
  // The notes list's toggle: the drawer on a phone; Ctrl+\ (and the palette) on a wide screen.
  if (hasLeft) document.querySelector(".topbar .inner").prepend(withIcon(paneButton("left", { class: "pane-toggle toggle-left icon-button", title: "Märkmed (Ctrl+\\)", "aria-label": "Näita või peida märkmete loend" }), LIST_ICON));
  window.addEventListener(
    "keydown",
    (e) => {
      if (e.shiftKey) return;
      const ctrl = (e.ctrlKey || e.metaKey) && !e.altKey;
      const alt = e.altKey && !e.ctrlKey && !e.metaKey;
      // Chrome keeps Ctrl+N for a new window in an ordinary tab, so Alt+N as well. e.code: on a
      // Mac, Alt+N types a character.
      if (ctrl && e.key.toLowerCase() === "k") openPalette();
      else if ((ctrl && e.key.toLowerCase() === "n") || (alt && e.code === "KeyN")) newNote();
      else return;
      e.preventDefault();
      e.stopPropagation();
    },
    true,
  );
  let gAt = 0; // when "g" was pressed
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || e.defaultPrevented) return;
    const target = e.target;
    if (target.isContentEditable || target.closest?.("input, textarea, select") || document.querySelector("dialog[open]")) return;
    const chord = Date.now() - gAt < 1500;
    gAt = 0;
    if (chord && GO[e.key]) location.href = GO[e.key];
    else if (e.key === "g") gAt = Date.now();
    else if (e.key === "n") newNote();
    else return;
    e.preventDefault();
  });
}
