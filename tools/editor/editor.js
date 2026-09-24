// MarkText's editor core (Muya) with the UI plugins MarkText uses, plus our toolbar, find bar,
// image gallery, caret fixes and note links. Ported from the Seljakotirobot site (tools/editor.js).
// tools/build-editor.mjs copies this to site/muya and rewrites the "@muyajs/core" import.
import {
  Muya,
  en,
  CodeBlockLanguageSelector,
  EmojiSelector,
  FootnoteTool,
  ImageEditTool,
  ImageResizeBar,
  ImageToolBar,
  InlineFormatToolbar,
  LinkTools,
  ParagraphFrontButton,
  ParagraphFrontMenu,
  ParagraphQuickInsertMenu,
  PreviewToolBar,
  TableColumnToolbar,
  TableDragBar,
  TableRowColumMenu,
} from "@muyajs/core";

import { api, toast } from "/static/app.js";

export async function upload(file) {
  const body = new FormData();
  body.append("file[]", file, file.name || "pilt.png");
  const data = await api("/pildid", { method: "POST", body });
  const src = Object.values(data.data.succMap)[0];
  if (!src) throw new Error("Server ei võtnud pilti vastu.");
  return src;
}

async function dataUrlToFile(src) {
  const blob = await (await fetch(src)).blob();
  return new File([blob], `pilt.${blob.type.split("/")[1] || "png"}`, { type: blob.type });
}

