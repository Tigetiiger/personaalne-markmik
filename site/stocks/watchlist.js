// Aktsiad → Jälgin: the watchlist table (price, day, 1 k, 1 a, a 90-day sparkline, held, the last
// research and the verdict), search and add, remove, note and tags. Owner: stocks-market.
import { api, toast } from "/static/app.js";
import { day, direction, h, num, pct, svg, verdictBadge } from "./common.js";

const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "SEK", "DKK", "NOK", "CAD"];
const POLL_MS = 3000;

/** A signed change with data-suund: the sign carries the direction, the colour only repeats it. */
function change(value) {
  return value == null ? h("span", { class: "ak-tuhi" }, "–") : h("span", { "data-suund": direction(value) }, pct(value, 1));
}

/** 90 days of closes as a line; its direction is also in the label and the 1 k column. */
function sparkline(values) {
  const nums = values.map(Number);
  if (nums.length < 2) return h("span", { class: "ak-tuhi" }, "–");
  const w = 96, hgt = 24, pad = 2.5;
  const lo = Math.min(...nums), hi = Math.max(...nums), span = hi - lo || 1;
  const x = (i) => pad + (i * (w - 2 * pad)) / (nums.length - 1);
  const y = (v) => pad + (hgt - 2 * pad) * (1 - (v - lo) / span);
  const points = nums.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const move = nums[nums.length - 1] / nums[0] - 1;
  return svg(
    "svg",
    { class: "ak-spark", viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, role: "img", "aria-label": `90 päeva: ${pct(move, 1)}`, "data-suund": direction(move) },
    svg("polyline", { points, fill: "none" }),
    svg("circle", { cx: x(nums.length - 1).toFixed(1), cy: y(nums[nums.length - 1]).toFixed(1), r: 2.5 }),
  );
}

