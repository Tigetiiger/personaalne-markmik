// Share links (app/views/meedia.py, /api/meedia/jagamised): make one for a file or a selection,
// copy it, see and revoke the open ones. A link's URL is shown only when it is made.
import { api, toast } from "/static/app.js";
import { h } from "/static/shell/dom.js";

import { dateTime } from "./format.js";

const EXPIRY = [
  ["", "Ei aegu"],
  ["1", "1 päev"],
  ["7", "7 päeva"],
  ["30", "30 päeva"],
  ["365", "1 aasta"],
];

async function copy(input) {
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    // No clipboard API on the plain-http Tailscale address: the old way.
    input.select();
    document.execCommand("copy");
  }
  toast("Link kopeeritud.");
}

/** The share controls for `names`; `name` set: also the open links holding that file. */
export function shareBox(names, { album = false, name = null } = {}) {
  const title = album
    ? h("input", {
        type: "text",
        placeholder: "Pealkiri (valikuline)",
        "aria-label": "Lingi pealkiri",
      })
    : null;
  const expiry = h(
    "select",
    { "aria-label": "Aegub" },
    EXPIRY.map(([v, label]) => h("option", { value: v }, label)),
  );
  const download = h("input", { type: "checkbox" });
  const made = h("div", { class: "md-link" });
  const list = h("ul", { class: "md-lingid" });

  async function create() {
    try {
      const share = await api("/api/meedia/jagamised", {
        method: "POST",
        body: {
          names,
          title: title?.value || "",
          days: expiry.value ? Number(expiry.value) : null,
          download: download.checked,
        },
      });
      const url = h("input", {
        type: "text",
        readonly: true,
        value: share.url,
        "aria-label": "Jagamislink",
      });
      made.replaceChildren(url, h("button", { type: "button", class: "primary", onclick: () => copy(url) }, "Kopeeri"));
      url.select();
      refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function refresh() {
    if (!name) return;
    const { shares } = await api(`/api/meedia/jagamised?name=${encodeURIComponent(name)}`);
    list.replaceChildren(
      ...shares.map((s) =>
        h(
          "li",
          {},
          h(
            "span",
            {},
            [s.title || (s.files > 1 ? `${s.files} faili` : "See fail"), ` · ${dateTime(new Date(s.created_at))}`, s.expires_at ? ` · aegub ${dateTime(new Date(s.expires_at))}` : ""].join(""),
          ),
          h(
            "button",
            {
              type: "button",
              class: "ghost md-ohtlik",
              onclick: async () => {
                await api(`/api/meedia/jagamised/${s.id}`, {
                  method: "DELETE",
                });
                toast("Link tühistatud.");
                refresh();
              },
            },
            "Tühista",
          ),
        ),
      ),
    );
  }
  refresh();

  return h(
    "div",
    { class: "md-jaga" },
    title,
    h("div", { class: "md-jaga-rida" }, h("label", {}, "Aegub ", expiry), h("label", {}, download, " Luba originaali allalaadimine")),
    h("button", { type: "button", class: "strong", onclick: create }, "Loo link"),
    made,
    name ? list : null,
  );
}

/** A dialog with shareBox for a selection. */
export function shareDialog(names) {
  const dialog = h(
    "dialog",
    { class: "md-jaga-aken", "aria-label": "Jaga" },
    h("h2", {}, `Jaga ${names.length} faili`),
    shareBox(names, { album: true }),
    h("button", { type: "button", class: "ghost", onclick: () => dialog.close() }, "Sulge"),
  );
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}
