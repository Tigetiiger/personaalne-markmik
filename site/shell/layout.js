// The three panes: which ones a page has, the collapse toggles (Ctrl+\ left, Ctrl+Shift+\ right,
// remembered), and on a phone the left pane as a drawer and the right one as a bottom sheet. The
// right panel starts closed: the crumb row's Viited and ✦ AI open it.
import { h, store, stored } from "./dom.js";

const app = document.getElementById("app");
const page = document.body.dataset.page;
const phone = matchMedia("(max-width: 900px)");

// The note page has both side panes, Täna only the sidebar; every other page (graph, calendar,
// Postkast, Seaded, ...) gets the full width. A page that wants a pane back removes the class in
// its own mount, which runs after this.
export const hasLeft = Boolean(app) && (page === "notes" || page === "today");
export const hasRight = Boolean(app) && page === "notes";
if (app) {
  if (!hasLeft) app.classList.add("no-left");
  if (!hasRight) app.classList.add("no-right");
  if (hasLeft && stored("shell:left") === "0") app.classList.add("no-left");
  if (hasRight && stored("shell:right") !== "1") app.classList.add("no-right");
}

const SIDES = { left: { has: hasLeft, key: "shell:left" }, right: { has: hasRight, key: "shell:right" } };

export function isOpen(side) {
  return phone.matches ? app.classList.contains(`show-${side}`) : !app.classList.contains(`no-${side}`);
}

/** Show or hide a side pane: collapse on a wide screen (remembered), drawer/sheet on a phone. */
export function toggle(side, open = !isOpen(side)) {
  if (!SIDES[side].has) return;
  if (phone.matches) {
    app.classList.toggle(`show-${side}`, open);
    if (open) app.classList.remove(`show-${side === "left" ? "right" : "left"}`);
  } else {
    app.classList.toggle(`no-${side}`, !open);
    store(SIDES[side].key, open ? "1" : "0");
  }
  syncButtons();
}

/** Close the phone drawer and sheet (after following a link in them). */
export function closeOverlays() {
  if (!app) return;
  app.classList.remove("show-left", "show-right");
  syncButtons();
}

const buttons = [];
export function syncButtons() {
  for (const [side, button, expanded] of buttons) button.setAttribute("aria-expanded", String(isOpen(side) && expanded()));
}

/** A button that toggles one side pane, or runs attrs.onclick instead; `expanded()` narrows when
 *  it counts as open (Viited: the right panel, but not on the Abiline tab). */
export function paneButton(side, attrs, ...content) {
  const { expanded = () => true, ...rest } = attrs;
  const button = h("button", { type: "button", "aria-controls": side, ...rest, onclick: rest.onclick || (() => toggle(side)) }, ...content);
  buttons.push([side, button, expanded]);
  syncButtons();
  return button;
}

if (app) {
  // Close buttons for the drawer and the sheet (shown on a phone only).
  for (const side of ["left", "right"]) {
    if (!SIDES[side].has) continue;
    document.getElementById(side).prepend(
      h("button", { type: "button", class: "pane-close ghost", "aria-label": "Sulge", onclick: () => toggle(side, false) }, "✕"),
    );
  }
  // A tap on the note behind the drawer or sheet closes it (not the tap on the button opening one).
  document.getElementById("main").addEventListener("click", (e) => phone.matches && !e.target.closest("[aria-controls]") && closeOverlays());
  phone.addEventListener("change", () => {
    closeOverlays();
    syncButtons();
  });
  window.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || (e.code !== "Backslash" && e.key !== "\\" && e.key !== "|")) return;
      e.preventDefault();
      e.stopPropagation();
      toggle(e.shiftKey ? "right" : "left");
    },
    true,
  );
}
