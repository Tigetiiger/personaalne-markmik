// WP-F: the Abiline panel (chat about the open note, streamed), the same chat on / as the home
// chat (docs/KODU-PLAN.md: pictures, reminders, proposals for any note), the ✦ action in the note's
// metadata row, and inline AI in the editor (tools/editor/inline-ai.js, loaded with the editor).
// The model's text is only ever put in the page as text nodes (renderMarkdown builds elements).
import { api, emit, muyaUrl, on, registerMetaAction, registerPanel, toast } from "/static/app.js";
import { review } from "/static/ai/review.js";
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
  loo_ulesanne: "Lisas ülesande päevikusse",
  meeldetuletused: "Vaatas meeldetuletusi",
  WebSearch: "Otsis veebist",
};
// Tools whose result is a card of its own (an event, a reminder, a proposal), not a tool line.
const CARD_TOOLS = new Set(["loo_sundmus", "muuda_markmet", "lisa_markmesse", "loo_meeldetuletus", "muuda_meeldetuletust"]);
const PICTURES_PER_MESSAGE = 4;

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
  if (inline && current.editor === editor) reviewAsked(note);
});

// /m/<slug>?ettepanek=<id>: a home chat proposal for a note that was open in another window is
// reviewed here, against the real editor (co-editors see it, Ctrl+Z undoes it).
async function reviewAsked(note) {
  const params = new URLSearchParams(location.search);
  const id = params.get("ettepanek");
  if (!/^\d+$/.test(id || "")) return;
  params.delete("ettepanek");
  history.replaceState(history.state, "", location.pathname + (params.size ? `?${params}` : ""));
  try {
    const p = await api(`/api/ai/proposals/${id}`);
    if (p.slug !== note.slug || p.settled) return;
    const out = await inline.review(p.base, p.markdown);
    if (!out) return;
    const settled = out.applied ? "applied" : out.skipped ? "failed" : "dropped";
    await api(`/api/ai/proposals/${id}`, { method: "POST", body: { settled } });
  } catch (err) {
    toast(err.message, "error");
  }
}

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

function toolCard(name, input, result) {
  const li = el("li", null, "ai-card ai-tool");
  const arg = input && (input.query || input.slug || input.title || input.text || (input.start && `${input.start}–${input.end}`));
  li.append(el("span", TOOL_LABELS[name] || name, "ai-card-label"));
  const made = name === "loo_markme" && parsed(result);
  if (made?.slug) {
    const a = el("a", made.title || made.slug);
    a.href = `/m/${encodeURIComponent(made.slug)}`;
    li.append(": ", a);
  } else if (arg) li.append(el("span", `: ${arg}`));
  return li;
}

