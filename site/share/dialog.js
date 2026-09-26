// The Jaga dialog: the note's share links, new view or edit links with an optional expiry, and
// revoking. Everything from the server goes in as text (h() never parses HTML). Messages go in
// the dialog itself: a modal dialog sits above the page's toast.
import { api, emit } from "/static/app.js";

const DAY_MS = 24 * 60 * 60 * 1000;
const EXPIRY = [["", "ei aegu"], ["1", "1 päev"], ["7", "7 päeva"], ["30", "30 päeva"]];
const stamp = new Intl.DateTimeFormat("et-EE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (utc) => stamp.format(new Date(utc));

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  el.append(...children.flat());
  return el;
}

/** The metadata-row control (docs/CONTRACTS.md §6 registerMetaAction). */
export function mountShareAction(el, { note }) {
  el.append(h("button", { type: "button", class: "linklike share-open", onclick: () => openDialog(note) }, "Jaga"));
}

function openDialog(note) {
  const expiry = h("select", { "aria-label": "Aegub" }, EXPIRY.map(([value, label]) => h("option", { value }, label)));
  const fresh = h("div", { class: "share-new", hidden: true });
  const list = h("ul", { class: "share-list" });
  const status = h("p", { class: "share-status", role: "status" });
  let freshId = null;
  const say = (text, error = false) => {
    status.textContent = text;
    status.classList.toggle("error", error);
  };
  const busy = async (button, fn) => {
    button.disabled = true;
    say("");
    try {
      await fn();
    } catch (err) {
      say(err.message, true);
    } finally {
      button.disabled = false;
    }
  };

  const dlg = h(
    "dialog",
    { class: "share-dialog ui", "aria-labelledby": "share-dialog-title" },
    h("h2", { id: "share-dialog-title" }, "Jaga märget"),
    h("p", { class: "share-hint" }, `„${note.title}“: lingiga saab märget avada ilma sisse logimata. Vaatamise link näitab märget, muutmise lingiga saab koos kirjutada.`),
    h(
      "div",
      { class: "share-create" },
      h("label", {}, "Aegub ", expiry),
      h("button", { type: "button", class: "primary", onclick: (e) => create(e.currentTarget, "view") }, "Loo vaatamise link"),
      h("button", { type: "button", onclick: (e) => create(e.currentTarget, "edit") }, "Loo muutmise link"),
    ),
    fresh,
    status,
    h("h3", {}, "Lingid"),
    list,
    h("div", { class: "share-foot" }, h("button", { type: "button", onclick: () => dlg.close() }, "Sulge")),
  );
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  refresh().catch((err) => say(err.message, true));

  async function create(button, mode) {
    const body = { slug: note.slug, mode };
    if (expiry.value) body.expires_at = new Date(Date.now() + Number(expiry.value) * DAY_MS).toISOString();
    await busy(button, async () => {
      const link = await api("/api/shares", { method: "POST", body });
      showFresh(link);
      await refresh();
    });
  }

  function showFresh(link) {
    freshId = link.id;
    const url = h("input", { type: "text", class: "share-url", readonly: true, value: link.url, "aria-label": "Uus link" });
    fresh.replaceChildren(
      h("p", {}, `Uus ${link.mode === "edit" ? "muutmise" : "vaatamise"} link. Kopeeri see kohe: hiljem seda enam näha ei ole.`),
      h("div", { class: "share-url-row" }, url, h("button", { type: "button", onclick: () => copy(url, say) }, "Kopeeri")),
    );
    fresh.hidden = false;
    url.select();
  }

  async function refresh() {
    const links = await api(`/api/shares?slug=${encodeURIComponent(note.slug)}`);
    list.replaceChildren(...(links.length ? links.map(row) : [h("li", { class: "share-empty" }, "Linke veel pole.")]));
    emit("shares:changed", { slug: note.slug });
  }

  function row(link) {
    const expired = link.expires_at && new Date(link.expires_at) <= new Date();
    return h(
      "li",
      { class: "share-link", "data-id": link.id },
      h("b", {}, link.mode === "edit" ? "Muutmiseks" : "Vaatamiseks"),
      h("span", {}, `loodud ${when(link.created_at)}`),
      h("span", { class: expired ? "error" : null }, link.expires_at ? `${expired ? "aegus" : "aegub"} ${when(link.expires_at)}` : "ei aegu"),
      h("span", {}, link.last_used_at ? `viimati kasutatud ${when(link.last_used_at)}` : "pole kasutatud"),
      h("button", { type: "button", class: "share-revoke", onclick: (e) => revoke(e.currentTarget, link) }, "Tühista"),
    );
  }

  async function revoke(button, link) {
    if (!confirm("Tühistada see link? Kes seda kasutab, ei pääse enam märkme juurde.")) return;
    await busy(button, async () => {
      await api(`/api/shares/${link.id}`, { method: "DELETE" });
      if (link.id === freshId) fresh.hidden = true;
      say("Link tühistatud.");
      await refresh();
    });
  }
}

// The clipboard API needs a secure context; over the tailnet the app is plain HTTP.
async function copy(input, say) {
  input.select();
  let ok;
  try {
    await navigator.clipboard.writeText(input.value);
    ok = true;
  } catch {
    ok = document.execCommand("copy");
  }
  say(ok ? "Link kopeeritud." : "Kopeeri valitud link käsitsi (Ctrl+C).", !ok);
}
