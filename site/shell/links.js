// "Viited" panel: links out of the note, backlinks into it, and unlinked mentions with "Seo",
// which turns the mention in the other note into a link here.
import { api, on, registerPanel, toast } from "/static/app.js";
import { h, noteHref, plainClick } from "./dom.js";
import { go } from "./note.js";

registerPanel("viited", {
  title: "Viited",
  order: 10,
  mount(el, { note }) {
    const base = `/api/notes/${encodeURIComponent(note.slug)}`;
    let gen = 0;
    const load = async () => {
      const mine = ++gen;
      try {
        const [links, mentions] = await Promise.all([api(`${base}/links`), api(`${base}/mentions`)]);
        if (mine !== gen) return;
        el.replaceChildren(
          block("Lingid siit", links.outgoing, "Sellest märkmest ei viita ükski link.", (l) =>
            l.exists ? [link(l.slug, l.title)] : [h("span", { class: "missing", title: "Seda märget pole" }, l.title)]),
          block("Tagasiviited", links.backlinks, "Siia ei viita veel ükski märge.", (b) => [link(b.slug, b.title), snippetOf(b)]),
          block("Mainimised", mentions, "Lingita mainimisi pole.", (m) => [
            link(m.slug, m.title),
            h("button", { type: "button", class: "mention-link", onclick: (e) => linkMention(e.currentTarget, m.slug) }, "Seo"),
            snippetOf(m),
          ]),
        );
      } catch (err) {
        if (mine === gen) el.replaceChildren(h("p", { class: "panel-empty error" }, err.message));
      }
    };

    async function linkMention(button, src) {
      button.disabled = true;
      try {
        await api(`${base}/link-mention`, { method: "POST", body: { src_slug: src } });
        toast("Seotud.");
        load();
      } catch (err) {
        button.disabled = false;
        toast(err.message, "error");
      }
    }

    load();
    return on("note:saved", ({ note: saved }) => saved.slug === note.slug && load());
  },
});

function block(title, items, empty, row) {
  return h("section", { class: "links-block" },
    h("h4", {}, title, " ", h("span", { class: "side-count" }, String(items.length))),
    items.length ? h("ul", {}, items.map((item) => h("li", {}, row(item)))) : h("p", { class: "panel-empty" }, empty));
}

function link(slug, title) {
  return h("a", { href: noteHref(slug), onclick: (e) => plainClick(e) && (e.preventDefault(), go(slug)) }, title);
}

const snippetOf = (item) => (item.snippet ? h("p", { class: "links-snippet" }, item.snippet) : null);
