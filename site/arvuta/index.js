// Python cells in notes as a notebook (docs/ARVUTUS-PLAN.md, docs/JUPYTER-PLAN.md). A ```arvuta
// block runs in the sandbox; its output region under it (```väljund text, "*Tabel: …*" tables,
// plots, a red ```viga) is the server's to rewrite; tables and `= x` values in the note fill in.
// The server answers with the note's new Markdown and only the blocks that differ change in the
// editor (updateMarkdown), as ordinary edits. Cells highlight as Python (a Muya build patch).
// Shift+Enter runs and moves on, Ctrl+Enter runs; each cell has [n], ▶ ⏩ ↑ ↓ ✕ and "+ Kood ·
// + Tekst" under it. Computing runs by itself 800 ms after a change unless the note is set to
// käsitsi. On a desktop each visible cell is edited in a CodeMirror editor laid over it, with
// completion (site/arvuta/koodiredaktor.js, docs/TAIENDUS-PLAN.md). Owner only: guests see the
// outputs that are in the note.
import { api, on, registerMetaAction, toast } from "/static/app.js";

const DEBOUNCE_MS = 800;
const LONG_LINES = 15;
const CELL_LANGS = new Set(["arvuta", "arvuta-peida"]);
const CAPTION = /^\*Tabel(?:: [^*\n]*)? · \d+ rida × \d+ veergu(?: \(näidatud \d+\))?\*\s*$/; // as app/arvuta/cells.py
const PLOT = /^!\[väljund\]\(\/pildid\/[0-9a-f]{40}\.png\)\s*$/;
const isOwner = () => Boolean(document.getElementById("nav"));
const phone = matchMedia("(max-width: 900px)");

let current = null;

