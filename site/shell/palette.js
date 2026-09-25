// Command palette (Ctrl+K): jump to a note, run a command, or add something ("Lisa: ...") through
// quick capture — text starting with ">" always, other text when /api/capture recognises a date
// or a task in it.
import { api, emit, listCommands, registerCommand, toast } from "/static/app.js";
import { debounce, h } from "./dom.js";
import { go, newNote } from "./note.js";

const WEEKDAY = ["P", "E", "T", "K", "N", "R", "L"];

registerCommand("uus-marge", { title: "Uus märge", keywords: "loo lisa new", hint: "Alt+N", run: newNote });
for (const [id, title, path, keywords] of [
  ["tana", "Täna", "/tana", "today paevik agenda"],
  ["kalender", "Kalender", "/kalender", "calendar sundmused"],
  ["graaf", "Graaf", "/graaf", "graph lingid"],
  ["postkast", "Postkast", "/postkast", "inbox pakutud"],
  ["seaded", "Seaded", "/seaded", "settings uhendused"],
]) {
  registerCommand(id, { title, keywords, run: () => (location.href = path) });
}
registerCommand("teema", { title: "Vaheta teema", keywords: "hele tume dark light", run: () => document.getElementById("teema")?.click() });

const fold = (s) => s.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
/** Every word typed starts a word of the command's title or keywords. */
const matches = (words, command) => {
  const own = fold(`${command.title} ${command.keywords || ""}`).split(/\s+/);
  return words.every((w) => own.some((o) => o.startsWith(w)));
};

let dialog = null;

export function openPalette() {
  if (!dialog) dialog = build();
  if (dialog.el.open) return;
  dialog.input.value = "";
  dialog.el.showModal();
  dialog.refresh();
}

function build() {
  const input = h("input", { type: "text", class: "palette-input", placeholder: "Otsi märget või käsku; > lisab kiirmärke", "aria-label": "Otsi märget või käsku", role: "combobox", "aria-controls": "palette-list", "aria-expanded": "true", autocomplete: "off" });
  const list = h("ul", { class: "palette-list", id: "palette-list", role: "listbox" });
  const el = h("dialog", { class: "palette", "aria-label": "Käsud" }, input, list,
    h("p", { class: "palette-hint" }, "↑↓ vali · Enter ava · Esc sulge"));
  document.body.append(el);
  let items = [];
  let active = 0;
  let gen = 0;
  let waiting = false; // typed, list not caught up yet
  let enterQueued = false;

  const draw = () => {
    list.replaceChildren(
      ...items.map((item, i) =>
        h("li", { role: "option", id: `palette-${i}`, class: `palette-item ${item.kind}`, "aria-selected": String(i === active), onmousedown: (e) => e.preventDefault(), onclick: () => run(item) },
          h("span", { class: "palette-label" }, item.label), item.hint ? h("span", { class: "palette-hint-inline" }, item.hint) : null)),
    );
    if (!items.length) list.append(h("li", { class: "palette-empty" }, "Midagi ei leitud."));
    input.setAttribute("aria-activedescendant", items.length ? `palette-${active}` : "");
    list.children[active]?.scrollIntoView({ block: "nearest" });
  };

  const update = debounce(async () => {
    const mine = ++gen;
    const text = input.value.trim();
    if (text.startsWith(">")) {
      const rest = text.slice(1).trim();
      const found = rest ? await preview(rest) : null;
      if (mine !== gen) return;
      items = rest ? [captureItem(rest, found)] : [];
    } else {
      // Notes and commands the text names come first ("täna" + Enter is the Täna page, not an
      // event today); quick capture of a recognised date or task comes after them.
      const words = fold(text).split(/\s+/).filter(Boolean);
      const commands = listCommands()
        .filter((c) => matches(words, c))
        .map((c) => ({ kind: "command", label: c.title, hint: c.hint || "käsk", run: c.run, top: Boolean(text) && fold(c.title).startsWith(fold(text)) }));
      const [notes, found] = await Promise.all([
        api(`/api/notes/suggest?q=${encodeURIComponent(text)}`).catch(() => []),
        text ? preview(text) : null,
      ]);
      if (mine !== gen) return;
      items = [
        ...commands.filter((c) => c.top),
        ...notes.map((n) => ({ kind: "note", label: n.title, hint: n.folder, run: () => go(n.slug) })),
        ...commands.filter((c) => !c.top),
        ...(found && found.kind !== "note" ? [captureItem(text, found)] : []),
      ];
    }
    active = 0;
    waiting = false;
    draw();
    if (enterQueued) {
      enterQueued = false;
      if (items[0]) run(items[0]);
    }
  }, 120);
  const refresh = () => {
    waiting = true;
    update();
  };

  function run(item) {
    el.close();
    Promise.resolve()
      .then(item.run)
      .catch((err) => toast(err.message, "error"));
  }

  input.addEventListener("input", refresh);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      active = (active + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      draw();
    } else if (e.key === "Enter") {
      e.preventDefault();
      // Enter before the list caught up with the typing: wait for it, then run the first item.
      if (waiting) enterQueued = true;
      else if (items[active]) run(items[active]);
    }
  });
  el.addEventListener("click", (e) => e.target === el && el.close()); // a click on the backdrop
  return { el, input, refresh };
}

async function preview(text) {
  try {
    return await api("/api/capture", { method: "POST", body: { text, preview: true } });
  } catch {
    return null;
  }
}

function captureItem(text, found) {
  const hint = { event: () => `${found.event_kind} · ${when(found.start)}`, task: () => "ülesanne tänasesse päevikusse" }[found?.kind];
  return { kind: "capture", label: `Lisa: ${text}`, hint: hint ? hint() : "uus märge", run: () => capture(text) };
}

function when(start) {
  const [date, time] = start.split("T");
  const d = new Date(`${date}T12:00`);
  return `${WEEKDAY[d.getDay()]} ${d.getDate()}.${d.getMonth() + 1}.${time ? ` kell ${time}` : ""}`;
}

async function capture(text) {
  const out = await api("/api/capture", { method: "POST", body: { text } });
  if (out.kind === "event") {
    toast(`Lisatud kalendrisse: ${out.title}, ${when(out.start)}`);
    emit("events:changed", {});
  } else if (out.kind === "task") {
    toast("Ülesanne lisatud tänasesse päevikusse.");
    emit("note:changed", { note: { slug: out.slug } });
  } else {
    await go(out.slug);
  }
}
