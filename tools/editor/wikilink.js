// "[[" picker: typing [[ (Muya auto-pairs the first bracket: [[]) opens a list of notes matching
// what is typed after it; choosing one replaces [[query]] with an ordinary Markdown link
// [Title](/m/slug). "Loo uus «…»" creates the note first. In git the links become wikilinks
// (app/linkfmt.py); in the editor they stay links Muya can render and edit.
import { api, toast } from "/static/app.js";

const BEFORE = /\[\[([^[\]\n]{0,80})$/; // text before the caret: "[[" and the query so far
const DEBOUNCE_MS = 120;

const escapeTitle = (title) => title.replace(/[\\[\]]/g, "\\$&");

export function mountWikilink(muya, { signal } = {}) {
  const box = document.createElement("div");
  box.className = "mt-wikilink";
  box.setAttribute("role", "listbox");
  box.hidden = true;
  document.body.append(box);
  signal?.addEventListener("abort", () => box.remove());

  let at = null; // {block, start, end, query, raw}: raw = "[[query" and up to two "]", at start..end
  let dismissed = null; // Escape: stays closed for this "[[" until it is gone
  let items = [];
  let active = 0;
  let request = 0;
  let timer = null;

  function find() {
    const sel = muya.editor.selection.getSelection();
    const block = sel && sel.isCollapsed && sel.anchorBlock;
    if (!block || !block.isContent() || !muya.domNode.contains(block.domNode)) return null;
    const caret = sel.anchor.offset;
    const m = block.text.slice(0, caret).match(BEFORE);
    if (!m) return null;
    const start = caret - m[1].length - 2;
    const end = caret + block.text.slice(caret, caret + 2).match(/^\]*/)[0].length;
    return { block, start, end, query: m[1], raw: block.text.slice(start, end) };
  }

  function close() {
    at = null;
    items = [];
    request++;
    clearTimeout(timer);
    box.hidden = true;
  }

  function update() {
    const found = find();
    if (dismissed && !(found && found.block === dismissed.block && found.start === dismissed.start)) dismissed = null;
    if (!found || dismissed) return close();
    const changed = !at || at.query !== found.query || at.block !== found.block;
    at = found;
    place();
    if (changed) {
      clearTimeout(timer);
      timer = setTimeout(load, DEBOUNCE_MS);
    }
  }

  async function load() {
    const n = ++request;
    const query = at.query.trim();
    let notes = [];
    try {
      notes = await api(`/api/notes/suggest?q=${encodeURIComponent(query)}`);
    } catch (err) {
      console.warn("wikilink: suggest failed", err);
    }
    if (n !== request || !at) return;
    items = notes.slice(0, 10).map((note) => ({ note }));
    if (query && !notes.some((note) => note.title.toLowerCase() === query.toLowerCase())) items.push({ create: query });
    active = 0;
    render();
  }

  function render() {
    box.replaceChildren(
      ...items.map((item, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "mt-wikilink-item";
        b.setAttribute("role", "option");
        b.setAttribute("aria-selected", String(i === active));
        if (item.note) {
          b.append(Object.assign(document.createElement("span"), { textContent: item.note.title || item.note.slug }));
          if (item.note.folder) b.append(Object.assign(document.createElement("small"), { textContent: item.note.folder }));
        } else {
          b.textContent = `Loo uus «${item.create}»`;
          b.classList.add("create");
        }
        // mousedown would take the caret out of the editor, and with it the [[…]] being chosen for.
        b.addEventListener("mousedown", (e) => e.preventDefault());
        b.addEventListener("click", () => choose(item));
        return b;
      }),
    );
    if (!items.length) box.append(Object.assign(document.createElement("p"), { className: "mt-wikilink-empty", textContent: "Kirjuta märkme nimi" }));
    box.hidden = false;
    place();
  }

  function place() {
    if (!at) return;
    const sel = window.getSelection();
    let r = sel.rangeCount ? sel.getRangeAt(0).getBoundingClientRect() : null;
    if (!r || (!r.width && !r.height && !r.top)) r = at.block.domNode.getBoundingClientRect();
    box.style.left = `${Math.max(4, Math.min(r.left, document.documentElement.clientWidth - box.offsetWidth - 4))}px`;
    box.style.top = `${r.bottom + 4}px`;
  }

  async function choose(item) {
    const target = at;
    if (!target) return;
    let note = item.note;
    if (item.create) {
      try {
        note = await api("/api/notes", { method: "POST", body: { title: item.create } });
      } catch (err) {
        toast(err.message, "error");
        return;
      }
    }
    // While the note was being created the text may have moved on (a co-editor typed).
    const { block, raw } = target;
    const start = block.text.slice(target.start, target.end) === raw ? target.start : block.text.indexOf(raw);
    if (start < 0 || !muya.domNode.contains(block.domNode)) return close();
    const link = `[${escapeTitle(note.title || note.slug)}](/m/${note.slug})`;
    block.text = block.text.slice(0, start) + link + block.text.slice(start + raw.length);
    block.setCursor(start + link.length, start + link.length, true);
    close();
  }

  muya.domNode.addEventListener("input", update);
  document.addEventListener("selectionchange", () => at && update(), { signal });
  document.addEventListener("scroll", () => at && place(), { capture: true, passive: true, signal });
  // Captured, before Muya: Enter must not split the line while the list is open.
  muya.domNode.addEventListener(
    "keydown",
    (e) => {
      if (box.hidden || !at) return;
      switch (e.key) {
        case "ArrowDown":
          if (!items.length) return;
          active = (active + 1) % items.length;
          render();
          break;
        case "ArrowUp":
          if (!items.length) return;
          active = (active + items.length - 1) % items.length;
          render();
          break;
        case "Enter":
        case "Tab":
          if (!items.length) return;
          choose(items[active]);
          break;
        case "Escape":
          dismissed = { block: at.block, start: at.start };
          close();
          break;
        default:
          return;
      }
      e.preventDefault();
      e.stopPropagation();
    },
    true,
  );
}