export function mount(el) {
  const search = h("input", { type: "search", id: "ak-otsi", placeholder: "nt NVDA, Apple, VWCE", autocomplete: "off", spellcheck: "false" });
  const matches = h("ul", { class: "ak-leiud", "aria-live": "polite" });
  const form = h(
    "form",
    { class: "ak-otsing", role: "search" },
    h("label", { for: "ak-otsi" }, "Lisa jälgimisse"),
    h("div", { class: "ak-otsing-rida" }, search, h("button", { type: "submit", class: "primary" }, "Otsi")),
  );
  const list = h("div", { class: "ak-jalgin" });
  el.append(form, matches, list);

  let rows = [];
  let editing = null;
  let pollTimer = null;

  async function load() {
    rows = await api("/api/stocks/watchlist");
    render();
    clearTimeout(pollTimer);
    // While a backfill runs the row says "Laen ajalugu…"; look again until its bars are in.
    if (rows.some((r) => r.loading)) pollTimer = setTimeout(() => load().catch(() => {}), POLL_MS);
  }

  function render() {
    list.replaceChildren();
    if (!rows.length) {
      list.append(h("p", { class: "leht-juhend ak-tuhi-leht" }, "Sa ei jälgi veel ühtki aktsiat. Otsi ülalt sümbolit või nime."));
      return;
    }
    const groups = [
      ["Aktsiad", rows.filter((r) => r.kind !== "etf")],
      ["ETF-id", rows.filter((r) => r.kind === "etf")],
    ].filter(([, g]) => g.length);
    for (const [title, group] of groups) {
      list.append(
        h("h2", { class: "sec" }, title),
        h(
          "div",
          { class: "ak-tabel-kerib" },
          h(
            "table",
            { class: "ak-tabel ak-jalgin-tabel" },
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", { scope: "col" }, "Sümbol"),
                h("th", { scope: "col", class: "ak-num" }, "Hind"),
                h("th", { scope: "col", class: "ak-num" }, "Päev"),
                h("th", { scope: "col", class: "ak-num", title: "Muutus ühe kuuga" }, "1 k"),
                h("th", { scope: "col", class: "ak-num", title: "Muutus ühe aastaga" }, "1 a"),
                h("th", { scope: "col" }, "90 päeva"),
                h("th", { scope: "col", class: "ak-num" }, "Hoian"),
                h("th", { scope: "col" }, "Uuring"),
                h("th", { scope: "col" }, "Hinnang"),
                h("th", { scope: "col" }, h("span", { class: "ak-peidus" }, "Toimingud")),
              ),
            ),
            h("tbody", {}, group.flatMap((r) => rowView(r))),
          ),
        ),
      );
    }
  }

  function rowView(r) {
    const price = r.loading && !r.has_bars
      ? h("td", { class: "ak-num ak-laeb", colspan: 5 }, "Laen ajalugu…")
      : [
          h("td", { class: "ak-num" }, r.price ? `${num(r.price)} ${r.currency}` : h("span", { class: "ak-tuhi" }, "Ajalugu puudub")),
          h("td", { class: "ak-num" }, change(r.day_change)),
          h("td", { class: "ak-num" }, change(r.ret_1m)),
          h("td", { class: "ak-num" }, change(r.ret_1y)),
          h("td", {}, sparkline(r.spark)),
        ];
    const tr = h(
      "tr",
      { "data-symbol": r.symbol },
      h(
        "td",
        { class: "ak-nimi" },
        h("a", { href: `/aktsiad/${encodeURIComponent(r.symbol)}`, class: "ak-symbol" }, r.symbol),
        h("span", { class: "ak-firma" }, r.name),
        r.note && h("span", { class: "ak-markus" }, r.note),
        r.tags.length > 0 && h("span", { class: "ak-sildid" }, r.tags.map((t) => h("span", { class: "ak-silt" }, `#${t}`))),
      ),
      price,
      h("td", { class: "ak-num" }, r.held ? num(r.held, Number(r.held) % 1 ? 4 : 0) : ""),
      h("td", { class: "ak-kp" }, r.last_analysis ? day(r.last_analysis) : ""),
      h("td", {}, verdictBadge(r.verdict)),
      h(
        "td",
        { class: "ak-nupud" },
        h("button", { type: "button", class: "ghost", "aria-label": `Muuda ${r.symbol} märkust ja silte`, "aria-expanded": String(editing === r.id), onclick: () => ((editing = editing === r.id ? null : r.id), render()) }, "Muuda"),
        h("button", { type: "button", class: "ghost", "aria-label": `Eemalda ${r.symbol} jälgimisest`, onclick: () => remove(r) }, "Eemalda"),
      ),
    );
    return editing === r.id ? [tr, editView(r)] : [tr];
  }

  function editView(r) {
    const note = h("textarea", { name: "note", rows: 2, maxlength: 2000 }, r.note);
    const tags = h("input", { name: "tags", value: r.tags.join(", "), autocomplete: "off", placeholder: "nt tehnoloogia, dividend" });
    const f = h(
      "form",
      { class: "vorm ak-muuda" },
      h("label", { class: "vali" }, h("span", {}, "Märkus"), note),
      h("label", { class: "vali" }, h("span", {}, "Sildid"), tags, h("small", {}, "Komadega eraldatud.")),
      h("div", { class: "ak-nupurida" }, h("button", { type: "submit", class: "primary" }, "Salvesta"), h("button", { type: "button", onclick: () => ((editing = null), render()) }, "Loobu")),
    );
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const saved = await api(`/api/stocks/watchlist/${r.id}`, { method: "PATCH", body: { note: note.value, tags: tags.value } });
        rows = rows.map((x) => (x.id === r.id ? saved : x));
        editing = null;
        render();
        toast(`${r.symbol} salvestatud.`);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    queueMicrotask(() => note.focus());
    return h("tr", { class: "ak-muuda-rida" }, h("td", { colspan: 10 }, f));
  }

  async function remove(r) {
    if (!confirm(`Eemaldada ${r.symbol} jälgimisest? Hinnaajalugu jääb alles.`)) return;
    try {
      await api(`/api/stocks/watchlist/${r.id}`, { method: "DELETE" });
      rows = rows.filter((x) => x.id !== r.id);
      render();
      toast(`${r.symbol} eemaldatud.`);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = search.value.trim();
    matches.replaceChildren();
    if (!q) return;
    matches.append(h("li", { class: "ak-tuhi" }, "Otsin…"));
    let found;
    try {
      found = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`);
    } catch (err) {
      matches.replaceChildren(h("li", { class: "error" }, err.message));
      return;
    }
    const followed = new Set(rows.map((r) => r.symbol));
    matches.replaceChildren(
      ...(found.length
        ? found.map((m) => matchView(m, followed.has(m.symbol)))
        : [h("li", { class: "ak-tuhi" }, `„${q}“ ei leidnud midagi.`)]),
    );
  });

  function matchView(m, followed) {
    const currency = h(
      "select",
      { "aria-label": `${m.symbol} valuuta` },
      [...new Set([m.currency, ...CURRENCIES])].map((c) => h("option", { value: c, selected: c === m.currency }, c)),
    );
    const add = h("button", { type: "button", class: "primary", disabled: followed }, followed ? "Jälgid" : "Lisa");
    add.addEventListener("click", async () => {
      add.disabled = true;
      try {
        const row = await api("/api/stocks/watchlist", { method: "POST", body: { symbol: m.symbol, name: m.name, exchange: m.exchange, currency: currency.value, kind: m.kind } });
        toast(`${row.symbol} lisatud.`);
        add.textContent = "Jälgid";
        await load();
      } catch (err) {
        add.disabled = false;
        toast(err.message, "error");
      }
    });
    return h(
      "li",
      { "data-symbol": m.symbol },
      h("span", { class: "ak-symbol" }, m.symbol),
      h("span", { class: "ak-firma" }, m.name),
      h("span", { class: "ak-tuhi" }, `${m.exchange} · ${m.kind === "etf" ? "ETF" : "aktsia"}`),
      currency,
      add,
    );
  }

  return load();
}
