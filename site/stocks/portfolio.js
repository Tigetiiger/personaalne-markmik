// Aktsiad → Portfell (GET /api/stocks/portfolio): the summary with the FX effect apart, positions,
// value over time, allocation, dividends and the investment account panel. Charts are plain SVG
// (no chart library). Owner: stocks-portfolio.
import { api } from "/static/app.js";
import { day, direction, h, money, num, pct, svg } from "./common.js";

/** An unsigned share: "0.6451" -> "64,5 %". */
const share = (fraction, digits = 1) => (fraction == null ? "" : `${num(Number(fraction) * 100, digits)} %`);

/** A signed amount with its direction, never colour alone: "+50,00", "−120,00". */
function signed(value, digits = 2) {
  if (value == null) return h("span", { class: "ak-num" }, "–");
  const v = Number(value);
  const text = `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), digits)}`;
  return h("span", { class: "ak-num", "data-suund": direction(value) }, text);
}

function signedPct(fraction) {
  return fraction == null ? h("span", { class: "ak-num" }, "–") : h("span", { class: "ak-num", "data-suund": direction(fraction) }, pct(fraction));
}

function section(id, title, ...body) {
  return h("section", { class: "pf-jaotis", id, "aria-labelledby": `${id}-pealkiri` }, h("h2", { id: `${id}-pealkiri` }, title), ...body);
}

function notices(items) {
  return items.map((text) => h("p", { class: "pf-teade", role: "note" }, h("span", { "aria-hidden": "true" }, "⚠ "), text));
}

function tile(label, value, sub, attrs = {}) {
  return h("div", { class: "pf-naitaja", ...attrs }, h("dt", {}, label), h("dd", {}, value, sub && h("small", {}, sub)));
}

function summary(s) {
  const eur = (v) => money(v, "EUR");
  return h(
    "dl",
    { class: "pf-kokkuvote", id: "pf-kokkuvote" },
    tile("Väärtus", eur(s.value), `sularaha ${eur(s.cash)}`, { class: "pf-naitaja pf-pea" }),
    tile("Sissemakstud neto", eur(s.net_invested)),
    tile("Kasum kokku", [signed(s.total), " EUR"]),
    tile("Realiseerimata", [signed(s.unrealised.eur), " EUR"], ["hinnast ", signed(s.unrealised.price), ", valuutast ", signed(s.unrealised.fx)]),
    tile("Realiseeritud", [signed(s.realised.eur), " EUR"], ["hinnast ", signed(s.realised.price), ", valuutast ", signed(s.realised.fx)]),
    tile("Dividendid ja intress", eur(s.income), "neto, pärast kinnipeetud maksu"),
    tile("Tasud", eur(s.fees), "eraldi tasud; vahendustasu on tehingu sees"),
    tile("Ajaga kaalutud tootlus", signedPct(s.twr), "TWR, kogu perioodi eest"),
    tile("Rahaga kaalutud tootlus", signedPct(s.xirr), "XIRR, aastas"),
  );
}

