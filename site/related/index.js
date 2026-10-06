// Seotud: notes like the open one (the app's own search, GET /api/notes/<slug>/sarnased, which
// knows Estonian), then brain pages related to it (gbrain, GET /api/related/<slug>), and a command
// to sync notes with the brain repo now (POST /api/sync/now). Owner: WP-H.
import { api, on, registerCommand, registerPanel, toast } from "/static/app.js";

function text(tag, className, content) {
  const el = document.createElement(tag);
  el.className = className;
  el.textContent = content;
  return el;
}

function renderSimilar(el, notes) {
  if (!notes.length) {
    el.replaceChildren(text("p", "related-empty", "Sarnaseid märkmeid ei leitud."));
    return;
  }
  const list = document.createElement("ul");
  list.className = "related-list";
  for (const note of notes) {
    const item = document.createElement("li");
    const title = text("a", "related-title", note.title);
    title.href = `/m/${encodeURIComponent(note.slug)}`;
    item.append(title);
    if (note.folder) item.append(text("span", "related-path", note.folder));
    list.append(item);
  }
  el.replaceChildren(list);
}

function render(el, hits) {
  if (!hits.length) {
    el.replaceChildren(text("p", "related-empty", "Seotud lehti ei leitud."));
    return;
  }
  const list = document.createElement("ul");
  list.className = "related-list";
  for (const hit of hits) {
    const item = document.createElement("li");
    // A note's file is named by its slug, so notes/…/<slug> opens here; other brain pages cannot.
    const slug = hit.slug.startsWith("notes/") ? hit.slug.split("/").pop() : null;
    const title = text(slug ? "a" : "span", "related-title", hit.title);
    if (slug) title.href = `/m/${encodeURIComponent(slug)}`;
    item.append(title, text("span", "related-path", hit.slug));
    if (hit.snippet) item.append(text("p", "related-snippet", hit.snippet));
    list.append(item);
  }
  el.replaceChildren(list);
}

registerPanel("related", {
  title: "Seotud",
  order: 40,
  mount(el, ctx) {
    let shown;
    const similar = document.createElement("div");
    const brain = document.createElement("div");
    el.replaceChildren(text("h4", "related-heading", "Sarnased märkmed"), similar, text("h4", "related-heading", "Brain'i lehed"), brain);
    async function load(note) {
      const slug = note?.slug ?? null;
      if (slug === shown) return;
      shown = slug;
      if (!slug) {
        similar.replaceChildren();
        brain.replaceChildren(text("p", "related-empty", "Ava märge, et näha brain'i seotud lehti."));
        return;
      }
      similar.replaceChildren(text("p", "related-empty", "Otsin sarnaseid…"));
      brain.replaceChildren(text("p", "related-empty", "Otsin seotud lehti…"));
      const path = encodeURIComponent(slug);
      await Promise.all([
        api(`/api/notes/${path}/sarnased`).then(
          (notes) => shown === slug && renderSimilar(similar, notes),
          (err) => shown === slug && similar.replaceChildren(text("p", "related-empty", err.message)),
        ),
        api(`/api/related/${path}`).then(
          (hits) => shown === slug && render(brain, hits),
          (err) => shown === slug && brain.replaceChildren(text("p", "related-empty", err.message)),
        ),
      ]);
    }
    load(ctx.note);
    return on("note:open", ({ note }) => load(note));
  },
});

registerCommand("brain-sync", {
  title: "Sünkrooni märkmed brain'iga",
  keywords: "brain git sünkroonimine sync",
  async run() {
    try {
      const r = await api("/api/sync/now", { method: "POST" });
      toast(`Brain sünkroonitud (saadetud: ${r.pushed}, saabunud: ${r.pulled}, konflikte: ${r.conflicts}).`);
    } catch (err) {
      toast(err.message, "error");
    }
  },
});
