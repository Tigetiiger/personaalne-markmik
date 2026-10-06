// "Ajalugu" panel (docs/MARKMED-PLAN.md 17): the open note's versions in the brain repo's git,
// newest first. A version opens in the review window against the editor's text; what is kept goes
// into the editor as ordinary edits, so co-editors get it and git gets a new version.
import { api, registerPanel, toast } from "/static/app.js";
import { h } from "./dom.js";

const when = (iso) => new Date(iso).toLocaleString("et-EE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });

registerPanel("ajalugu", {
  title: "Ajalugu",
  order: 50,
  async mount(el, { note, editor }) {
    const base = `/api/notes/${encodeURIComponent(note.slug)}/ajalugu`;
    el.replaceChildren(h("p", { class: "panel-empty" }, "Loen ajalugu…"));
    let versions;
    try {
      ({ versions } = await api(base));
    } catch (err) {
      el.replaceChildren(h("p", { class: "panel-empty error" }, err.message));
      return;
    }
    if (!versions.length) {
      el.replaceChildren(h("p", { class: "panel-empty" }, "Ajalugu tekib pärast esimest sünkroonimist brain'iga."));
      return;
    }
    const open = async (button, v) => {
      button.disabled = true;
      try {
        const [{ body }, { review }] = await Promise.all([api(`${base}/${v.sha}`), import("/static/ai/review.js")]);
        const result = await review({ base: editor.getMarkdown(), markdown: body, editor, heading: `Versioon ${when(v.at)}`, empty: "See versioon on sama mis praegune tekst." });
        if (result?.applied) toast("Versioon taastatud.");
      } catch (err) {
        toast(err.message, "error");
      } finally {
        button.disabled = false;
      }
    };
    el.replaceChildren(
      h("p", { class: "panel-empty" }, "Viimase paari minuti muudatused pole veel gitis."),
      h("ul", { class: "history-list" },
        versions.map((v) => {
          const button = h("button", { type: "button", class: "linklike history-version", title: "Vaata ja taasta" }, when(v.at));
          button.addEventListener("click", () => open(button, v));
          return h("li", {}, button, h("span", { class: "history-message" }, v.message));
        })),
    );
  },
});
