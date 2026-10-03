// Python cells in notes and templates (docs/ARVUTUS-PLAN.md). A ```arvuta block runs in the
// sandbox, its output goes in a ```väljund block under it, tables and `= x` values fill in; the
// server answers with the note's new Markdown and only the blocks that differ change in the editor
// (updateMarkdown), as ordinary edits. ```arvuta-peida shows only the output. Computing runs by
// itself 800 ms after a change; ▶ also refreshes Mouser prices. Owner only: guests see the outputs
// that are in the note.
import { api, on, registerMetaAction, toast } from "/static/app.js";

const DEBOUNCE_MS = 800;
const CELL_LANGS = new Set(["arvuta", "arvuta-peida"]);
const FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)/;
const isOwner = () => Boolean(document.getElementById("nav"));

let current = null; // {note, editor, observer, timer, hash, layer, running, status, guests}

/** The cell fences in Markdown, as {line, lang}; same rules as app/arvuta/cells.py. */
export function cellFences(markdown) {
  const lines = markdown.split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(FENCE);
    if (!m) continue;
    const mark = m[1];
    let end = i + 1;
    while (end < lines.length && !(lines[end].trim().startsWith(mark[0].repeat(mark.length)) && /^[`~]+$/.test(lines[end].trim()) && lines[end].trim()[0] === mark[0])) end++;
    if (CELL_LANGS.has(m[2].toLowerCase())) out.push({ line: i, lang: m[2].toLowerCase() });
    i = end;
  }
  return out;
}

const hasWork = (md) => /^ {0,3}(`{3,}|~{3,})[ \t]*arvuta/m.test(md) || /`=[^`]/.test(md);

on("editor:ready", ({ note, editor }) => {
  stop();
  if (!isOwner() || !editor) return;
  const root = document.querySelector("#editor");
  if (!root) return;
  current = { note, editor, root, timer: null, hash: null, running: false, status: [], guests: false, error: null };
  current.layer = Object.assign(document.createElement("div"), { className: "arvuta-layer" });
  document.body.append(current.layer);
  const mine = current;
  current.observer = new MutationObserver(() => {
    if (current !== mine) return;
    decorate();
    schedule();
  });
  current.observer.observe(root, { childList: true, subtree: true, characterData: true });
  current.resize = () => current === mine && decorate();
  window.addEventListener("resize", current.resize);
  decorate();
  schedule();
});
on("note:open", ({ note }) => current && note?.slug !== current.note.slug && stop());
on("home:show", () => stop());

function stop() {
  if (!current) return;
  clearTimeout(current.timer);
  current.observer?.disconnect();
  window.removeEventListener("resize", current.resize);
  current.layer?.remove();
  current = null;
}

function schedule() {
  clearTimeout(current.timer);
  const mine = current;
  current.timer = setTimeout(() => current === mine && run("auto"), DEBOUNCE_MS);
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
    c.error = out.error || null;
    if (out.error) toast(out.error, "error");
    c.status = out.cells || [];
    c.seconds = out.seconds;
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

/** Code shown or hidden: the cell's language switches between arvuta and arvuta-peida. */
function toggleHidden(index) {
  const c = current;
  const markdown = c.editor.getMarkdown();
  const cell = cellFences(markdown)[index];
  if (!cell) return;
  const lines = markdown.split("\n");
  const lang = cell.lang === "arvuta" ? "arvuta-peida" : "arvuta";
  lines[cell.line] = lines[cell.line].replace(/(`{3,}|~{3,})[ \t]*[^\s`]*/, (m, fence) => fence + lang);
  c.editor.updateMarkdown(lines.join("\n"));
}

function langOf(block) {
  return (block.querySelector(".mu-language-input")?.textContent || "").trim().toLowerCase();
}

/** Classes on Muya's code blocks (cells, hidden cells, outputs) and a toolbar over each cell. */
function decorate() {
  const c = current;
  if (!c) return;
  const blocks = [...c.root.querySelectorAll(".mu-code-block")].filter((b) => !b.parentElement.closest(".mu-code-block"));
  const cells = [];
  for (const b of blocks) {
    const lang = langOf(b);
    b.classList.toggle("arvuta-rakk", CELL_LANGS.has(lang));
    b.classList.toggle("arvuta-peidus", lang === "arvuta-peida");
    b.classList.toggle("arvuta-valjund", lang === "väljund");
    if (CELL_LANGS.has(lang)) cells.push({ block: b, hidden: lang === "arvuta-peida" });
  }
  const bars = cells.map(({ block, hidden }, i) => toolbar(block, i, hidden));
  c.layer.replaceChildren(...bars);
  if (c.guests && cells.length) {
    const note = Object.assign(document.createElement("p"), { className: "arvuta-kulalised", textContent: "Automaatne arvutus on väljas: märkmel on muutmislink, koodi võisid muuta külalised. Vajuta ▶, kui oled koodi üle vaadanud." });
    const r = cells[0].block.getBoundingClientRect();
    Object.assign(note.style, { top: `${r.top + window.scrollY - 34}px`, left: `${r.left + window.scrollX}px`, width: `${r.width}px` });
    c.layer.append(note);
  }
}

function toolbar(block, index, hidden) {
  const c = current;
  const r = block.getBoundingClientRect();
  const bar = document.createElement("div");
  bar.className = "arvuta-riba";
  Object.assign(bar.style, { top: `${r.top + window.scrollY - 12}px`, left: `${r.right + window.scrollX}px` });
  const button = (text, title, onclick) => {
    const b = Object.assign(document.createElement("button"), { type: "button", textContent: text, title });
    b.setAttribute("aria-label", title);
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", onclick);
    return b;
  };
  const confirmGuests = () => !c.guests || window.confirm("Koodi võisid muuta lingiga külalised. Kas käivitan selle?");
  bar.append(
    button("▶", "Käivita see ja eelmised plokid (värskendab ka Mouseri hinnad)", () => confirmGuests() && run(index)),
    button("⏩", "Käivita kõik plokid", () => confirmGuests() && run("all")),
    button(hidden ? "Näita koodi" : "Peida kood", hidden ? "Näita koodi" : "Näita ainult väljundit", () => toggleHidden(index)),
  );
  const s = c.status[index];
  const state = Object.assign(document.createElement("span"), { className: "arvuta-olek" });
  if (c.running) state.textContent = "arvutab…";
  else if (s) {
    state.textContent = s.ok ? `✓ ${String(c.seconds ?? "").replace(".", ",")} s` : "✗ viga";
    state.dataset.ok = String(s.ok);
    if (s.error) state.title = s.error;
  }
  bar.append(state);
  return bar;
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
