// Data files in notes (docs/FAILID-PLAN.md): the right panel's "Failid" tab lists the files the
// open note links and adds new ones (upload, a web address, a file another note has); a data file
// dropped onto the note is uploaded. A file is a link [name](/failid/<sha256>.<ext>); Python cells
// read it by that name (fail("name")). Owner only.
import { api, on, registerPanel, toast } from "/static/app.js";

const ACCEPT = ".csv,.tsv,.txt,.json,.xlsx,.parquet";
const DATA = /\.(csv|tsv|txt|json|xlsx|parquet)$/i;
// As app/failid.py LINK: not a picture link; escapes in the text undone.
const LINK = /(?<!!)\[((?:[^[\]\\\n]|\\.)*)\]\(\/failid\/([0-9a-f]{64}\.(?:csv|tsv|txt|json|xlsx|parquet))\)/g;
const isOwner = () => Boolean(document.getElementById("nav"));

export function linked(markdown) {
  const seen = new Map();
  for (const m of markdown.matchAll(LINK)) {
    const label = m[1].replace(/\\(.)/g, "$1");
    if (!seen.has(m[2] + label)) seen.set(m[2] + label, { label, name: m[2] });
  }
  return [...seen.values()];
}

// Markdown's own marks too, so "*mõõtmised*.csv" stays a name, not emphasis (app/failid.py undoes it).
const escapeLabel = (text) => text.replace(/([[\]\\*_`])/g, "\\$1");
const linkFor = (file) => `[${escapeLabel(file.original_name)}](/failid/${file.name})`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1024))} kB`);

/** The link under the block the caret is in, or at the note's end. */
function insertLink(editor, file) {
  const blocks = editor.blocks?.();
  if (!Array.isArray(blocks)) return false;
  const node = document.getSelection()?.anchorNode;
  const nodes = editor.blockNodes();
  let at = blocks.length;
  if (node) {
    const i = nodes.findIndex((n) => n?.contains(node));
    if (i >= 0) at = i + 1;
  }
  return editor.insertBlocks(at, linkFor(file));
}

async function upload(file, note) {
  const body = new FormData();
  body.append("file", file, file.name);
  if (note?.slug) body.append("slug", note.slug);
  return api("/api/failid", { method: "POST", body });
}

function mount(root, ctx) {
  if (!isOwner() || !ctx.editor) {
    root.replaceChildren(el("p", { class: "failid-tyhi" }, "Failid on märkme omaniku jaoks."));
    return undefined;
  }
  root.classList.add("failid");
  const list = el("ul", { class: "failid-nimekiri" });
  const add = el("button", { type: "button", class: "primary", onclick: () => dialog(ctx, render) }, "Lisa fail");
  const hint = el("p", { class: "failid-vihje" }, "Ploki koodis: fail(\"nimi.csv\"). Faili saab ka märkmele lohistada.");
  root.replaceChildren(add, list, hint);

  async function render() {
    const files = linked(ctx.editor.getMarkdown() || "");
    if (!files.length) {
      list.replaceChildren(el("li", { class: "failid-tyhi" }, "Selles märkmes pole veel faile."));
      return;
    }
    const infos = await Promise.all(files.map((f) => api(`/api/failid/${f.name}`).catch(() => null)));
    list.replaceChildren(...files.map((f, i) => row(f, infos[i])));
  }

  function row(f, info) {
    const li = el("li", {},
      el("a", { href: `/failid/${f.name}`, download: f.label }, f.label),
      info ? el("span", { class: "failid-info" }, size(info.size)) : null,
      info?.source_url ? el("span", { class: "failid-info", title: info.source_url }, `veebist · ${new Date(info.fetched_at).toLocaleDateString("et")}`) : null);
    if (info?.source_url) {
      li.append(el("button", {
        type: "button", class: "linklike", title: "Lae aadressilt uuesti",
        onclick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            const fresh = await api(`/api/failid/${f.name}/refresh`, { method: "POST", body: { slug: ctx.note.slug } });
            if (fresh.name === f.name) toast("Fail on sama, mis enne.");
            else {
              // The note's links move to the new copy; the old file stays for other notes.
              const md = ctx.editor.getMarkdown();
              ctx.editor.updateMarkdown(md.split(`](/failid/${f.name})`).join(`](/failid/${fresh.name})`));
              toast("Fail uuendatud.");
            }
            render();
          } catch (err) {
            toast(err.message, "error");
          }
        },
      }, "Värskenda"));
    }
    return li;
  }

  render();
  const timer = setInterval(render, 5000); // links come and go with editing
  return () => clearInterval(timer);
}