function positions(rows) {
  if (!rows.length) return h("p", { class: "leht-juhend" }, "Praegu ei hoia sa ühtegi väärtpaberit.");
  return h(
    "div",
    { class: "ak-tabel-kerib" },
    h(
      "table",
      { class: "ak-tabel", id: "pf-positsioonid" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Väärtpaber"),
          ["Kogus", "Keskm. hind", "Hind", "Väärtus EUR", "Osakaal", "Kasum", "Kasum EUR", "Div. tootlus"].map((t) => h("th", { class: "ak-num", scope: "col" }, t)),
        ),
      ),
      h(
        "tbody",
        {},
        rows.map((p) =>
          h(
            "tr",
            { "data-symbol": p.symbol },
            h("td", {}, h("a", { href: `/aktsiad/${encodeURIComponent(p.symbol)}` }, p.symbol), h("small", { class: "pf-nimi" }, p.name)),
            h("td", { class: "ak-num" }, num(p.qty, Number.isInteger(Number(p.qty)) ? 0 : 4)),
            h("td", { class: "ak-num" }, money(p.avg_cost, p.currency)),
            h("td", { class: "ak-num" }, money(p.price, p.currency), p.market_price ? null : h("small", { class: "pf-markus" }, "viimase tehingu hind")),
            h("td", { class: "ak-num" }, p.value_eur == null ? h("small", { class: "pf-markus" }, "kurss puudub") : num(p.value_eur)),
            h("td", { class: "ak-num" }, share(p.weight)),
            h("td", { class: "ak-num" }, p.pnl ? [signed(p.pnl.local), h("small", { class: "pf-markus" }, `${p.currency} · ${pct(p.pnl_pct)}`)] : "–"),
            h("td", { class: "ak-num" }, p.pnl ? [signed(p.pnl.eur), h("small", { class: "pf-markus" }, "hinnast ", signed(p.pnl.price), ", valuutast ", signed(p.pnl.fx))] : "–"),
            h("td", { class: "ak-num" }, p.yield_on_cost == null ? "–" : share(p.yield_on_cost, 2)),
          ),
        ),
      ),
    ),
  );
}

// --- Value over time: the account value (line with a wash) and the net money put in (dashed
// step), one axis in EUR. Crosshair and readout on hover and on the arrow keys.

/** Round tick step for a range: 1, 2 or 5 times a power of ten. */
function niceStep(range, count = 4) {
  const raw = range / count || 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
}

const shortDay = new Intl.DateTimeFormat("et-EE", { month: "short", year: "numeric" });

