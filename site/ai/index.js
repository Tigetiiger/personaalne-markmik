// WP-F: the Abiline panel (chat about the open note, streamed), the ✦ action in the note's
// metadata row, and inline AI in the editor (tools/editor/inline-ai.js, loaded with the editor).
// The model's text is only ever put in the page as text nodes (renderMarkdown builds elements).
import { api, emit, muyaUrl, on, registerMetaAction, registerPanel, toast } from "/static/app.js";
import { streamAi } from "/static/ai/sse.js";

const TOOL_LABELS = {
  otsi_markmeid: "Otsis märkmetest",
  loe_markmet: "Luges märget",
  loo_markme: "Lõi märkme",
  lisa_markmesse: "Pakkus teksti märkmesse", // in threads from before muuda_markmet
  muuda_markmet: "Pakkus muudatust märkmele",
  sundmused: "Vaatas kalendrit",
  loo_sundmus: "Lisas kalendrisse",
  ulesanded: "Vaatas ülesandeid",
  aju_otsing: "Otsis ajust",
  WebSearch: "Otsis veebist",
};

// Shown while the note has an active edit link (the server tells the model the same).
const GUESTS_WARNING = "Seda märget saavad muuta ka lingiga külalised. Ära lase AI-l täita märkmes olevaid juhiseid pimesi.";

let current = { note: null, editor: null }; // the note open in this page's editor
let inline = null; // inline AI for that editor
let selectionText = ""; // what is selected in the editor, as the panel's "Kasuta valikut" offers it
const selectionListeners = new Set();

// Inline AI is the owner's: share-link guests see no top navigation and have no AI.
const isOwner = () => Boolean(document.getElementById("nav"));

on("editor:ready", async ({ note, editor }) => {
  current = { note, editor };
  const root = document.querySelector(".mt"); // one editor per page (docs/CONTRACTS.md §6)
  if (!root || !isOwner()) return;
  try {
    const { attachInlineAi } = await import(`${muyaUrl()}/inline-ai.js`);
    if (current.editor === editor) inline = attachInlineAi(root, { editor, note });
  } catch (err) {
    console.warn("inline AI not loaded", err);
  }
});

document.addEventListener("selectionchange", () => {
  const sel = document.getSelection();
  const root = document.querySelector(".mt");
  if (!sel || !root || !root.contains(sel.anchorNode)) return; // selecting in the panel keeps the last one
  const text = sel.isCollapsed ? "" : sel.toString().trim();
  if (text === selectionText) return;
  selectionText = text;
  for (const fn of selectionListeners) fn(text);
});

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

// --- A safe Markdown subset -------------------------------------------------------------------
const LIST_RE = /^\s*([-*+]|\d+[.)])\s+/;
const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*)/g;

function renderInline(parent, text) {
  let last = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    parent.append(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) parent.append(el("strong", t.slice(2, -2)));
    else if (t.startsWith("`")) parent.append(el("code", t.slice(1, -1)));
    else if (t.startsWith("[")) {
      const [, label, href] = t.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      // Web links, and paths on this site ("//x" and "/\x" would leave it).
      if (/^(https?:\/\/|\/(?![/\\]))/i.test(href)) {
        const a = el("a", label);
        a.href = href;
        if (!href.startsWith("/")) Object.assign(a, { target: "_blank", rel: "noopener noreferrer" });
        parent.append(a);
      } else parent.append(label);
    } else parent.append(el("em", t.slice(1, -1)));
    last = m.index + t.length;
  }
  parent.append(text.slice(last));
}