function parsed(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function reminderCard(r) {
  const li = el("li", null, "ai-card ai-event ai-reminder");
  li.append(el("span", r.done ? "Meeldetuletus (tehtud)" : "Meeldetuletus telefoni", "ai-card-label"), el("b", `🔔 ${r.text}`), el("span", formatStart(r.remind_at)));
  const remove = el("button", "Eemalda");
  remove.type = "button";
  remove.addEventListener("click", async () => {
    remove.disabled = true;
    try {
      await api(`/api/reminders/${r.id}`, { method: "DELETE" });
      li.classList.add("removed");
      remove.replaceWith(el("span", "Eemaldatud", "ai-card-label"));
      emit("events:changed", {});
    } catch (err) {
      remove.disabled = false;
      toast(err.status === 404 ? "Juba eemaldatud." : err.message, "error");
    }
  });
  li.append(remove);
  return li;
}

const SETTLED = { applied: "Rakendatud", dropped: "Loobutud", failed: "Ei saanud rakendada" };

/** The whole new note the assistant proposes for the open note (muuda_markmet), against the text
 *  the question was asked about (ev.base). "Vaata üle" shows the changes in the review window
 *  until the owner accepts them; what is kept goes in through the editor as ordinary edits:
 *  co-editors see it, Ctrl+Z undoes it. How it ended is stored with the proposal (ev.id), so a
 *  reload offers only the ones still open. Returns {li, open(auto)}. */
function proposalCard(ev) {
  if (ev.rev !== undefined) return homeProposalCard(ev);
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

/** A home chat proposal for any note (docs/KODU-PLAN.md): reviewed here, against the note as the
 *  server has it now (the window applies the kept changes onto that), and written by the server
 *  if the note did not change meanwhile. A note open in an editor is reviewed on its own page. */
function homeProposalCard(ev) {
  const li = el("li", null, "ai-card ai-proposal");
  const link = el("a", ev.title || ev.slug);
  link.href = `/m/${encodeURIComponent(ev.slug)}`;
  li.append(el("span", "Muudatusettepanek:", "ai-card-label"), link);
  if (ev.settled) {
    li.append(el("span", SETTLED[ev.settled], "ai-card-label"));
    return { li, open() {} };
  }
  const view = el("button", "Vaata üle", "primary");
  view.type = "button";
  async function open() {
    view.disabled = true;
    try {
      const settled = await reviewOnServer(ev);
      if (settled === "busy") {
        const go = el("a", "Ava märge ja vaata seal üle", "button primary");
        go.href = `/m/${encodeURIComponent(ev.slug)}?ettepanek=${ev.id}`;
        view.replaceWith(go);
        toast("Märge on teises aknas lahti: vaata muudatus üle seal.");
      } else if (settled) {
        view.replaceWith(el("span", SETTLED[settled], "ai-card-label"));
        if (settled !== "applied") await api(`/api/ai/proposals/${ev.id}`, { method: "POST", body: { settled } });
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      view.disabled = false;
    }
  }
  view.addEventListener("click", () => open());
  li.append(view);
  return { li, open };
}

/** How the review of a home proposal ended: "applied" | "dropped" | "failed" | "busy" (the note
 *  is open in an editor). After a change meanwhile the window opens again on the new text. */
async function reviewOnServer(ev) {
  for (;;) {
    const note = await api(`/api/notes/${encodeURIComponent(ev.slug)}`);
    let out = null;
    const editor = { getMarkdown: () => note.body, replaceMarkdown: (md) => { out = md; } };
    const result = await review({ base: ev.base, markdown: ev.markdown, editor });
    if (!result) return "dropped";
    if (out === null) return result.skipped ? "failed" : "dropped";
    try {
      await api(`/api/ai/proposals/${ev.id}/apply`, { method: "POST", body: { markdown: out, rev: note.rev } });
      toast("Märge muudetud.");
      return "applied";
    } catch (err) {
      if (err.status === 409 && err.data?.reason === "lahti") return "busy";
      if (err.status !== 409) throw err;
      toast("Märget muudeti vahepeal: vaata muudatus uuesti üle.");
    }
  }
}

// --- Pictures for the home chat -----------------------------------------------------------------
async function uploadPicture(file) {
  const body = new FormData();
  body.append("file[]", file, file.name || "pilt.png");
  const data = await api("/pildid", { method: "POST", body });
  const src = Object.values(data.data.succMap)[0];
  if (!src) throw new Error(`Seda faili ei saa lisada: ${file.name || "pilt"}`);
  return src.split("/").pop();
}

function thumb(name, onClick, title) {
  const b = el("button", null, "ai-thumb");
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", title);
  const img = el("img");
  img.src = `/pildid/${name}`;
  img.alt = "";
  b.append(img);
  b.addEventListener("click", onClick);
  return b;
}

// [label, text put in the composer, open the picture picker]
const SUGGESTIONS = [
  ["Tee pildist märge", "Tee sellest pildist märge.", true],
  ["Mis mul täna on?", "Mis mul täna ja homme kalendris on?", false],
  ["Tuleta mulle meelde…", "Tuleta mulle meelde ", false],
];

function mountPanel(root, ctx) {
  const home = Boolean(ctx.home);
  let note = home ? null : ctx.note || current.note;
  let busy = null;
  let loaded = true; // the thread is on screen: a message may be sent
  root.classList.add("ai-panel");
  const thread = el("ol", null, "ai-thread");
  thread.setAttribute("aria-live", "polite");
  const empty = el("p", home ? "Küsi midagi, lisa pilt või palu midagi meelde tuletada." : "Küsi midagi selle märkme, kalendri või oma märkmete kohta.", "ai-empty");
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

  // Home only: pictures, suggestions and "Uus vestlus".
  let pending = []; // picture names to send with the next message
  const tray = el("div", null, "ai-tray");
  const picker = el("input");
  Object.assign(picker, { type: "file", accept: "image/*", multiple: true, hidden: true });
  picker.setAttribute("aria-label", "Lisa pilt");
  const attach = el("button", "📎", "ai-attach");
  attach.type = "button";
  attach.title = "Lisa pilt (või kleebi / lohista)";
  attach.setAttribute("aria-label", "Lisa pilt");
  const chips = el("div", null, "ai-suggest");
  if (home) {
    root.classList.add("ai-home");
    guests.remove(); // the note panel's: there is no open note here
    chip.remove();
    input.placeholder = "Kirjuta või lisa pilt…";
    const fresh = el("button", "Uus vestlus", "ai-new");
    fresh.type = "button";
    fresh.addEventListener("click", () => {
      if (busy || !loaded) return;
      pending = [];
      renderTray();
      load(() => api("/api/ai/threads", { method: "POST" }));
    });
    const actions = el("span", null, "crumb-actions");
    actions.append(fresh);
    const head = el("div", null, "crumb ai-home-head");
    head.append(el("span", "Abiline"), actions);
    for (const [label, text, pick] of SUGGESTIONS) {
      const b = el("button", label, "ai-chip");
      b.type = "button";
      b.addEventListener("click", () => {
        input.value = text;
        input.focus();
        if (pick) picker.click();
      });
      chips.append(b);
    }
    row.prepend(attach);
    form.prepend(tray);
    form.append(picker);
    root.prepend(head);
    empty.after(chips);
    attach.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
      addFiles([...picker.files]);
      picker.value = "";
    });
    const images = (list) => [...(list || [])].filter((f) => f.type.startsWith("image/"));
    input.addEventListener("paste", (e) => {
      const files = images(e.clipboardData?.files);
      if (!files.length) return;
      e.preventDefault();
      addFiles(files);
    });
    form.addEventListener("dragover", (e) => e.preventDefault());
    form.addEventListener("drop", (e) => {
      const files = images(e.dataTransfer?.files);
      if (!files.length) return;
      e.preventDefault();
      addFiles(files);
    });
  }

  function renderTray() {
    tray.replaceChildren(...pending.map((name) => thumb(name, () => {
      pending = pending.filter((n) => n !== name);
      renderTray();
    }, "Eemalda pilt")));
    tray.hidden = !pending.length;
  }
  renderTray();

  function addPending(name) {
    if (pending.includes(name)) return true;
    if (pending.length >= PICTURES_PER_MESSAGE) {
      toast(`Ühe sõnumiga saab saata kuni ${PICTURES_PER_MESSAGE} pilti.`, "error");
      return false;
    }
    pending = [...pending, name];
    renderTray();
    return true;
  }

  async function addFiles(files) {
    attach.disabled = true;
    try {
      for (const file of files) {
        if (pending.length >= PICTURES_PER_MESSAGE) return addPending(null);
        addPending(await uploadPicture(file));
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      attach.disabled = false;
    }
  }

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
    chips.hidden = true;
    node.scrollIntoView({ block: "nearest" });
    return node;
  }
  const bubble = (role, text, pictures = []) => {
    const li = el("li", null, `ai-msg ai-${role}`);
    if (role === "assistant") li.append(renderMarkdown(text));
    else li.textContent = text;
    if (pictures?.length) {
      const strip = el("div", null, "ai-pictures");
      strip.append(...pictures.map((name) => thumb(name, () => addPending(name), "Lisa see pilt uuesti sõnumisse")));
      li.append(strip);
    }
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

  let loading = 0;
  async function load(reset) {
    const gen = ++loading;
    thread.replaceChildren();
    empty.hidden = false;
    chips.hidden = false;
    checkGuests();
    if (!note && !home) return;
    const slug = note?.slug;
    // A message sent now would land in the thread being replaced.
    loaded = false;
    send.disabled = true;
    try {
      if (reset) await reset();
      const messages = await api(home ? "/api/ai/threads" : `/api/ai/threads/${encodeURIComponent(slug)}`);
      if (note?.slug !== slug || gen !== loading) return;
      for (const m of messages) {
        if (m.role !== "tool") add(bubble(m.role, m.text, m.images));
        else if (m.event) add(eventCard(m.event));
        else if (m.reminder) add(reminderCard(m.reminder));
        else if ((m.name === "muuda_markmet" || m.name === "lisa_markmesse") && m.base !== undefined) add(proposalCard({ ...m, slug: m.slug || slug, markdown: m.proposal }).li);
        // As while streaming: no card for a refused event, reminder or proposal. A note's thread
        // may hold lisa_markmesse lines from before muuda_markmet, without a proposal.
        else if (!CARD_TOOLS.has(m.name) || (m.name === "lisa_markmesse" && !home)) add(toolCard(m.name, m.input, m.result));
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      if (gen === loading) {
        loaded = true;
        send.disabled = Boolean(busy);
      }
    }
  }

  async function ask(message) {
    const selection = chosen;
    chosen = "";
    chip.setAttribute("aria-pressed", "false");
    chip.hidden = !selectionText;
    const pictures = pending;
    pending = [];
    renderTray();
    add(bubble("user", message, pictures));
    const answer = add(el("li", null, "ai-msg ai-assistant ai-streaming"));
    let text = "";
    let proposal = null;
    busy = new AbortController();
    send.disabled = true;
    const before = (node) => thread.insertBefore(node, answer);
    // The editor's text, typing not yet saved included: what the model edits and the review's base.
    const markdown = current.editor && current.note?.slug === note?.slug ? current.editor.getMarkdown() : undefined;
    try {
      const body = home ? { message, images: pictures } : { slug: note?.slug, message, selection, markdown };
      await streamAi("/api/ai/chat", body, (ev) => {
        if (ev.type === "delta") {
          text += ev.text;
          answer.textContent = text;
        } else if (ev.type === "tool" && !CARD_TOOLS.has(ev.name)) before(toolCard(ev.name, ev.input, ev.result));
        else if (ev.type === "event_created") {
          before(eventCard(ev));
          emit("events:changed", {});
        } else if (ev.type === "reminder_created") {
          before(reminderCard(ev));
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
        if (pictures.length && !pending.length) {
          pending = pictures; // not sent: they wait in the tray for the next try
          renderTray();
        }
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
    if (!message || busy || !loaded) return;
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
    if (home || next?.slug === note?.slug) return;
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

// The home chat: the note page shows it on / in place of the empty state (site/shell/note.js).
// "/" focuses its composer when nothing else is being typed in.
let homeCleanup = null;
on("home:show", ({ el: root }) => {
  homeCleanup?.();
  const cleanup = mountPanel(root, { home: true });
  const focus = (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea, select, [contenteditable], dialog")) return;
    e.preventDefault();
    root.querySelector(".ai-form textarea")?.focus();
  };
  document.addEventListener("keydown", focus);
  homeCleanup = () => {
    cleanup();
    document.removeEventListener("keydown", focus);
  };
});
on("home:hide", () => {
  homeCleanup?.();
  homeCleanup = null;
});

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
