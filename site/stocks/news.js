// Aktsiad: the "Uudised" section of a stock's page (stocks-sources). The last 25 documents about
// the stock, with the model's one-line summary and sentiment once the night has scored them.
// GET /api/stocks/docs/<symbol>.
import { api } from "/static/app.js";
import { h, num, registerTickerSection, when } from "./common.js";
import { SOURCE_LABEL } from "./sources.js";

const EVENT = {
  earnings: "tulemused",
  guidance: "prognoos",
  m_and_a: "ühinemine või ost",
  product: "toode",
  regulatory: "regulatsioon",
  legal: "kohtuasi",
  management: "juhtkond",
  insider: "siseringi tehing",
  analyst: "analüütikud",
  capital_return: "dividend või tagasiost",
  macro: "makro",
  other: "muu",
};
// The model's -1..1 as words and a shape, never colour alone.
function mood(value) {
  const v = Number(value);
  const [kind, shape, word] = v >= 0.15 ? ["up", "▲", "positiivne"] : v <= -0.15 ? ["down", "▼", "negatiivne"] : ["flat", "◆", "neutraalne"];
  const signed = `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), 2)}`;
  return h("span", { class: "ak-meelsus", "data-suund": kind, title: `Meelsus ${signed} (−1 … +1)` }, h("span", { "aria-hidden": "true" }, shape), ` ${word} ${signed}`);
}

function item(d) {
  const title = d.title || "(pealkirjata)";
  const meta = [SOURCE_LABEL[d.source] || d.source, d.publisher, when(d.published_at)].filter(Boolean).join(" · ");
  return h(
    "li",
    { class: "ak-uudis" },
    d.url ? h("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, title) : h("span", {}, title),
    h(
      "div",
      { class: "ak-uudis-meta" },
      meta,
      d.sentiment != null ? mood(d.sentiment) : null,
      d.event_type ? h("span", { class: "ak-uudis-liik" }, EVENT[d.event_type] || d.event_type) : null,
    ),
    d.summary ? h("p", { class: "ak-uudis-kokku" }, d.summary) : null,
  );
}

async function mount(el, { symbol }) {
  const box = h("section", { class: "ak-uudised", "aria-labelledby": "ak-uudised-pealkiri" }, h("h2", { class: "sec", id: "ak-uudised-pealkiri" }, "Uudised"));
  el.append(box);
  let docs;
  try {
    docs = await api(`/api/stocks/docs/${encodeURIComponent(symbol)}`);
  } catch (err) {
    box.append(h("p", { class: "error" }, `Uudiseid ei saanud laadida: ${err.message}`));
    return;
  }
  box.append(docs.length ? h("ul", { class: "rows ak-uudised-list" }, docs.map(item)) : h("p", { class: "ak-all-tuhi" }, "Uudiseid pole veel. Allikaid loetakse igal ööl."));
}

registerTickerSection("news", { order: 60, mount });