const langOf = (b) => String(b?.meta?.lang || "").toLowerCase();
const hasWork = (md) => /^ {0,3}(`{3,}|~{3,})[ \t]*arvuta/m.test(md) || /`=[^`]/.test(md);

/** The document as chunks: each cell with its output region, and every other top-level block on
 *  its own. Same region rules as app/arvuta/cells.py, over Muya's block state. */
export function chunks(state) {
  const out = [];
  if (!Array.isArray(state)) return out;
  for (let i = 0; i < state.length; ) {
    const b = state[i];
    if (b.name === "code-block" && CELL_LANGS.has(langOf(b))) {
      let j = i + 1;
      for (;;) {
        const n = state[j];
        if (n?.name === "code-block" && (langOf(n) === "väljund" || langOf(n) === "viga")) j++;
        else if (n?.name === "paragraph" && PLOT.test(n.text)) j++;
        else if (n?.name === "paragraph" && CAPTION.test(n.text)) j += state[j + 1]?.name === "table" ? 2 : 1;
        else break;
      }
      out.push({ start: i, count: j - i, cell: true, lang: langOf(b) });
      i = j;
    } else out.push({ start: i++, count: 1, cell: false });
  }
  return out;
}

on("editor:ready", ({ note, editor }) => {
  stop();
  if (!isOwner() || !editor?.blocks) return;
  const root = document.querySelector("#editor");
  if (!root) return;
  const c = (current = {
    note, editor, root, timer: null, hash: null, running: false, guests: false, auto: note.arvutus_auto !== false,
    runs: 0, counts: [], status: [], unfolded: new Set(), editors: [], cm: null,
  });
  c.layer = Object.assign(document.createElement("div"), { className: "arvuta-layer" });
  c.cmLayer = Object.assign(document.createElement("div"), { className: "arvuta-layer arvuta-cm-layer" });
  document.body.append(c.cmLayer, c.layer);
  c.resized = new ResizeObserver(() => requestAnimationFrame(() => current === c && decorate()));
  c.observer = new MutationObserver(() => {
    if (current !== c) return;
    decorate();
    schedule();
  });
  c.observer.observe(root, { childList: true, subtree: true, characterData: true });
  c.resize = () => current === c && decorate();
  window.addEventListener("resize", c.resize);
  c.keys = (e) => current === c && keys(e);
  root.addEventListener("keydown", c.keys, true); // capture: before Muya's own Enter handling
  decorate();
  schedule();
  // The code editor only where cells are edited: a desktop, a note with cells.
  if (!phone.matches && hasWork(editor.getMarkdown() || "")) loadCodeEditor(c);
});

async function loadCodeEditor(c) {
  try {
    const module = await import("/static/arvuta/koodiredaktor.js");
    if (current !== c) return;
    c.cm = module;
    decorate();
  } catch (err) {
    console.warn("code editor not loaded; cells stay plain", err);
  }
}
on("note:open", ({ note }) => current && note?.slug !== current.note.slug && stop());
on("home:show", () => stop());

function stop() {
  if (!current) return;
  clearTimeout(current.timer);
  current.observer?.disconnect();
  window.removeEventListener("resize", current.resize);
  current.root.removeEventListener("keydown", current.keys, true);
  current.layer?.remove();
  for (const e of current.editors) e.destroy();
  current.resized?.disconnect();
  current.cmLayer?.remove();
  current = null;
}

function schedule() {
  const c = current;
  clearTimeout(c.timer);
  if (!c.auto) return;
  c.timer = setTimeout(() => current === c && run("auto"), DEBOUNCE_MS);
}

/** Run the cells: "auto", "all", or a cell's index (that one and the ones before it). */
async function run(mode) {
  const c = current;
  if (!c || c.running) return;
  const markdown = c.editor.getMarkdown();
  if (typeof markdown !== "string" || !hasWork(markdown)) return;
  c.running = true;
  decorate();
  try {
    const out = await api("/api/arvuta", { method: "POST", body: { slug: c.note.slug, markdown, run: mode, hash: c.hash } });
    if (current !== c) return;
    if (out.skipped) {
      c.guests = out.skipped === "kulalised";
      return;
    }
    c.guests = false;
    if (out.error) toast(out.error, "error");
    c.runs++;
    (out.cells || []).forEach((s, i) => {
      c.status[i] = { ...s, seconds: out.seconds };
      c.counts[i] = c.runs;
    });
    // Typed meanwhile: the answer is for text that is gone; the next run takes the new text.
    if (out.markdown && c.editor.getMarkdown() === markdown) c.editor.updateMarkdown(out.markdown);
    if (out.hash) c.hash = out.hash;
  } catch (err) {
    if (current === c && err.status !== 409) toast(err.message, "error");
  } finally {
    if (current === c) {
      c.running = false;
      decorate();
    }
  }
}

const confirmGuests = () => !current.guests || window.confirm("Koodi võisid muuta lingiga külalised. Kas käivitan selle?");

/** Shift+Enter in a cell: run it, then the caret goes to the next cell (a new one after the last).
 *  Ctrl+Enter: run and stay. */
function keys(e) {
  if (e.key !== "Enter" || !(e.shiftKey || e.ctrlKey || e.metaKey) || e.isComposing) return;
  const node = e.target.closest?.(".mu-code-block") || document.getSelection()?.anchorNode?.parentElement?.closest(".mu-code-block");
  const c = current;
  const nodes = c.editor.blockNodes();
  const state = c.editor.blocks();
  if (!Array.isArray(nodes) || !Array.isArray(state)) return;
  const list = chunks(state);
  const k = list.filter((ch) => ch.cell).findIndex((ch) => nodes[ch.start] === node);
  if (k < 0) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  runFrom(k, e.shiftKey);
}

function addBlock(at, kind) {
  const c = current;
  resetEditors();
  if (!c.editor.insertBlocks(at, kind === "kood" ? "```arvuta\n```" : "Tekst")) return;
  if (kind === "kood") {
    decorate(); // the new cell gets its code editor now
    const k = chunks(c.editor.blocks()).filter((ch) => ch.cell).findIndex((ch) => ch.start === at);
    if (k >= 0 && c.editors[k]) return c.editors[k].focus(false);
  }
  c.editor.focusBlock(at, { select: kind !== "kood" });
}

/** The caret into cell k: its code editor, or the Muya block. */
function focusCell(k) {
  const c = current;
  const cell = chunks(c.editor.blocks()).filter((ch) => ch.cell)[k];
  if (!cell) return false;
  if (c.editors[k]) c.editors[k].focus(false);
  else c.editor.focusBlock(cell.start);
  return true;
}

/** Shift+Enter (moveOn) or Ctrl+Enter in cell k. */
function runFrom(k, moveOn) {
  const c = current;
  if (!confirmGuests()) return;
  run(k).then(() => {
    if (!moveOn || current !== c) return;
    const cells = chunks(c.editor.blocks()).filter((ch) => ch.cell);
    if (cells[k + 1]) focusCell(k + 1);
    else if (cells[k]) addBlock(cells[k].start + cells[k].count, "kood");
  });
}

/** The cells' code editors are tied to a cell's place: after cells move, come or go they are made
 *  anew (decorate), so no editor holds another cell's text or undo history. */
function resetEditors() {
  const c = current;
  for (const e of c.editors) e?.destroy();
  c.editors = [];
}

/** Move a cell with its outputs past the chunk before or after it; or delete it. */
function moveCell(k, step) {
  const c = current;
  resetEditors();
  const list = chunks(c.editor.blocks());
  const at = list.findIndex((ch) => ch === list.filter((x) => x.cell)[k]);
  const cell = list[at];
  const other = list[at + step];
  if (!cell || !other) return;
  c.editor.moveBlocks(cell.start, cell.count, step < 0 ? other.start : cell.start + other.count);
}

function deleteCell(k) {
  resetEditors();
  const cell = chunks(current.editor.blocks()).filter((ch) => ch.cell)[k];
  if (cell && current.editor.removeBlocks(cell.start, cell.count)) toast("Plokk kustutatud (Ctrl+Z toob tagasi).");
}

/** Code shown or hidden: the cell's language switches between arvuta and arvuta-peida. */
function toggleHidden(k) {
  const c = current;
  resetEditors();
  const cell = chunks(c.editor.blocks()).filter((ch) => ch.cell)[k];
  if (!cell) return;
  const state = c.editor.blocks()[cell.start];
  const lang = cell.lang === "arvuta" ? "arvuta-peida" : "arvuta";
  const fence = "`".repeat(Math.max(3, ...[...String(state.text).matchAll(/`+/g)].map((m) => m[0].length + 1)));
  if (c.editor.removeBlocks(cell.start, 1)) c.editor.insertBlocks(cell.start, `${fence}${lang}\n${state.text}\n${fence}`);
}

async function setAuto(on) {
  const c = current;
  try {
    const note = await api(`/api/notes/${encodeURIComponent(c.note.slug)}`, { method: "PATCH", body: { arvutus_auto: on } });
    if (current !== c) return;
    c.auto = note.arvutus_auto !== false;
    c.note = { ...c.note, arvutus_auto: c.auto };
    toast(c.auto ? "Automaatne arvutus on sees." : "Automaatne arvutus on väljas: käivita ▶, Shift+Enter või Ctrl+Enter.");
    decorate();
    schedule();
  } catch (err) {
    toast(err.message, "error");
  }
}

const REGION_CLASSES = ["arvuta-cm", "arvuta-rakk", "arvuta-peidus", "arvuta-valjund", "arvuta-viga", "arvuta-pikk", "arvuta-tabel-pealkiri", "arvuta-valjund-tabel", "arvuta-graafik"];

/** Classes on the blocks (cells, hidden cells, outputs), and over them the toolbars, the "+" rows
 *  and the fold buttons (not on a phone). */
function decorate() {
  const c = current;
  if (!c) return;
  const state = c.editor.blocks();
  const nodes = c.editor.blockNodes();
  // A handle whose note was left answers null (site/shell/note.js, scoped).
  if (!Array.isArray(state) || !Array.isArray(nodes)) return;
  for (const n of nodes) n?.classList.remove(...REGION_CLASSES);
  const list = chunks(state);
  // A co-editor's new or removed cell remakes the code editors (resetEditors).
  const count = list.filter((ch) => ch.cell).length;
  if (c.cellCount !== undefined && c.cellCount !== count) resetEditors();
  c.cellCount = count;
  const parts = [];
  let k = -1;
  for (const ch of list) {
    if (!ch.cell) continue;
    k++;
    const cellNode = nodes[ch.start];
    cellNode?.classList.add("arvuta-rakk", ...(ch.lang === "arvuta-peida" ? ["arvuta-peidus"] : []));
    let longOutput = null;
    for (let i = ch.start + 1; i < ch.start + ch.count; i++) {
      const b = state[i];
      const n = nodes[i];
      if (!n) continue;
      if (b.name === "code-block" && langOf(b) === "viga") n.classList.add("arvuta-viga");
      else if (b.name === "code-block") {
        n.classList.add("arvuta-valjund");
        const lines = String(b.text).split("\n").length;
        if (lines > LONG_LINES && !c.unfolded.has(k)) {
          n.classList.add("arvuta-pikk");
          longOutput = { node: n, lines };
        }
      } else if (b.name === "table") n.classList.add("arvuta-valjund-tabel");
      else if (PLOT.test(b.text || "")) n.classList.add("arvuta-graafik");
      else n.classList.add("arvuta-tabel-pealkiri");
    }
    if (phone.matches || !cellNode) continue;
    if (c.cm) codeEditor(k, ch, state, cellNode);
    parts.push(toolbar(cellNode, k, ch.lang === "arvuta-peida", k === 0));
    if (longOutput) parts.push(foldButton(longOutput, k));
    parts.push(plusRow(nodes[ch.start + ch.count - 1] || cellNode, ch.start + ch.count));
  }
  if (c.guests && k >= 0 && !phone.matches) {
    const first = nodes[list.find((ch) => ch.cell).start];
    const r = first.getBoundingClientRect();
    const note = el("p", "arvuta-kulalised", "Automaatne arvutus on väljas: märkmel on muutmislink, koodi võisid muuta külalised. Vajuta ▶, kui oled koodi üle vaadanud.");
    Object.assign(note.style, { top: `${r.top + window.scrollY - 34}px`, left: `${r.left + window.scrollX}px`, width: `${r.width}px` });
    parts.push(note);
  }
  // Editors of cells that are hidden now, or beyond the last cell.
  const visible = list.filter((ch) => ch.cell).length;
  for (let i = 0; i < c.editors.length; i++) {
    const cell = list.filter((ch) => ch.cell)[i];
    if (c.editors[i] && (i >= visible || cell?.lang === "arvuta-peida")) {
      c.editors[i].destroy();
      c.editors[i] = null;
    }
  }
  c.editors.length = Math.min(c.editors.length, visible);
  c.layer.replaceChildren(...parts);
}

/** Cell k's CodeMirror editor over its Muya block: made once, then laid over the block's code, the
 *  block's code area as high as the editor, and a co-editor's change pulled in. */
function codeEditor(k, ch, state, cellNode) {
  const c = current;
  const code = cellNode.querySelector(".mu-codeblock-content");
  if (ch.lang === "arvuta-peida" || !code) return;
  let e = c.editors[k];
  if (!e) {
    const host = el("div", "arvuta-cm-host");
    c.cmLayer.append(host);
    e = c.cm.cellEditor(host, String(state[ch.start].text ?? ""), {
      editor: c.editor,
      index: () => chunks(c.editor.blocks()).filter((x) => x.cell)[k]?.start ?? -1,
      before: () => {
        const cells = chunks(c.editor.blocks()).filter((x) => x.cell).slice(0, k);
        const blocks = c.editor.blocks();
        return cells.map((x) => String(blocks[x.start].text ?? ""));
      },
      tables: () => columnNames(c.editor.getMarkdown() || ""),
      keys: {
        run: (moveOn) => runFrom(k, moveOn),
        leave: (step) => {
          const now = chunks(c.editor.blocks()).filter((x) => x.cell)[k];
          if (!now) return false;
          const target = step < 0 ? now.start - 1 : now.start + now.count;
          if (target < 0 || target >= c.editor.blocks().length) return false;
          c.editor.focusBlock(target);
          return true;
        },
      },
    });
    const destroy = e.destroy;
    e.destroy = () => {
      c.resized.unobserve(host);
      destroy();
      host.remove();
      code.style.height = "";
    };
    e.host = host;
    c.resized.observe(host);
    c.editors[k] = e;
  }
  e.pull(String(state[ch.start].text ?? ""));
  cellNode.classList.add("arvuta-cm");
  // The block's own code area keeps the place in the page; the editor lies on it.
  code.style.height = `${e.host.offsetHeight}px`;
  const r = code.getBoundingClientRect();
  Object.assign(e.host.style, { top: `${r.top + window.scrollY}px`, left: `${r.left + window.scrollX}px`, width: `${r.width}px` });
}

/** The column names of the note's tables, for completion after t[". */
function columnNames(markdown) {
  const lines = markdown.split("\n");
  const names = new Set();
  for (let i = 0; i + 1 < lines.length; i++) {
    if (!lines[i].includes("|") || !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) continue;
    for (const cell of lines[i].trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/)) {
      const name = cell.trim().replace(/\\\|/g, "|");
      if (name) names.add(name);
    }
  }
  return [...names];
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, title, onclick) {
  const b = Object.assign(el("button", null, text), { type: "button", title });
  b.setAttribute("aria-label", title);
  b.addEventListener("mousedown", (e) => e.preventDefault()); // the caret stays where it was
  b.addEventListener("click", onclick);
  return b;
}