// Muya.use registers a plugin for every Muya created afterwards; one registration for the page.
let registered = false;
function registerPlugins() {
  if (registered) return;
  registered = true;
  Muya.use(InlineFormatToolbar);
  Muya.use(ImageToolBar);
  Muya.use(ImageResizeBar);
  // MarkText gives imageAction every src that is not http(s). In a browser that is only a data
  // URL (a pasted picture); there are no file paths.
  Muya.use(ImageEditTool, {
    imageAction: async ({ src }) => (src.startsWith("data:") ? upload(await dataUrlToFile(src)) : src),
  });
  Muya.use(CodeBlockLanguageSelector);
  Muya.use(EmojiSelector);
  // Links to notes are followed by each editor itself (mountLinks); this is for the web.
  Muya.use(LinkTools, {
    jumpClick: (info) => {
      const href = info && info.href;
      if (href && /^https?:\/\//.test(href)) window.open(href, "_blank", "noopener,noreferrer");
    },
  });
  Muya.use(ParagraphFrontButton);
  Muya.use(ParagraphFrontMenu);
  Muya.use(ParagraphQuickInsertMenu);
  Muya.use(TableColumnToolbar);
  Muya.use(TableDragBar);
  Muya.use(TableRowColumMenu);
  Muya.use(PreviewToolBar);
  Muya.use(FootnoteTool);
}

/** A Muya editor in place of `container` (Muya replaces the element with its own). */
export function mountEditor(container, { markdown }) {
  registerPlugins();
  const muya = new Muya(container, {
    markdown,
    spellcheckEnabled: false,
    hideQuickInsertHint: false,
    // The server renders both too (app/render.py): footnotes, and math via KaTeX in the browser.
    footnote: true,
    math: true,
    mermaidTheme: document.documentElement.dataset.teema === "tume" ? "dark" : "neutral",
    bulletListMarker: "-",
    // A list typed in the editor stays tight ("- [ ] a\n- [ ] b"), the same as one read from Markdown.
    preferLooseListItem: false,
    codeBlockLineNumbers: false,
  });
  muya.locale(en);
  muya.init();

  // "@" menu: when no item matches, Muya would leave an empty "No result" box. The writer then
  // just wanted an "@", so the menu hides. Only on the next frame: show() places the box
  // asynchronously and would make it visible again.
  const quickInsert = muya._uiPlugins.quickInsert;
  const search = quickInsert.search.bind(quickInsert);
  quickInsert.search = (text) => {
    search(text);
    if (!quickInsert.renderData.length) {
      requestAnimationFrame(() => {
        if (!quickInsert.renderData.length) quickInsert.hide();
      });
    }
  };

  // MarkText's paste and drop: a picture file is uploaded and ![name](url) typed at the caret,
  // which Muya renders as the picture right away.
  async function insertImages(files) {
    for (const file of files) {
      const src = await upload(file);
      const alt = (file.name || "pilt").replace(/\.[^.]+$/, "").replace(/[[\]]/g, "");
      document.execCommand("insertText", false, `![${alt}](${src})`);
    }
  }
  const imageFiles = (list) => Array.from(list || []).filter((f) => f.type.startsWith("image/"));
  const failed = (err) => toast(err.message, "error");
  const root = muya.domNode;

  // A math block is already $$ … $$: a formula pasted with its own $$ (from a chat, say) would
  // have them twice. Only the inside is pasted.
  root.addEventListener(
    "paste",
    (e) => {
      const text = e.clipboardData && e.clipboardData.getData("text/plain");
      const math = text && text.trim().match(/^\$\$\s*([\s\S]*?)\s*\$\$$/);
      if (!math) return;
      const sel = muya.editor.selection.getSelection();
      let block = sel && sel.anchorBlock;
      while (block && block.blockName !== "math-block") block = block.parent;
      if (!block) return;
      e.preventDefault();
      e.stopPropagation();
      document.execCommand("insertText", false, math[1]);
    },
    true,
  );
  root.addEventListener(
    "paste",
    (e) => {
      const files = imageFiles(e.clipboardData && e.clipboardData.files);
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      insertImages(files).catch(failed);
    },
    true,
  );
  root.addEventListener(
    "dragover",
    (e) => {
      if (e.dataTransfer && e.dataTransfer.types.includes("Files")) e.preventDefault();
    },
    true,
  );
  root.addEventListener(
    "drop",
    (e) => {
      const files = imageFiles(e.dataTransfer && e.dataTransfer.files);
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      if (!root.contains(document.activeElement)) muya.focus();
      insertImages(files).catch(failed);
    },
    true,
  );

  return muya;
}

// --- Links to other notes ----------------------------------------------------------------------
// Muya never follows a link on click (it is a text editor). Ctrl/Cmd+click on a link to a note,
// or the "jump" icon of the link's hover tools, opens that note.

/** "/m/<slug>" (also as a full URL on this site) -> slug, else null. */
export function noteSlug(href) {
  if (!href) return null;
  try {
    const url = new URL(href, location.href);
    const m = url.origin === location.origin && url.pathname.match(/^\/m\/([^/]+)\/?$/);
    return m ? decodeURIComponent(m[1]) : null;
  } catch {
    return null;
  }
}

export function mountLinks(muya, onOpenNote) {
  muya.domNode.addEventListener(
    "click",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || !(e.target instanceof Element)) return;
      const link = e.target.closest("span.mu-link, a.mu-reference-link, a.mu-raw-html");
      const slug = link && noteSlug(link.getAttribute("href") || link.href);
      if (!slug) return;
      e.preventDefault();
      e.stopPropagation();
      onOpenNote(slug);
    },
    true,
  );
  const tools = muya._uiPlugins.linkTools;
  const selectItem = tools.selectItem.bind(tools);
  tools.selectItem = (e, item) => {
    const slug = item.type === "jump" && tools.linkInfo && noteSlug(tools.linkInfo.href);
    if (!slug) return selectItem(e, item);
    e.preventDefault();
    e.stopPropagation();
    tools.hide();
    onOpenNote(slug);
  };
}

// --- MarkText's menu commands on a toolbar --------------------------------------------------
// MarkText has paragraph and format commands in its menu bar and on shortcuts. Muya itself does
// block commands only on an empty line and only with Cmd (macOS), so they are redone here
// through the logic of Muya's paragraph menu "Turn into", which also works on a line with text.

const isMac = /Mac/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";
const ALT = isMac ? "⌥" : "Alt";