function dialog(ctx, done) {
  const dlg = el("dialog", { class: "failid-dialog" });
  const close = () => dlg.close();
  const added = (file) => {
    if (!insertLink(ctx.editor, file)) toast("Linki ei saanud märkmesse lisada.", "error");
    else toast(`Lisatud: ${file.original_name}`);
    close();
    done();
  };
  const error = el("p", { class: "failid-viga", role: "alert", hidden: "" });
  const fail = (err) => {
    error.textContent = err.message;
    error.hidden = false;
  };

  const picker = el("input", { type: "file", accept: ACCEPT, "aria-label": "Fail arvutist" });
  picker.addEventListener("change", async () => {
    if (!picker.files[0]) return;
    try {
      added(await upload(picker.files[0], ctx.note));
    } catch (err) {
      fail(err);
    }
  });

  const address = el("input", { type: "url", placeholder: "https://…/andmed.csv", "aria-label": "Faili veebiaadress" });
  const fetchIt = el("button", { type: "button", onclick: async () => {
    fetchIt.disabled = true;
    try {
      added(await api("/api/failid/url", { method: "POST", body: { url: address.value.trim(), slug: ctx.note.slug } }));
    } catch (err) {
      fail(err);
    } finally {
      fetchIt.disabled = false;
    }
  } }, "Lae alla");

  const query = el("input", { type: "search", placeholder: "Otsi failinime järgi", "aria-label": "Otsi olemasolevat faili" });
  const found = el("ul", { class: "failid-leitud" });
  let searchTimer = null;
  const search = async () => {
    try {
      const files = await api(`/api/failid?q=${encodeURIComponent(query.value.trim())}`);
      found.replaceChildren(...(files.length ? files.map((f) => el("li", {},
        el("button", { type: "button", class: "linklike", onclick: () => added(f) }, f.original_name),
        el("span", { class: "failid-info" }, `${size(f.size)}${f.notes?.length ? ` · ${f.notes.map((n) => n.title).join(", ")}` : ""}`))) : [el("li", { class: "failid-tyhi" }, "Ei leitud.")]));
    } catch (err) {
      fail(err);
    }
  };
  query.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(search, 200);
  });

  dlg.append(
    el("h2", {}, "Lisa andmefail"),
    el("p", { class: "failid-vihje" }, "CSV, TSV, TXT, JSON, Excel (.xlsx) või Parquet, kuni 25 MB."),
    el("section", {}, el("h3", {}, "Arvutist"), picker),
    el("section", {}, el("h3", {}, "Veebiaadressilt"), el("div", { class: "failid-rida" }, address, fetchIt)),
    el("section", {}, el("h3", {}, "Teise märkme fail"), query, found),
    error,
    el("div", { class: "failid-nupud" }, el("button", { type: "button", onclick: close }, "Sulge")),
  );
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  search();
}

// A data file dropped onto the note: uploaded and linked where it fell (pictures stay Muya's).
document.addEventListener("drop", async (e) => {
  const editorEl = e.target.closest?.("#editor");
  const files = [...(e.dataTransfer?.files || [])].filter((f) => DATA.test(f.name));
  if (!editorEl || !files.length || !isOwner()) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const ctx = dropContext;
  if (!ctx?.editor) return;
  for (const file of files) {
    try {
      const stored = await upload(file, ctx.note);
      insertLink(ctx.editor, stored);
      toast(`Lisatud: ${stored.original_name}`);
    } catch (err) {
      toast(`${file.name}: ${err.message}`, "error");
    }
  }
}, true);
document.addEventListener("dragover", (e) => {
  if (e.target.closest?.("#editor") && [...(e.dataTransfer?.items || [])].some((i) => i.kind === "file")) e.preventDefault();
}, true);

let dropContext = null; // the note page's editor, for drops
on("editor:ready", (ctx) => (dropContext = ctx));

registerPanel("failid", { title: "Failid", order: 45, mount });