function historyChart(points) {
  const wrap = h("div", { class: "pf-graafik" });
  const readout = h("div", { class: "pf-naidik", hidden: true, "aria-live": "polite" });
  const legend = h(
    "ul",
    { class: "pf-legend" },
    h("li", {}, svg("svg", { width: 22, height: 10, "aria-hidden": "true" }, svg("line", { x1: 1, y1: 5, x2: 21, y2: 5, class: "pf-joon-vaartus" })), "Väärtus"),
    h("li", {}, svg("svg", { width: 22, height: 10, "aria-hidden": "true" }, svg("line", { x1: 1, y1: 5, x2: 21, y2: 5, class: "pf-joon-sisse" })), "Sissemakstud neto"),
  );
  const table = h(
    "details",
    { class: "pf-tabelina" },
    h("summary", {}, "Tabelina"),
    h(
      "div",
      { class: "ak-tabel-kerib" },
      h(
        "table",
        { class: "ak-tabel" },
        h("thead", {}, h("tr", {}, h("th", {}, "Kuupäev"), h("th", { class: "ak-num" }, "Väärtus EUR"), h("th", { class: "ak-num" }, "Sissemakstud EUR"))),
        h("tbody", {}, [...points].reverse().map((p) => h("tr", {}, h("td", {}, day(p.date)), h("td", { class: "ak-num" }, num(p.value)), h("td", { class: "ak-num" }, num(p.invested))))),
      ),
    ),
  );
  const plot = h("div", { class: "pf-joonis" });
  wrap.append(legend, plot, readout, table);

  function draw() {
    plot.replaceChildren();
    const width = Math.max(plot.clientWidth || 640, 280);
    const height = 220;
    const pad = { top: 12, right: 12, bottom: 24, left: 64 };
    const t = points.map((p) => new Date(`${p.date}T12:00`).getTime());
    const values = points.flatMap((p) => [Number(p.value), Number(p.invested)]);
    const low = Math.min(0, ...values);
    const step = niceStep(Math.max(...values) - low);
    const top = Math.ceil(Math.max(...values, 1) / step) * step;
    const bottom = Math.floor(low / step) * step;
    const x = (ms) => pad.left + ((ms - t[0]) / (t[t.length - 1] - t[0] || 1)) * (width - pad.left - pad.right);
    const y = (v) => pad.top + (1 - (v - bottom) / (top - bottom || 1)) * (height - pad.top - pad.bottom);

    const grid = [];
    for (let v = bottom; v <= top + step / 2; v += step) {
      grid.push(svg("line", { x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v), class: "pf-ruudustik" }));
      grid.push(svg("text", { x: pad.left - 6, y: y(v) + 4, "text-anchor": "end", class: "pf-telg" }, num(v, 0)));
    }
    const months = [];
    const first = new Date(t[0]);
    const span = (t[t.length - 1] - t[0]) / 864e5;
    const every = span > 1100 ? 12 : span > 500 ? 6 : span > 200 ? 3 : 1;
    for (let d = new Date(first.getFullYear(), first.getMonth() + 1, 1); d.getTime() <= t[t.length - 1]; d.setMonth(d.getMonth() + 1)) {
      if (d.getMonth() % every) continue;
      months.push(svg("text", { x: x(d.getTime()), y: height - 6, "text-anchor": "middle", class: "pf-telg" }, shortDay.format(d)));
    }

    const line = points.map((p, i) => `${i ? "L" : "M"}${x(t[i]).toFixed(1)},${y(Number(p.value)).toFixed(1)}`).join("");
    const area = `${line}L${x(t[t.length - 1]).toFixed(1)},${y(bottom)}L${x(t[0]).toFixed(1)},${y(bottom)}Z`;
    // The money put in changes on the day of a flow and stays until the next one.
    const steps = points.map((p, i) => `${i ? `L${x(t[i]).toFixed(1)},${y(Number(points[i - 1].invested)).toFixed(1)}L` : "M"}${x(t[i]).toFixed(1)},${y(Number(p.invested)).toFixed(1)}`).join("");
    const cross = svg("line", { class: "pf-rist", y1: pad.top, y2: height - pad.bottom, visibility: "hidden" });
    const dotValue = svg("circle", { r: 4, class: "pf-punkt-vaartus", visibility: "hidden" });
    const dotIn = svg("circle", { r: 4, class: "pf-punkt-sisse", visibility: "hidden" });
    const last = points[points.length - 1];
    const chart = svg(
      "svg",
      { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: 0, "aria-label": `Portfelli väärtus ajas. Viimati ${num(last.value)} EUR, sissemakstud ${num(last.invested)} EUR. Nooleklahvid liiguvad punktide vahel.` },
      grid,
      months,
      svg("path", { d: area, class: "pf-ala" }),
      svg("path", { d: steps, class: "pf-joon-sisse" }),
      svg("path", { d: line, class: "pf-joon-vaartus" }),
      svg("circle", { cx: x(t[t.length - 1]), cy: y(Number(last.value)), r: 4, class: "pf-punkt-vaartus" }),
      cross,
      dotIn,
      dotValue,
    );

    let current = null;
    function show(i) {
      current = Math.max(0, Math.min(points.length - 1, i));
      const p = points[current];
      const px = x(t[current]);
      for (const [el, v] of [[dotValue, p.value], [dotIn, p.invested]]) {
        el.setAttribute("cx", px);
        el.setAttribute("cy", y(Number(v)));
        el.setAttribute("visibility", "visible");
      }
      cross.setAttribute("x1", px);
      cross.setAttribute("x2", px);
      cross.setAttribute("visibility", "visible");
      readout.replaceChildren(h("strong", {}, day(p.date)), h("span", {}, h("b", {}, `${num(p.value)} EUR`), " väärtus"), h("span", {}, h("b", {}, `${num(p.invested)} EUR`), " sissemakstud"));
      readout.hidden = false;
      readout.style.left = `${Math.min(px + 12, width - 200)}px`;
    }
    function hide() {
      for (const el of [cross, dotValue, dotIn]) el.setAttribute("visibility", "hidden");
      readout.hidden = true;
      current = null;
    }
    chart.addEventListener("pointermove", (e) => {
      const box = chart.getBoundingClientRect();
      const ms = t[0] + ((e.clientX - box.left - pad.left) / (width - pad.left - pad.right)) * (t[t.length - 1] - t[0]);
      let best = 0;
      t.forEach((v, i) => Math.abs(v - ms) < Math.abs(t[best] - ms) && (best = i));
      show(best);
    });
    chart.addEventListener("pointerleave", hide);
    chart.addEventListener("blur", hide);
    chart.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      show(current == null ? points.length - 1 : current + (e.key === "ArrowRight" ? 1 : -1));
    });
    plot.append(chart);
  }

  let timer = null;
  const observer = new ResizeObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(draw, 100);
  });
  requestAnimationFrame(() => {
    draw();
    observer.observe(plot);
  });
  return wrap;
}