// [label, Muya label, tooltip, key] — the same keys as MarkText (KeyboardEvent.code).
const PARAGRAPH = [
  ["Lõik", "paragraph", "Tavaline lõik", { code: "Digit0" }],
  ["Pealkiri 1", "atx-heading 1", "Pealkiri 1", { code: "Digit1" }],
  ["Pealkiri 2", "atx-heading 2", "Pealkiri 2", { code: "Digit2" }],
  ["Pealkiri 3", "atx-heading 3", "Pealkiri 3", { code: "Digit3" }],
  ["Pealkiri 4", "atx-heading 4", "Pealkiri 4", { code: "Digit4" }],
  ["Pealkiri 5", "atx-heading 5", "Pealkiri 5", { code: "Digit5" }],
  ["Pealkiri 6", "atx-heading 6", "Pealkiri 6", { code: "Digit6" }],
];
const BLOCKS = [
  ["“", "block-quote", "Tsitaat", { code: "KeyQ", alt: true }],
  ["•", "bullet-list", "Täpploend", { code: "KeyU", alt: true }],
  ["1.", "order-list", "Nummerdatud loend", { code: "KeyO", alt: true }],
  ["[x]", "task-list", "Ülesannete loend", { code: "KeyX", alt: true }],
  ["▦", "table", "Tabel", { code: "KeyT", shift: true }],
  ["{ }", "code-block", "Koodiplokk", { code: "KeyC", alt: true }],
  ["∑", "math-block", "Valem (LaTeX)", { code: "KeyM", alt: true }],
  ["◇", "diagram mermaid", "Mermaid-diagramm", null],
  ["</>", "html-block", "HTML-plokk", { code: "KeyJ", alt: true }],
  ["―", "thematic-break", "Horisontaaljoon", { code: "Minus", alt: true }],
];
// Labels without emoji: not every system has an emoji font.
const UNDO = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3 2 7l4 4M2.5 7H10a4 4 0 0 1 0 8H8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
const REDO = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="m10 3 4 4-4 4M13.5 7H6a4 4 0 0 0 0 8h2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
// Muya handles these keys itself (InlineFormatToolbar); here are only the buttons.
const FORMATS = [
  ["B", "strong", "Paks", `${MOD}+B`],
  ["I", "em", "Kaldkiri", `${MOD}+I`],
  ["U", "u", "Allajoonitud", `${MOD}+U`],
  ["S", "del", "Läbikriipsutatud", `${MOD}+D`],
  ["H", "mark", "Esiletõst", `${MOD}+Shift+H`],
  ["`", "inline_code", "Koodijupp", `${MOD}+E`],
  ["$", "inline_math", "Valem real", `${MOD}+Shift+E`],
  ["Link", "link", "Link", `${MOD}+L`],
  ["Pilt", "image", "Pilt", `${MOD}+Shift+I`],
  ["Tx", "clear", "Eemalda vormindus", `${MOD}+Shift+R`],
];
// These add a new block and must not take the text of the current line.
const INSERTS = /table|code-block|math-block|html-block|thematic-break|diagram/;
const LISTS = /^(bullet-list|order-list|task-list)$/;

function keyLabel(k) {
  return [MOD, k.shift && "Shift", k.alt && ALT, k.code.replace(/^(Digit|Key)/, "").replace("Minus", "-")]
    .filter(Boolean)
    .join("+");
}

function button(label, title, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  if (label.startsWith("<svg")) b.innerHTML = label;
  else b.textContent = label;
  b.title = title;
  // mousedown must not take the focus from the editor, or the selection is lost.
  b.addEventListener("mousedown", (e) => e.preventDefault());
  b.addEventListener("click", onClick);
  return b;
}

function group(...children) {
  const g = document.createElement("div");
  g.className = "mt-group";
  g.append(...children);
  return g;
}

