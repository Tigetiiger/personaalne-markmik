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

const slot = document.getElementById("topbar-slot"); // only the owner has one
if (slot) {
  slot.append(
    hasLeft ? paneButton("left", { class: "pane-toggle toggle-left", title: "Märkmed (Ctrl+\\)", "aria-label": "Näita või peida märkmete loend" }, "☰") : "",
    h("button", { type: "button", class: "palette-open", title: "Käsud ja otsing (Ctrl+K)", onclick: openPalette }, "Otsi…", h("kbd", {}, "Ctrl K")),
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