/** Paragraphs, headings (as bold lines), lists, code blocks, **bold**, *italic*, `code`, links. */
export function renderMarkdown(text) {
  const out = document.createDocumentFragment();
  const lines = text.replace(/\r/g, "").split("\n");
  for (let i = 0; i < lines.length; ) {
    const line = lines[i];
    if (!line.trim()) i++;
    else if (line.startsWith("```")) {
      const code = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.appendChild(el("pre")).append(el("code", code.join("\n")));
    } else if (/^#{1,6}\s/.test(line)) {
      const p = out.appendChild(el("p"));
      renderInline(p.appendChild(el("strong")), line.replace(/^#+\s*/, ""));
      i++;
    } else if (LIST_RE.test(line)) {
      const list = out.appendChild(el(/^\s*\d/.test(line) ? "ol" : "ul"));
      for (; i < lines.length && LIST_RE.test(lines[i]); i++) renderInline(list.appendChild(el("li")), lines[i].replace(LIST_RE, ""));
    } else {
      const p = out.appendChild(el("p"));
      for (let first = true; i < lines.length && lines[i].trim() && !LIST_RE.test(lines[i]) && !/^(```|#{1,6}\s)/.test(lines[i]); i++, first = false) {
        if (!first) p.append(el("br"));
        renderInline(p, lines[i]);
      }
    }
  }
  return out;
}

// --- Abiline panel ------------------------------------------------------------------------------
function formatStart(start) {
  const [day, time] = start.split("T");
  const [y, m, d] = day.split("-").map(Number);
  return `${d}.${m}.${y}` + (time ? ` kell ${time.replace(":", ".")}` : "");
}

function eventCard(ev) {
  const li = el("li", null, "ai-card ai-event");
  li.append(el("span", "Lisatud kalendrisse", "ai-card-label"), el("b", ev.title), el("span", formatStart(ev.start)));
  const remove = el("button", "Eemalda");
  remove.type = "button";
  remove.addEventListener("click", async () => {
    remove.disabled = true;
    try {
      await api(`/api/events/${ev.id}`, { method: "DELETE" });
      li.classList.add("removed");
      remove.replaceWith(el("span", "Eemaldatud", "ai-card-label"));
      emit("events:changed", {});
    } catch (err) {
      remove.disabled = false;
      toast(err.message, "error");
    }
  });
  li.append(remove);
  return li;
}

function toolCard(name, input) {
  const li = el("li", null, "ai-card ai-tool");
  const arg = input && (input.query || input.slug || input.title || (input.start && `${input.start}–${input.end}`));
  li.append(el("span", TOOL_LABELS[name] || name, "ai-card-label"));
  if (arg) li.append(el("span", `: ${arg}`));
  return li;
}

const SETTLED = { applied: "Rakendatud", dropped: "Loobutud", failed: "Ei saanud rakendada" };

/** The whole new note the assistant proposes for the open note (muuda_markmet), against the text
 *  the question was asked about (ev.base). "Vaata üle" shows the changes in the review window
 *  until the owner accepts them; what is kept goes in through the editor as ordinary edits:
 *  co-editors see it, Ctrl+Z undoes it. How it ended is stored with the proposal (ev.id), so a
 *  reload offers only the ones still open. Returns {li, open(auto)}. */
function proposalCard(ev) {
  const li = el("li", null, "ai-card ai-proposal");
  li.append(el("span", "Muudatusettepanek märkmele", "ai-card-label"));
  if (ev.settled) {
    li.append(el("span", SETTLED[ev.settled], "ai-card-label"));
    return { li, open() {} };
  }
  const view = el("button", "Vaata üle", "primary");
  view.type = "button";
  async function open(auto) {
    if (!inline || current.note?.slug !== ev.slug) {
      if (!auto) toast("Ava märge, et muudatus üle vaadata.", "error");
      return;
    }
    view.disabled = true;
    try {
      const out = await inline.review(ev.base, ev.markdown);
      if (!out) return;
      const settled = out.applied ? "applied" : out.skipped ? "failed" : "dropped";
      view.replaceWith(el("span", SETTLED[settled], "ai-card-label"));
      await api(`/api/ai/proposals/${ev.id}`, { method: "POST", body: { settled } }).catch((err) => toast(err.message, "error"));
    } finally {
      view.disabled = false;
    }
  }
  view.addEventListener("click", () => open(false));
  li.append(view);
  return { li, open };
}

function mountPanel(root, ctx) {
  let note = ctx.note || current.note;
  let busy = null;
  root.classList.add("ai-panel");
  const thread = el("ol", null, "ai-thread");
  thread.setAttribute("aria-live", "polite");
  const empty = el("p", "Küsi midagi selle märkme, kalendri või oma märkmete kohta.", "ai-empty");
  const guests = el("p", GUESTS_WARNING, "ai-guests");
  guests.setAttribute("role", "note");
  guests.hidden = true;
  const form = el("form", null, "ai-form");
  const chip = el("button", "Kasuta valikut", "ai-chip");
  chip.type = "button";
  chip.hidden = !selectionText;
  chip.setAttribute("aria-pressed", "false");
  let chosen = "";
  const input = el("textarea");
  Object.assign(input, { rows: 3, placeholder: "Küsi Abiliselt…" });
  input.setAttribute("aria-label", "Sõnum Abilisele");
  const send = el("button", "Saada", "primary");
  send.type = "submit";
  const row = el("div", null, "ai-form-row");
  row.append(chip, send);
  form.append(input, row);
  root.replaceChildren(guests, thread, empty, form);

  chip.addEventListener("mousedown", (e) => e.preventDefault()); // keep the editor's selection
  chip.addEventListener("click", () => {
    const on = chip.getAttribute("aria-pressed") !== "true" && Boolean(selectionText);
    chosen = on ? selectionText : "";
    chip.setAttribute("aria-pressed", String(on));
    chip.title = on ? `Valik: „${chosen.slice(0, 200)}“` : "";
  });
  const onSelection = (text) => {
    if (chip.getAttribute("aria-pressed") === "true") return;
    chip.hidden = !text;
  };
  selectionListeners.add(onSelection);

  function add(node) {
    thread.append(node);
    empty.hidden = true;
    node.scrollIntoView({ block: "nearest" });
    return node;
  }
  const bubble = (role, text) => {
    const li = el("li", null, `ai-msg ai-${role}`);
    if (role === "assistant") li.append(renderMarkdown(text));
    else li.textContent = text;
    return li;
  };

  async function checkGuests() {
    guests.hidden = true;
    if (!note) return;
    const slug = note.slug;
    const now = new Date();
    try {
      const links = await api(`/api/shares?slug=${encodeURIComponent(slug)}`);
      if (note?.slug !== slug) return;
      guests.hidden = !links.some((l) => l.mode === "edit" && (!l.expires_at || new Date(l.expires_at) > now));
    } catch (err) {
      console.warn("share links not checked", err);
    }
  }

  async function load() {
    thread.replaceChildren();
    empty.hidden = false;
    checkGuests();
    if (!note) return;
    const slug = note.slug;
    try {
      const messages = await api(`/api/ai/threads/${encodeURIComponent(slug)}`);
      if (note?.slug !== slug) return;
      for (const m of messages) {
        if (m.role !== "tool") add(bubble(m.role, m.text));
        else if (m.event) add(eventCard(m.event));
        else if (m.name === "muuda_markmet" && m.base !== undefined) add(proposalCard({ ...m, slug, markdown: m.proposal }).li);
        // As while streaming: no card for a refused event or proposal.
        else if (!{ loo_sundmus: true, lisa_markmesse: !m.proposal, muuda_markmet: !m.proposal }[m.name]) add(toolCard(m.name, m.input));
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function ask(message) {
    const selection = chosen;
    chosen = "";
    chip.setAttribute("aria-pressed", "false");
    chip.hidden = !selectionText;
    add(bubble("user", message));
    const answer = add(el("li", null, "ai-msg ai-assistant ai-streaming"));
    let text = "";
    let proposal = null;
    busy = new AbortController();
    send.disabled = true;
    const before = (node) => thread.insertBefore(node, answer);
    // The editor's text, typing not yet saved included: what the model edits and the review's base.
    const markdown = current.editor && current.note?.slug === note?.slug ? current.editor.getMarkdown() : undefined;
    try {
      await streamAi("/api/ai/chat", { slug: note?.slug, message, selection, markdown }, (ev) => {
        if (ev.type === "delta") {
          text += ev.text;
          answer.textContent = text;
        } else if (ev.type === "tool" && ev.name !== "loo_sundmus" && ev.name !== "muuda_markmet") before(toolCard(ev.name, ev.input));
        else if (ev.type === "event_created") {
          before(eventCard(ev));
          emit("events:changed", {});
        } else if (ev.type === "edit") {
          proposal = proposalCard(ev);
          before(proposal.li);
        } else if (ev.type === "error") {
          answer.classList.add("error");
          text = text ? `${text}\n\n${ev.message}` : ev.message;
          answer.textContent = text;
        }
      }, busy.signal);
    } catch (err) {
      if (err.name !== "AbortError") {
        answer.classList.add("error");
        answer.textContent = err.message;
      }
    } finally {
      busy = null;
      send.disabled = false;
      answer.classList.remove("ai-streaming");
      if (!answer.classList.contains("error")) answer.replaceChildren(renderMarkdown(text));
      proposal?.open(true); // once the answer is in; the card reopens it
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message || busy) return;
    input.value = "";
    ask(message);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  const off = on("note:open", ({ note: next }) => {
    if (next?.slug === note?.slug) return;
    busy?.abort();
    note = next;
    load();
  });
  const offShares = on("shares:changed", ({ slug }) => slug === note?.slug && checkGuests());
  load();
  return () => {
    busy?.abort();
    off();
    offShares();
    selectionListeners.delete(onSelection);
  };
}

registerPanel("assistant", { title: "Abiline", order: 30, mount: mountPanel });

// ✦ AI in the note's crumb row opens the Abiline tab (the shell's right panel); inline AI on a
// selection is the ✦ next to it and Ctrl+J.
registerMetaAction("ai", {
  order: 40,
  mount(root) {
    const b = el("button", "✦ AI", "primary ai-meta");
    b.type = "button";
    b.title = "Abiline (valitud tekstiga: Ctrl+J)";
    b.addEventListener("mousedown", (e) => e.preventDefault()); // the editor keeps its selection, for "Kasuta valikut"
    b.addEventListener("click", () => emit("panel:toggle", { id: "assistant" }));
    root.append(b);
  },
});