// onImage: instead of MarkText's image dialog, the gallery opens (mountGallery).
// signal: aborting it removes the listeners on the document.
export function mountToolbar(muya, toolbar, { onImage, signal } = {}) {
  const frontMenu = muya._uiPlugins.frontMenu;
  const noop = { preventDefault() {}, stopPropagation() {} };

  function selectedContent() {
    const sel = muya.editor.selection.getSelection();
    return sel && sel.anchorBlock && muya.domNode.contains(sel.anchorBlock.domNode) ? sel.anchorBlock : null;
  }

  // The block Muya's paragraph menu would pick: the whole list for a list, else paragraph/heading.
  function targetBlock(content, label) {
    const outer = content.parent;
    if (LISTS.test(label)) {
      for (let b = outer; b; b = b.parent) if (LISTS.test(b.blockName)) return b;
    }
    return /^(paragraph|atx-heading)$/.test(outer.blockName) ? outer : null;
  }

  // The outermost block around the caret (a formula, a table or a whole list).
  function topBlock(b) {
    while (b.parent && b.parent.blockName !== "scrollpage") b = b.parent;
    return b;
  }

  function turnInto(label) {
    const content = selectedContent();
    if (!content) return muya.focus();
    let block = targetBlock(content, label);
    const insert = INSERTS.test(label);
    if (!block && !insert) return;
    // A new block goes on a new line: after a line with text, or after code/table/formula.
    if (insert && (!block || content.text.trim())) {
      frontMenu._block = block || topBlock(content);
      frontMenu.selectItem(noop, { label: "new" });
      block = selectedContent().parent;
    }
    frontMenu._block = block;
    frontMenu.selectItem(noop, { label });
  }

  function format(type) {
    const content = selectedContent();
    if (content && typeof content.format === "function") content.format(type);
    else muya.focus();
  }

  // Paragraph type drop-down like MarkText's "Paragraph" menu.
  const menu = document.createElement("div");
  menu.className = "mt-menu";
  menu.hidden = true;
  for (const [label, type, title, key] of PARAGRAPH) {
    const b = button(label, `${title} (${keyLabel(key)})`, () => {
      menu.hidden = true;
      turnInto(type);
    });
    b.className = `mt-${type.replace(" ", "-")}`;
    menu.append(b);
  }
  const menuButton = button("Lõik ▾", "Lõigu tüüp", () => {
    menu.hidden = !menu.hidden;
  });
  const menuWrap = document.createElement("div");
  menuWrap.className = "mt-menuwrap";
  menuWrap.append(menuButton, menu);
  document.addEventListener("click", (e) => {
    if (!menuWrap.contains(e.target)) menu.hidden = true;
  }, { signal });

  toolbar.append(
    group(menuWrap),
    group(
      ...FORMATS.map(([label, type, title, key]) =>
        button(label, `${title} (${key})`, () => (type === "image" && onImage ? onImage() : format(type))),
      ),
    ),
    group(...BLOCKS.map(([label, type, title, key]) => button(label, key ? `${title} (${keyLabel(key)})` : title, () => turnInto(type)))),
    group(
      button(UNDO, `Võta tagasi (${MOD}+Z)`, () => muya.undo()),
      button(REDO, `Tee uuesti (${MOD}+Shift+Z)`, () => muya.redo()),
    ),
  );

  // Keys before Muya (capture), so Ctrl works on Linux/Windows too and on a line with text.
  const shortcuts = [...PARAGRAPH, ...BLOCKS].filter((row) => row[3]);
  muya.domNode.addEventListener(
    "keydown",
    (e) => {
      if (!(isMac ? e.metaKey : e.ctrlKey)) return;
      if (onImage && e.code === "KeyI" && e.shiftKey && !e.altKey) {
        e.preventDefault();
        e.stopPropagation();
        onImage();
        return;
      }
      // Muya's history works only through its API (MarkText binds it in the menu); the
      // browser's own contenteditable undo would drift away from Muya's state.
      if (e.code === "KeyZ" || e.code === "KeyY") {
        e.preventDefault();
        e.stopPropagation();
        if (e.code === "KeyY" || e.shiftKey) muya.redo();
        else muya.undo();
        return;
      }
      const hit = shortcuts.find(([, , , k]) => k.code === e.code && !!k.shift === e.shiftKey && !!k.alt === e.altKey);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      turnInto(hit[1]);
    },
    true,
  );

  return { turnInto, format };
}

// --- Find and replace (MarkText's Ctrl+F / Ctrl+R) -----------------------------------------
export function mountFind(muya, bar) {
  const query = bar.querySelector("input[type=search]");
  const replacement = bar.querySelector("input.mt-replace");
  const count = bar.querySelector(".mt-count");
  let result = null;

  function show() {
    result = muya.search(query.value, { isCaseSensitive: bar.querySelector(".mt-case").checked });
    count.textContent = query.value ? `${result.matches.length ? result.index + 1 : 0}/${result.matches.length}` : "";
  }
  function open(withReplace) {
    bar.hidden = false;
    bar.classList.toggle("with-replace", withReplace);
    const sel = document.getSelection().toString();
    if (sel && !sel.includes("\n")) query.value = sel;
    query.focus();
    query.select();
    show();
  }
  function close() {
    bar.hidden = true;
    muya.search("");
    muya.focus();
  }
  function step(dir) {
    if (!result) show();
    result = muya.find(dir);
    count.textContent = `${result.matches.length ? result.index + 1 : 0}/${result.matches.length}`;
  }

  query.addEventListener("input", show);
  bar.querySelector(".mt-case").addEventListener("change", show);
  query.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      step(e.shiftKey ? "previous" : "next");
    }
  });
  bar.querySelector(".mt-prev").addEventListener("click", () => step("previous"));
  bar.querySelector(".mt-next").addEventListener("click", () => step("next"));
  bar.querySelector(".mt-one").addEventListener("click", () => {
    muya.replace(replacement.value, { isSingle: true, isRegexp: false });
    show();
  });
  bar.querySelector(".mt-all").addEventListener("click", () => {
    muya.replace(replacement.value, { isSingle: false, isRegexp: false });
    show();
  });
  bar.querySelector(".mt-close").addEventListener("click", close);
  bar.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });

  return { open, close };
}