// --- Allocation: one horizontal bar per slice, one hue (the length is the value), the share at
// the tip and the EUR value in the row.
function allocation(groups) {
  const names = { instrument: "Väärtpaber", currency: "Valuuta", kind: "Liik" };
  return h(
    "div",
    { class: "pf-jaotus", id: "pf-jaotus" },
    Object.entries(names).map(([key, title]) =>
      h(
        "figure",
        { class: "pf-ribad", "data-jaotus": key },
        h("figcaption", {}, title),
        h(
          "ul",
          {},
          (groups[key] || []).map((s) =>
            h(
              "li",
              { title: `${s.label}: ${share(s.weight)}, ${num(s.value)} EUR` },
              h("span", { class: "pf-silt" }, s.label),
              svg("svg", { class: "pf-riba", viewBox: "0 0 100 12", preserveAspectRatio: "none", "aria-hidden": "true" }, svg("rect", { x: 0, y: 1, height: 10, rx: 2, width: Math.max(Number(s.weight) * 100, 0.5) })),
              h("span", { class: "ak-num pf-osa" }, share(s.weight)),
              h("span", { class: "ak-num pf-eur" }, `${num(s.value, 0)} EUR`),
            ),
          ),
        ),
      ),
    ),
  );
}

function dividendTables(d) {
  if (!d.recent.length) return h("p", { class: "leht-juhend" }, "Dividende pole veel saadud.");
  return h(
    "div",
    { class: "pf-kaks" },
    h(
      "table",
      { class: "ak-tabel", id: "pf-dividendiaastad" },
      h("caption", {}, "Aastate kaupa, EUR"),
      h("thead", {}, h("tr", {}, h("th", {}, "Aasta"), h("th", { class: "ak-num" }, "Bruto"), h("th", { class: "ak-num" }, "Kinni peetud"), h("th", { class: "ak-num" }, "Neto"))),
      h("tbody", {}, d.years.map((y) => h("tr", {}, h("td", {}, String(y.year)), h("td", { class: "ak-num" }, num(y.gross)), h("td", { class: "ak-num" }, num(y.withheld)), h("td", { class: "ak-num" }, num(y.net))))),
    ),
    h(
      "div",
      { class: "ak-tabel-kerib" },
      h(
        "table",
        { class: "ak-tabel", id: "pf-dividendid" },
        h("caption", {}, "Viimased"),
        h("thead", {}, h("tr", {}, h("th", {}, "Kuupäev"), h("th", {}, "Väärtpaber"), h("th", { class: "ak-num" }, "Bruto"), h("th", { class: "ak-num" }, "Kinni peetud"), h("th", { class: "ak-num" }, "Neto EUR"))),
        h(
          "tbody",
          {},
          d.recent.map((x) =>
            h(
              "tr",
              {},
              h("td", {}, day(x.date)),
              h("td", {}, x.symbol),
              h("td", { class: "ak-num" }, money(x.gross, x.currency)),
              h("td", { class: "ak-num" }, `${money(x.withheld, x.currency)} (${share(x.withholding_rate, 0)})`),
              h("td", { class: "ak-num" }, num(x.net_eur)),
            ),
          ),
        ),
      ),
    ),
  );
}

