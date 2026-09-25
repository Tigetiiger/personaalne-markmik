// "Sisukord" panel: the open note's headings, read from the editor itself so it follows typing;
// a click scrolls to the heading.
import { registerPanel } from "/static/app.js";
import { debounce, h } from "./dom.js";

registerPanel("sisukord", {
  title: "Sisukord",
  order: 20,
  mount(el) {
    const editor = document.getElementById("editor");
    const render = () => {
      const heads = [...editor.querySelectorAll(".mu-editor :is(h1, h2, h3, h4, h5, h6)")]
        .map((node) => ({ node, level: Number(node.tagName[1]), text: node.textContent.replace(/^#{1,6}\s*/, "").trim() }))
        .filter((x) => x.text);
      if (!heads.length) return el.replaceChildren(h("p", { class: "panel-empty" }, "Selles märkmes pole pealkirju."));
      const top = Math.min(...heads.map((x) => x.level));
      el.replaceChildren(
        h("ul", { class: "outline" },
          heads.map(({ node, level, text }) =>
            h("li", { style: `--depth: ${level - top}` },
              h("button", { type: "button", class: "linklike", onclick: () => node.scrollIntoView({ block: "start", behavior: "smooth" }) }, text)))),
      );
    };
    render();
    const observer = new MutationObserver(debounce(render, 250));
    observer.observe(editor, { subtree: true, childList: true, characterData: true });
    return () => observer.disconnect();
  },
});