// --- Image gallery ------------------------------------------------------------------------------
// Every picture ever added (pictures are never deleted: any note may still show one). A click
// inserts the picture at the caret; "Lisa uus" uploads a file and inserts it right away.
export function mountGallery(muya, dialog) {
  const grid = dialog.querySelector(".mt-gallery-grid");
  const empty = dialog.querySelector(".mt-gallery-empty");
  const fileInput = dialog.querySelector("input[type=file]");
  let saved = null;

  function remember() {
    const sel = muya.editor.selection.getSelection();
    saved = sel && sel.anchorBlock && muya.domNode.contains(sel.anchorBlock.domNode)
      ? { block: sel.anchorBlock, start: Math.min(sel.anchor.offset, sel.focus.offset), end: Math.max(sel.anchor.offset, sel.focus.offset) }
      : null;
  }

  function insert(images) {
    dialog.close();
    if (saved && muya.domNode.contains(saved.block.domNode)) {
      saved.block.setCursor(saved.start, saved.end, true);
    } else {
      const last = muya.editor.scrollPage.lastContentInDescendant();
      last.setCursor(last.text.length, last.text.length, true);
    }
    const text = images.map(({ name, url }) => `![${name.replace(/\.[^.]+$/, "").replace(/[[\]]/g, "")}](${url})`).join(" ");
    document.execCommand("insertText", false, text);
  }

  async function render() {
    const images = await api("/api/images");
    const text = muya.getMarkdown();
    empty.hidden = images.length > 0;
    grid.replaceChildren(
      ...images.map((img) => {
        const name = img.original_name || img.name;
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.type = "button";
        b.title = `Lisa „${name}“ teksti`;
        const pic = Object.assign(document.createElement("img"), { src: img.url, alt: "", loading: "lazy" });
        const label = Object.assign(document.createElement("span"), { textContent: name });
        const meta = Object.assign(document.createElement("small"), { textContent: text.includes(img.url) ? "tekstis" : "" });
        b.append(pic, label, meta);
        b.addEventListener("click", () => insert([{ name, url: img.url }]));
        li.append(b);
        return li;
      }),
    );
  }

  fileInput.addEventListener("change", async () => {
    const files = Array.from(fileInput.files || []);
    fileInput.value = "";
    try {
      const added = [];
      for (const file of files) added.push({ name: file.name || "pilt", url: await upload(file) });
      if (added.length) insert(added);
    } catch (err) {
      toast(err.message, "error");
    }
  });
  dialog.querySelector(".mt-close").addEventListener("click", () => dialog.close());

  // "@" menu item "image" (added by the build): the "@pilt" line empties and the gallery opens.
  const quickInsert = muya._uiPlugins.quickInsert;
  const selectItem = quickInsert.selectItem.bind(quickInsert);
  quickInsert.selectItem = (item) => {
    if (item.label !== "image") return selectItem(item);
    const block = quickInsert.block;
    quickInsert.hide();
    block.text = "";
    block.setCursor(0, 0, true);
    open();
  };

  function open() {
    remember();
    dialog.showModal();
    render().catch((err) => {
      empty.hidden = false;
      empty.textContent = err.message;
    });
  }

  return { open };
}