function investmentPanel(ia) {
  const eur = (v) => (v == null ? "–" : money(v, "EUR"));
  const d = ia.deferred;
  const missing = ia.missing_years.map((y) => `Aasta ${y} tulumaksumäär puudub, nii et selle aasta maksu ei arvutata. Lisa see Seadetes (Aktsiad), kui oled EMTA juhendi üle vaadanud.`);
  return section(
    "pf-ik",
    "Investeerimiskonto",
    h("p", { class: "leht-juhend" }, `Kontod: ${ia.accounts.join(", ")}. Algsaldo ${eur(ia.opening_balance)} (Seaded → Aktsiad).`),
    notices([...missing, ...ia.problems]),
    h(
      "dl",
      { class: "pf-kokkuvote" },
      tile("Sissemaksete jääk", eur(ia.balance)),
      d && tile("Kui täna kõik välja võtta, maksustatav", eur(d.taxable_gain), `konto väärtus ${eur(d.account_value)}`),
      d && tile(`Edasilükkunud tulumaks (${share(d.rate, 0)})`, eur(d.tax)),
    ),
    ia.years.length
      ? h(
          "div",
          { class: "ak-tabel-kerib" },
          h(
            "table",
            { class: "ak-tabel", id: "pf-maksuaastad" },
            h("caption", {}, "Tuludeklaratsiooni tabel 6.5 aastate kaupa, EUR"),
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "Aasta"),
                ["Algjääk", "Sissemaksed", "Väljamaksed", "Lõppjääk", "Maksustatav", "Määr", "Tulumaks", "Div. bruto", "Kinni peetud", "Lisamaks"].map((t) => h("th", { class: "ak-num", scope: "col" }, t)),
              ),
            ),
            h(
              "tbody",
              {},
              ia.years.map((y) =>
                h(
                  "tr",
                  {},
                  h("td", {}, String(y.year)),
                  [y.opening_balance, y.contributions, y.withdrawals, y.closing_balance, y.taxable_amount].map((v) => h("td", { class: "ak-num" }, num(v))),
                  h("td", { class: "ak-num" }, y.rate == null ? "–" : share(y.rate, 0)),
                  [y.tax_due, y.dividends_gross, y.dividends_withheld, y.dividend_top_up].map((v) => h("td", { class: "ak-num" }, num(v))),
                ),
              ),
            ),
          ),
        )
      : null,
    h(
      "p",
      { class: "pf-hoiatus", id: "pf-maksuhoiatus" },
      "Arvutus järgib tulumaksuseaduse § 17² ja deklaratsiooni tabelit 6.5; eeldused ja allikad on failis app/stocks/portfolio/TAX_NOTES.md. Dividendid loetakse sissemakseks neto summas ja kõik dividendid välismaisteks. Kontrolli määrad ja reeglid EMTA juhendist enne, kui neile deklaratsioonis tugined.",
    ),
  );
}

export async function mount(el) {
  const data = await api("/api/stocks/portfolio");
  if (!data.has_transactions) {
    el.append(
      h("p", { class: "leht-juhend", id: "pf-tuhi" }, "Tehinguid pole veel. ", h("a", { href: "/aktsiad/tehingud#lisa" }, "Lisa esimene tehing"), " või impordi need CSV-failist."),
    );
    return;
  }
  el.append(
    h("p", { class: "leht-juhend" }, `Seis ${day(data.as_of)}. Kõik kontod koos, eurodes ECB kursiga.`),
    notices(data.problems),
    data.summary && summary(data.summary),
    section("pf-positsioonid-j", "Positsioonid", positions(data.positions)),
    data.history.length > 1 && section("pf-ajalugu", "Väärtus ajas", historyChart(data.history)),
    Object.keys(data.allocation).length > 0 && section("pf-jaotus-j", "Jaotus", allocation(data.allocation)),
    section("pf-dividendid-j", "Dividendid", dividendTables(data.dividends)),
    data.investment && investmentPanel(data.investment),
  );
}
