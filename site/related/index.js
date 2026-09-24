// Seotud: brain pages related to the open note (gbrain, GET /api/related/<slug>), and a command
// to sync notes with the brain repo now (POST /api/sync/now). Owner: WP-H.
import { api, on, registerCommand, registerPanel, toast } from "/static/app.js";

function text(tag, className, content) {
  const el = document.createElement(tag);
  el.className = className;
  el.textContent = content;
  return el;
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
    async function load(note) {
      const slug = note?.slug ?? null;
      if (slug === shown) return;
      shown = slug;
      if (!slug) {
        el.replaceChildren(text("p", "related-empty", "Ava märge, et näha brain'i seotud lehti."));
        return;
      }
      el.replaceChildren(text("p", "related-empty", "Otsin seotud lehti…"));
      try {
        const hits = await api(`/api/related/${encodeURIComponent(slug)}`);
        if (shown === slug) render(el, hits);
      } catch (err) {
        if (shown === slug) el.replaceChildren(text("p", "related-empty", err.message));
      }
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