// --- Caret in picture-only paragraphs -------------------------------------------------------
// In a paragraph holding only a picture Muya's caret gets stuck (arrow down, End and Enter do
// nothing, a typed character is lost). Here the arrows move on to the next/previous block and
// Enter (or a typed character) starts a new paragraph under the picture.
const IMAGE_MD = /!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>/g; // a Markdown picture or Muya's aligned/sized <img>
export function mountCaret(muya) {
  const page = muya.editor.scrollPage;
  const make = (state) => page.constructor.loadBlock(state.name).create(muya, state);
  const imageOnly = (content) => content.blockName === "paragraph.content" && content.text.trim() !== "" && content.text.replace(IMAGE_MD, "").trim() === "";
  let offset = 0;
  const current = () => {
    const s = muya.editor.selection.getSelection();
    offset = s && s.anchor ? s.anchor.offset : 0;
    return s && s.anchorBlock && s.isCollapsed && muya.domNode.contains(s.anchorBlock.domNode) ? s.anchorBlock : null;
  };
  const go = (content, dir) => {
    const c = dir > 0 ? content.nextContentInContext() : content.previousContentInContext();
    if (!c) return;
    const at = dir > 0 || imageOnly(c) ? 0 : c.text.length;
    c.setCursor(at, at, true);
  };
  // A new paragraph after the block in the same container: for a picture in a list item the new
  // paragraph stays inside the item.
  const paragraphAfter = (block, text = "") => {
    const p = make({ name: "paragraph", text });
    block.parent.insertAfter(p, block);
    p.firstContentInDescendant().setCursor(text.length, text.length, true);
  };

  muya.domNode.addEventListener(
    "keydown",
    (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const c = current();
      if (!c || !imageOnly(c)) return;
      let handled = true;
      switch (e.key) {
        case "ArrowDown": go(c, 1); break;
        case "ArrowUp": go(c, -1); break;
        case "ArrowRight": if (offset >= c.text.length) go(c, 1); else handled = false; break;
        case "ArrowLeft": if (offset <= 0) go(c, -1); else handled = false; break;
        case "Enter": paragraphAfter(c.parent); break;
        default:
          if (e.key.length === 1) paragraphAfter(c.parent, e.key);
          else handled = false;
      }
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    true,
  );
}

// --- Block menu by keyboard -------------------------------------------------------------------
// A click on the block handle opens Muya's block menu (mu-front-menu: block types in a grid,
// Duplicate / New Paragraph / Delete below). In Muya only the mouse works there; here the arrows
// move the choice, Enter picks, Escape closes. Captured, so Muya's own arrows do not move the caret.
export function mountFrontMenuKeys(muya, signal) {
  const menu = muya._uiPlugins.frontMenu;
  const items = () => [...menu.container.querySelectorAll(".turn-into-item, li.item")];
  let reference = null;
  let active = -1;
  const paint = (list) => list.forEach((el, i) => el.classList.toggle("mt-focus", i === active));

  document.addEventListener(
    "keydown",
    (e) => {
      if (!menu.status || e.ctrlKey || e.metaKey || e.altKey) return;
      const list = items();
      if (!list.length) return;
      if (menu.reference !== reference) {
        reference = menu.reference;
        active = Math.max(0, list.findIndex((el) => el.classList.contains("active")));
      }
      // Grid rows by offsetTop; up/down keep the column, left/right go in order.
      const rows = [];
      list.forEach((el, i) => {
        const row = rows.find((r) => Math.abs(r.top - el.offsetTop) < 4) || rows[rows.push({ top: el.offsetTop, idx: [] }) - 1];
        row.idx.push(i);
      });
      const r = rows.findIndex((row) => row.idx.includes(active));
      const c = rows[r].idx.indexOf(active);
      const n = list.length;
      switch (e.key) {
        case "ArrowRight": active = (active + 1) % n; break;
        case "ArrowLeft": active = (active + n - 1) % n; break;
        case "ArrowDown": { const row = rows[(r + 1) % rows.length]; active = row.idx[Math.min(c, row.idx.length - 1)]; break; }
        case "ArrowUp": { const row = rows[(r + rows.length - 1) % rows.length]; active = row.idx[Math.min(c, row.idx.length - 1)]; break; }
        case "Enter": list[active].click(); break;
        case "Escape": menu.hide(); break;
        default: return;
      }
      e.preventDefault();
      e.stopPropagation();
      paint(items());
    },
    { capture: true, signal },
  );
}
