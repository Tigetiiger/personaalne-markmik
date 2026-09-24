// App shell (WP-C): sidebar, note page, right-panel tabs, command palette, Täna, pane layout.
// docs/PLAN-OVERVIEW.md §4; contract in docs/CONTRACTS.md §5 row C and §6.
import { h } from "./dom.js";
import { hasLeft, hasRight, paneButton } from "./layout.js";
import { newNote } from "./note.js";
import { openPalette } from "./palette.js";
import "./sidebar.js";
import "./links.js";
import "./outline.js";
import "./today.js";

// app.js marks "/" current only on "/" itself; every /m/<slug> is the Märkmed page too.
if (document.body.dataset.page === "notes") document.querySelector('#nav a[href="/"]')?.setAttribute("aria-current", "page");

const SEARCH_ICON = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="6.5" cy="6.5" r="4.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m10 10 4.5 4.5" stroke="currentColor" stroke-width="2"/></svg>';

const slot = document.getElementById("topbar-slot"); // only the owner has one
if (slot) {
  const palette = h("button", { type: "button", class: "palette-open", title: "Käsud ja otsing (Ctrl+K)", "aria-label": "Käsud ja otsing", onclick: openPalette },
    h("span", { class: "palette-open-text" }, "Otsi…"), h("kbd", {}, "Ctrl K"));
  palette.insertAdjacentHTML("afterbegin", SEARCH_ICON); // a constant, no user text
  slot.append(
    hasLeft ? paneButton("left", { class: "pane-toggle toggle-left", title: "Märkmed (Ctrl+\\)", "aria-label": "Näita või peida märkmete loend" }, "☰") : "",
    palette,
    hasRight ? paneButton("right", { class: "pane-toggle toggle-right", title: "Kõrvalpaneel (Ctrl+Shift+\\)", "aria-label": "Näita või peida kõrvalpaneel" }, "◨") : "",
  );
  window.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      const key = e.key.toLowerCase();
      if (key === "k") openPalette();
      else if (key === "n") newNote();
      else return;
      e.preventDefault();
      e.stopPropagation();
    },
    true,
  );
}