function toolbar(block, k, hidden, first) {
  const c = current;
  const r = block.getBoundingClientRect();
  const bar = el("div", "arvuta-riba");
  Object.assign(bar.style, { top: `${r.top + window.scrollY - 12}px`, left: `${r.right + window.scrollX}px` });
  const s = c.status[k];
  const number = el("span", "arvuta-nr", c.running ? "[*]" : c.counts[k] ? `[${c.counts[k]}]` : "[ ]");
  number.title = "Mitmes käivitus selle lehe avamisest";
  bar.append(
    number,
    button("▶", "Käivita see ja eelmised plokid (Ctrl+Enter; värskendab ka Mouseri hinnad)", () => confirmGuests() && run(k)),
    button("⏩", "Käivita kõik plokid", () => confirmGuests() && run("all")),
    button("↑", "Liiguta plokk üles", () => moveCell(k, -1)),
    button("↓", "Liiguta plokk alla", () => moveCell(k, 1)),
    button("✕", "Kustuta plokk koos väljundiga", () => deleteCell(k)),
    button(hidden ? "Näita koodi" : "Peida kood", hidden ? "Näita koodi" : "Näita ainult väljundit", () => toggleHidden(k)),
  );
  if (first) {
    const auto = button(c.auto ? "auto: sees" : "auto: väljas", c.auto ? "Lülita automaatne arvutus välja" : "Lülita automaatne arvutus sisse", () => setAuto(!c.auto));
    auto.classList.add("arvuta-auto");
    auto.setAttribute("aria-pressed", String(c.auto));
    bar.append(auto);
  }
  const state = el("span", "arvuta-olek");
  if (c.running) state.textContent = "arvutab…";
  else if (s) {
    state.textContent = s.ok ? `✓ ${String(s.seconds ?? "").replace(".", ",")} s` : "✗ viga";
    state.dataset.ok = String(s.ok);
    if (s.error) state.title = s.error;
  }
  bar.append(state);
  return bar;
}

