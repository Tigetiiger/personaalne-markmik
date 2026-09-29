// Aktsiad tab stub; its package replaces this file (docs/CONTRACTS.md, "Aktsiad").
import { h } from "./common.js";

export function mount(el) {
  el.append(h("p", { class: "leht-juhend" }, "Tuleb peagi."));
}
