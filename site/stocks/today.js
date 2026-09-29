// Aktsiad: Täna's "Turg" card (GET /api/stocks/today), in the look of Täna's other blocks: the day's
// move of every holding, earnings today or tomorrow, and the verdicts last night changed. Nothing is
// shown while no stock is followed. Owner: stocks-night.
import { api } from "/static/app.js";
import { h, money, pct, VERDICT } from "./common.js";

const href = (symbol) => `/aktsiad/${encodeURIComponent(symbol)}`;
const WHEN = { bmo: "enne avanemist", amc: "pärast sulgemist" };
const verdictText = (v) => (VERDICT[v] || [v]).join(" ");

export async function mount(el) {
  if (!el) return;
  let data;
  try {
    data = await api("/api/stocks/today");
  } catch (err) {
    // The rest of Täna matters more than this card; it just stays away.
    console.info("Turg not loaded:", err.message);
    return;
  }
  if (!data.followed) return;
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
  const rows = [
    ...data.holdings.map((m) => row(move(m), m, m.price == null ? "hinda pole" : money(m.price, m.currency))),
    ...data.earnings.map((e) => row(h("span", {}, e.date === today ? "täna" : "homme"), e, ["tulemused", WHEN[e.time_of_day]].filter(Boolean).join(" "))),
    ...data.verdicts.map((v) => row(h("span", {}, "hinnang"), v, `${verdictText(v.previous)} → ${verdictText(v.verdict)}`)),
  ];
  el.append(
    h(
      "section",
      { class: "today-block turg", "aria-labelledby": "turg-pealkiri" },
      h("h2", { class: "sec", id: "turg-pealkiri" }, h("a", { href: "/aktsiad" }, "Turg")),
      rows.length ? h("ul", { class: "rows turg-read" }, rows) : h("p", { class: "today-empty" }, "Täna pole portfellis ega jälgitavate seas midagi erilist."),
    ),
  );
}

/** One row: a lead column (the move, "täna", "hinnang"), the symbol as a link, the name, a note. */
function row(lead, item, note) {
  return h(
    "li",
    {},
    h("span", { class: "turg-lead" }, lead),
    h("a", { class: "turg-symbol", href: href(item.symbol) }, item.symbol),
    h("span", { class: "agenda-note turg-name" }, item.name),
    h("span", { class: "agenda-note" }, note),
  );
}

// The direction as a shape and a sign, the colour only repeats it.
function move(m) {
  if (m.change == null) return h("span", { "data-suund": "flat" }, "–");
  const v = Number(m.change);
  const dir = v > 0 ? "up" : v < 0 ? "down" : "flat";
  return h("span", { "data-suund": dir }, h("span", { "aria-hidden": "true" }, dir === "up" ? "▲ " : dir === "down" ? "▼ " : "◆ "), pct(m.change));
}