function plusRow(after, at) {
  const r = after.getBoundingClientRect();
  const row = el("div", "arvuta-pluss");
  Object.assign(row.style, { top: `${r.bottom + window.scrollY + 2}px`, left: `${r.left + window.scrollX}px`, width: `${r.width}px` });
  row.append(button("+ Kood", "Uus koodiplokk siia", () => addBlock(at, "kood")), button("+ Tekst", "Uus tekstilõik siia", () => addBlock(at, "tekst")));
  return row;
}

function foldButton({ node, lines }, k) {
  const r = node.getBoundingClientRect();
  const b = button(`Näita kõike (${lines} rida)`, "Näita kogu väljundit", () => {
    current.unfolded.add(k);
    decorate();
  });
  b.classList.add("arvuta-volt");
  Object.assign(b.style, { top: `${r.bottom + window.scrollY - 30}px`, left: `${r.left + window.scrollX + 8}px` });
  return b;
}


// --- Templates: "Tee märge sellest mallist" on a note in Mallid/ --------------------------------
registerMetaAction("mall", {
  order: 15,
  mount(el, ctx) {
    const folder = (ctx.note?.folder || "").split("/")[0].toLowerCase();
    if (folder !== "mallid" || !isOwner()) return;
    const b = Object.assign(document.createElement("button"), { type: "button", className: "primary", textContent: "Tee märge sellest mallist" });
    b.addEventListener("click", () => fromTemplate(ctx.note.slug));
    el.append(b);
  },
});

async function fromTemplate(slug) {
  let defaults;
  try {
    defaults = await api(`/api/mallid/${encodeURIComponent(slug)}`);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const dlg = document.createElement("dialog");
  dlg.className = "mall-dialog";
  dlg.innerHTML = `<form method="dialog"><h2>Uus märge mallist</h2>
    <label>Pealkiri <input name="title" required maxlength="200" autocomplete="off"></label>
    <label>Kaust <input name="folder" maxlength="200" autocomplete="off" placeholder="nt tellimused"></label>
    <p class="mall-viga" role="alert" hidden></p>
    <div class="mall-nupud"><button type="button" value="loobu">Loobu</button><button class="primary" value="tee">Tee märge</button></div></form>`;
  const form = dlg.querySelector("form");
  form.title.value = defaults.title;
  form.folder.value = defaults.folder;
  dlg.querySelector('[value="loobu"]').addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", () => dlg.remove());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const error = dlg.querySelector(".mall-viga");
    try {
      const note = await api("/api/notes/from-template", { method: "POST", body: { template: slug, title: form.title.value, folder: form.folder.value } });
      location.href = `/m/${encodeURIComponent(note.slug)}`;
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
  document.body.append(dlg);
  dlg.showModal();
  form.title.select();
}
