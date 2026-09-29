// Aktsiad: the "Sinu positsioon" section of a stock's page (GET /api/stocks/position/<symbol>).
// Nothing is shown when the stock is not held. Owner: stocks-portfolio.
import { api } from "/static/app.js";
import { day, direction, h, money, num, pct, registerTickerSection } from "./common.js";

const qty = (v) => num(v, Number.isInteger(Number(v)) ? 0 : 4);

registerTickerSection("position", {
  order: 30,
  async mount(el, { symbol }) {
    const p = await api(`/api/stocks/position/${encodeURIComponent(symbol)}`);
    if (!p) return;
    const gain = p.gain_eur == null ? "–" : h("span", { "data-suund": direction(p.gain_eur) }, `${Number(p.gain_eur) > 0 ? "+" : Number(p.gain_eur) < 0 ? "−" : ""}${num(Math.abs(Number(p.gain_eur)))} EUR`, p.unrealised_pct != null && ` (${pct(p.unrealised_pct)})`);
    el.append(
      h(
        "section",
        { class: "pf-positsioon", "aria-labelledby": "pf-positsioon-pealkiri" },
        h("h2", { id: "pf-positsioon-pealkiri" }, "Sinu positsioon"),
        h(
          "dl",
          { class: "pf-kokkuvote" },
          h("div", { class: "pf-naitaja" }, h("dt", {}, "Kogus"), h("dd", {}, qty(p.qty))),
          h("div", { class: "pf-naitaja" }, h("dt", {}, "Keskmine hind"), h("dd", {}, money(p.avg_cost, p.currency))),
          h(
            "div",
            { class: "pf-naitaja" },
            h("dt", {}, "Väärtus"),
            h("dd", {}, p.value_eur == null ? money(p.value, p.currency) : money(p.value_eur, "EUR"), h("small", {}, p.market_price ? `hind ${money(p.price, p.currency)}, ${day(p.price_as_of)}` : "viimase tehingu hinnaga")),
          ),
          h("div", { class: "pf-naitaja" }, h("dt", {}, "Kasum"), h("dd", {}, gain, h("small", {}, `soetus ${money(p.cost_eur, "EUR")}`))),
        ),
        h("p", { class: "leht-juhend pf-kontodel" }, `Kontodel: ${p.accounts.map((a) => `${a.name} ${qty(a.qty)}`).join(" · ")}`),
        h("p", {}, h("a", { href: "/aktsiad/portfell" }, "Portfell"), " · ", h("a", { href: "/aktsiad/tehingud#lisa" }, "Lisa tehing")),
      ),
    );
  },
});
