// The stock page /aktsiad/<SYMBOL>: price and day change, the chart (TradingView Lightweight
// Charts, loaded on this page only: candles and volume, SMA 50 and a dashed SMA 200, the vs-SPY
// pane, E/R markers, ranges 1D-5Y with the bar size following the range, a crosshair readout),
// the trend card and key stats. Then every section other packages registered, in order.
// Owner: stocks-market.
import { api, on, toast } from "/static/app.js";
import { day, direction, h, listTickerSections, money, num, pct } from "./common.js";

// label, bar set, bars shown from the end (0 = all, -1 = the year to date). Short ranges get finer
// bars, long ones coarser (stock-oracle's PLAN, Phase 11): 1D 5-minute, 5D 15-minute, 1M hourly,
// 3M-1Y daily, 2Y weekly, 5Y monthly.
const RANGES = [
  ["1D", "intraday", 0],
  ["5D", "intraday", 0],
  ["1M", "intraday", 0],
  ["3M", "daily", 63],
  ["6M", "daily", 126],
  ["YTD", "daily", -1],
  ["1Y", "daily", 252],
  ["2Y", "weekly", 104],
  ["5Y", "monthly", 0],
];
const BAR_NOTES = { "5m": "5-minutilised tulbad", "15m": "15-minutilised tulbad", "1h": "tunnitulbad", daily: "päevatulbad", weekly: "nädalatulbad", monthly: "kuutulbad" };
const POLL_MS = 3000;

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (v) => (v == null ? "–" : num(v, 2));

/** A fraction as "+1,2 %" with data-suund; "–" when missing. */
function change(value, digits = 1) {
  return value == null ? h("span", { class: "ak-tuhi" }, "–") : h("span", { "data-suund": direction(value) }, pct(value, digits));
}

export async function mount(el, symbol) {
  let data;
  try {
    data = await api(`/api/stocks/instruments/${encodeURIComponent(symbol)}`);
  } catch (err) {
    if (err.status !== 404) throw err;
    el.append(notFound(symbol));
    return;
  }
  const head = h("header", { class: "ak-pais" });
  const chartBox = h("section", { class: "ak-graafik", "aria-label": "Hinnagraafik" });
  const side = h("div", { class: "ak-kylg" });
  el.append(head, h("div", { class: "ak-leht-ruudustik" }, chartBox, side));

  const render = async () => {
    head.replaceChildren(...header(data));
    side.replaceChildren(trendCard(data.trend), statsCard(data));
    chartBox.replaceChildren();
    if (data.has_bars) await drawChart(chartBox, data);
    else chartBox.append(h("p", { class: "ak-laeb" }, data.loading ? "Laen ajalugu…" : "Hinnaajalugu pole veel salvestatud. Öine uuendus laeb selle."));
  };
  await render();
  if (!data.has_bars && data.loading) {
    // The 5-year backfill runs on a thread after adding; draw the chart once its bars are in.
    const poll = async () => {
      data = await api(`/api/stocks/instruments/${encodeURIComponent(symbol)}`);
      if (data.has_bars || !data.loading) await render();
      else setTimeout(() => poll().catch(() => {}), POLL_MS);
    };
    setTimeout(() => poll().catch(() => {}), POLL_MS);
  }
  await mountSections(el, { symbol: data.instrument.symbol, instrument: data.instrument });
}

function notFound(symbol) {
  const add = h("button", { type: "button", class: "primary" }, "Lisa jälgimisse");
  add.addEventListener("click", async () => {
    add.disabled = true;
    try {
      await api("/api/stocks/watchlist", { method: "POST", body: { symbol } });
      location.reload();
    } catch (err) {
      add.disabled = false;
      toast(err.message, "error");
    }
  });
  return h("div", { class: "ak-tundmatu" }, h("p", { class: "leht-juhend" }, `Sümbolit ${symbol} siin veel pole.`), add);
}

function header(d) {
  const i = d.instrument;
  const last = d.last;
  return [
    h(
      "div",
      {},
      h("h1", {}, i.symbol, " ", h("span", { class: "ak-liik" }, i.kind === "etf" ? "ETF" : i.kind === "index" ? "indeks" : "aktsia")),
      h("p", { class: "sub" }, [i.name, i.exchange, i.currency].filter(Boolean).join(" · ")),
      d.watch?.note && h("p", { class: "ak-markus" }, d.watch.note),
      d.watch?.tags?.length > 0 && h("p", { class: "ak-sildid" }, d.watch.tags.map((t) => h("span", { class: "ak-silt" }, `#${t}`))),
    ),
    last &&
      h(
        "dl",
        { class: "ak-hind" },
        h("div", { class: "ak-hind-suur" }, h("dt", {}, `Viimane hind, ${day(last.as_of)}`), h("dd", {}, money(last.price, i.currency))),
        h("div", {}, h("dt", {}, "Päev"), h("dd", {}, change(last.day_change, 2))),
      ),
  ];
}

const STATE = {
  uptrend: ["▲", "Tõusutrend", "hind on mõlemast keskmisest kõrgemal ja 50 päeva keskmine 200 päeva omast kõrgemal."],
  downtrend: ["▼", "Langustrend", "hind on mõlemast keskmisest madalamal ja 50 päeva keskmine 200 päeva omast madalamal."],
  mixed: ["◆", "Segane", "hind ja keskmised ei ole ühel meelel."],
};

function figure(label, value) {
  return h("div", {}, h("dt", {}, label), h("dd", {}, value));
}

function trendCard(t) {
  const card = h("section", { class: "ak-kaart ak-trend", "aria-labelledby": "ak-trend-pealkiri" }, h("h2", { class: "sec", id: "ak-trend-pealkiri" }, "Trend"));
  if (!t) return card.append(h("p", { class: "ak-tuhi" }, "Hindu pole veel.")), card;
  const state = STATE[t.state];
  card.append(
    h(
      "p",
      { class: "ak-seis", "data-seis": t.state || "none" },
      state ? [h("strong", { "data-suund": t.state === "uptrend" ? "up" : t.state === "downtrend" ? "down" : "flat" }, `${state[0]} ${state[1]}`), `: ${state[2]}`] : "200 päeva keskmise jaoks pole veel piisavalt ajalugu.",
    ),
  );
  const cross = t.last_cross ? `${t.last_cross.kind === "golden" ? "Kuldne rist" : "Surmarist"} ${day(t.last_cross.date)}` : "–";
  card.append(
    h(
      "dl",
      { class: "ak-numbrid" },
      figure("50 p keskmisest", change(t.vs_sma_50)),
      figure("200 p keskmisest", change(t.vs_sma_200)),
      figure("Viimane 50/200 ristumine", cross),
      figure("52 nädala tipust", change(t.drawdown)),
      Object.keys(t.excess).length > 0 && [
        figure("SPY vastu, 20 p", change(t.excess["20"])),
        figure("SPY vastu, 60 p", change(t.excess["60"])),
        figure("SPY vastu, 1 a", change(t.excess["252"])),
      ],
    ),
  );
  if (t.range_position != null) {
    const at = Math.round(t.range_position * 100);
    card.append(
      h(
        "div",
        { class: "ak-vahemik", role: "img", "aria-label": `Hind on 52 nädala vahemiku ${at} % kõrgusel` },
        h("span", {}, num(t.low_52w)),
        h("span", { class: "ak-vahemik-rada" }, h("span", { class: "ak-vahemik-mark", style: `left: ${at}%` })),
        h("span", {}, num(t.high_52w)),
      ),
      h("p", { class: "ak-selgitus" }, "Kus hind on oma 52 nädala madalaima ja kõrgeima vahel. Keskmised on sulgemishindadest; vahemik ja SPY võrdlus dividendidega korrigeeritud hindadest."),
    );
  }
  return card;
}

function statsCard(d) {
  const s = d.stats;
  const card = h("section", { class: "ak-kaart ak-statistika", "aria-labelledby": "ak-stat-pealkiri" }, h("h2", { class: "sec", id: "ak-stat-pealkiri" }, "Põhinäitajad"));
  if (!s.last_close) return card.append(h("p", { class: "ak-tuhi" }, "Hindu pole veel.")), card;
  card.append(
    h(
      "dl",
      { class: "ak-numbrid" },
      figure("52 nädala kõrgeim", num(s.high_52w)),
      figure("52 nädala madalaim", num(s.low_52w)),
      figure("5 päeva", change(s.ret_5d)),
      figure("20 päeva", change(s.ret_20d)),
      figure("60 päeva", change(s.ret_60d)),
      figure("1 aasta", change(s.ret_1y)),
      figure("Volatiilsus 20 p, aastas", s.vol_20d == null ? "–" : pct(s.vol_20d, 1).replace("+", "")),
      figure("Volatiilsus 60 p, aastas", s.vol_60d == null ? "–" : pct(s.vol_60d, 1).replace("+", "")),
    ),
    h("p", { class: "ak-selgitus" }, "Tootlused dividendidega korrigeeritud sulgemishindadest."),
  );
  return card;
}

async function mountSections(el, ctx) {
  const sections = listTickerSections().map((s) => {
    const box = h("section", { class: "ak-jaotis", "data-jaotis": s.id });
    if (s.title) box.append(h("h2", { class: "sec" }, s.title));
    el.append(box);
    return [s, box];
  });
  await Promise.all(
    sections.map(async ([s, box]) => {
      try {
        await s.mount(box, ctx);
      } catch (err) {
        console.error(`stock page section ${s.id} failed`, err);
        box.append(h("p", { class: "error" }, "Seda osa ei õnnestunud laadida."));
      }
    }),
  );
}

// --- The chart ------------------------------------------------------------------------------------

/** Lightweight Charts gives a daily bar's time back as a string or a {year, month, day}. */
function timeKey(t) {
  if (t && typeof t === "object") return `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;
  return t;
}

function colours() {
  return {
    ink: css("--ink-2"),
    line: css("--line"),
    up: css("--ok"),
    down: css("--danger"),
    // Two series: the accent and a second hue apart from it in both themes (checked with the
    // dataviz validator); the 200-day line is dashed as well, so colour is never the only cue.
    fast: css("--slate"),
    slow: css("--warn"),
    font: getComputedStyle(document.body).fontFamily,
  };
}

async function drawChart(box, data) {
  const symbol = data.instrument.symbol;
  const [LC, daily] = await Promise.all([import("/static/vendor/lightweight-charts.js"), api(`/api/stocks/chart/${encodeURIComponent(symbol)}`)]);
  const withRelative = daily.relative.length > 0;

  const note = h("span", { class: "ak-tulbad", "aria-live": "polite" });
  const buttons = RANGES.map(([label, set, bars]) => h("button", { type: "button", "data-range": label, "data-set": set, "data-bars": bars, "aria-pressed": String(label === "1Y") }, label));
  const readout = h("p", { class: "ak-lugem", "aria-live": "off" });
  const toggles = [
    ["sma_50", "SMA 50", "ak-joon"],
    ["sma_200", "SMA 200", "ak-joon ak-joon-katkend"],
  ]
    .filter(([key]) => daily[key].length)
    .map(([key, label, cls]) => h("label", { class: "ak-legend-voti" }, h("input", { type: "checkbox", checked: true, "data-series": key }), h("span", { class: cls, "data-series": key, "aria-hidden": "true" }), label));
  const surface = h("div", { class: "ak-graafik-pind", "data-paanid": withRelative ? "2" : "1" });
  const relativeNote = withRelative && h("p", { class: "ak-selgitus" }, `Alumine paan: tootlus võrreldes SPY-ga alates esimesest näidatud tulbast. Üle 0 % tähendab, et ${symbol} edestab SPY-d.`);
  box.append(
    h("div", { class: "ak-vahemikud", role: "group", "aria-label": "Graafiku vahemik" }, buttons, note),
    h("div", { class: "ak-legend" }, toggles, daily.markers.length > 0 && h("span", { class: "ak-legend-voti ak-tuhi" }, "E tulemused · R uuring")),
    readout,
    surface,
    relativeNote,
    h(
      "p",
      { class: "ak-selgitus ak-omistus" },
      "Hinnad on splittidega korrigeeritud. Graafik: ",
      h("a", { href: "https://www.tradingview.com/", rel: "noopener", target: "_blank" }, "TradingView Lightweight Charts™"),
      ", © TradingView, Inc.",
    ),
  );

  let c = colours();
  const chart = LC.createChart(surface, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: c.ink, fontFamily: c.font, attributionLogo: true },
    grid: { vertLines: { visible: false }, horzLines: { color: c.line } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
    crosshair: { mode: LC.CrosshairMode.Normal },
    localization: { locale: "et-EE" },
  });
  // Prices in Estonian notation (1 234,56); the vs-SPY pane has its own percent format.
  const priceFormat = { type: "custom", minMove: 0.01, formatter: (p) => num(p, 2) };
  const candles = chart.addSeries(LC.CandlestickSeries, { upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false, priceFormat });
  const volume = chart.addSeries(LC.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false, color: c.line });
  volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const lines = {};
  for (const [key, colour, style] of [["sma_50", "fast", LC.LineStyle.Solid], ["sma_200", "slow", LC.LineStyle.Dashed]]) {
    if (daily[key].length) lines[key] = chart.addSeries(LC.LineSeries, { color: c[colour], lineWidth: 2, lineStyle: style, priceFormat, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  }
  const markers = LC.createSeriesMarkers(candles, []);

  // Return vs SPY in a pane of its own (never a second axis on the price pane), rebased to 0 % at
  // the first bar shown. Not on intraday views: SPY's series is daily.
  let relative = null;
  function showRelative(show) {
    if (relativeNote) relativeNote.hidden = !show;
    if (show && !relative && withRelative) {
      relative = chart.addSeries(
        LC.LineSeries,
        { color: c.fast, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), 1)} %` } },
        1,
      );
      relative.createPriceLine({ price: 0, color: c.ink, lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: false });
      const panes = chart.panes();
      if (panes.length > 1) {
        panes[0].setStretchFactor(3);
        panes[1].setStretchFactor(1);
      }
    } else if (!show && relative) {
      chart.removeSeries(relative);
      relative = null;
    }
  }

  const sets = { daily, weekly: daily.weekly, monthly: daily.monthly };
  let current = null;
  let times = [];
  let intraday = false;
  let baseNote = "";

  function snapMarkers() {
    // An event goes on the last bar that starts on or before its date (the week or month holding
    // it); events after the last daily bar are left out.
    const lastDay = daily.candles[daily.candles.length - 1].time;
    const out = [];
    for (const m of daily.markers) {
      if (m.time > lastDay) continue;
      let t = null;
      for (let i = 0; i < times.length && times[i] <= m.time; i++) t = times[i];
      if (!t) continue;
      out.push({ time: t, position: "belowBar", text: m.text, shape: m.kind === "earnings" ? "square" : "circle", color: m.kind === "earnings" ? c.ink : c.fast });
    }
    return out;
  }

  function apply(set, isIntraday) {
    // Clear every series first: one chart cannot mix date and timestamp times.
    for (const line of Object.values(lines)) line.setData([]);
    showRelative(false);
    volume.setData([]);
    candles.setData([]);
    chart.applyOptions({ timeScale: { timeVisible: isIntraday, secondsVisible: false } });
    candles.setData(set.candles);
    volume.setData(set.volume.map((v) => ({ time: v.time, value: v.value })));
    if (!isIntraday) {
      for (const [key, line] of Object.entries(lines)) line.setData(set[key] || []);
      showRelative(true);
    }
    current = set;
    intraday = isIntraday;
    times = set.candles.map((x) => x.time);
    markers.setMarkers(isIntraday ? [] : snapMarkers());
  }

  function rebase(from) {
    if (!relative || !current.relative?.length) return;
    const base = current.relative.find((p) => p.time >= from) || current.relative[0];
    relative.setData(current.relative.map((p) => ({ time: p.time, value: (p.value / base.value - 1) * 100 })));
  }

  const label = (t) => {
    if (typeof t === "number") {
      const d = new Date(t * 1000);
      return `${day(d.toISOString().slice(0, 10))} ${d.toISOString().slice(11, 16)}`;
    }
    if (current === sets.weekly) return `nädal ${day(t)}`;
    if (current === sets.monthly) return t.slice(0, 7).split("-").reverse().join(".");
    return day(t);
  };

  function show(time) {
    if (!times.length) return;
    let i = time == null ? -1 : times.indexOf(timeKey(time));
    if (i < 0) i = times.length - 1;
    const bar = current.candles[i];
    const parts = [label(bar.time), `avam. ${fmt(bar.open)}`, `kõrg. ${fmt(bar.high)}`, `madal. ${fmt(bar.low)}`, `sulg ${fmt(bar.close)}`];
    if (!intraday) {
      for (const [key, name] of [["sma_50", "SMA 50"], ["sma_200", "SMA 200"]]) {
        const p = (current[key] || []).find((x) => x.time === bar.time);
        if (p && lines[key]?.options().visible) parts.push(`${name} ${fmt(p.value)}`);
      }
    }
    if (relative) {
      const r = relative.data().find((x) => x.time === bar.time);
      if (r) parts.push(`SPY vastu ${r.value > 0 ? "+" : r.value < 0 ? "−" : ""}${num(Math.abs(r.value), 1)} %`);
    }
    readout.textContent = parts.join(" · ");
  }
  chart.subscribeCrosshairMove((param) => show(param.time));

  for (const input of toggles.map((t) => t.querySelector("input"))) {
    input.addEventListener("change", () => {
      lines[input.dataset.series]?.applyOptions({ visible: input.checked });
      show(null);
    });
  }

  // Bars to show: a count from the end, -1 for the year to date, 0 for everything. Counting bars
  // keeps the 1Y view on the same first day as the trend card's one-year numbers.
  function view(count) {
    if (!times.length) return;
    let from = null;
    if (!intraday) {
      if (count === -1) from = times.find((t) => t >= `${times[times.length - 1].slice(0, 4)}-01-01`);
      else if (count) from = times[Math.max(0, times.length - 1 - count)];
    }
    rebase(from || times[0]);
    show(null);
    if (!from) chart.timeScale().fitContent();
    else chart.timeScale().setVisibleRange({ from, to: times[times.length - 1] });
  }

  const cache = {};
  function fetchIntraday(range, depth) {
    const key = `${range}:${depth}`;
    cache[key] ||= api(`/api/stocks/chart/${encodeURIComponent(symbol)}/intraday?range=${range}&depth=${depth}`).then((body) => {
      if (!body.candles.length) throw new Error("Selle vahemiku päevasiseseid hindu pole.");
      return body;
    });
    cache[key].catch(() => delete cache[key]);
    return cache[key];
  }

  let intradayRange = null;
  let loading = false;
  async function select(button) {
    const set = button.dataset.set;
    if (set !== "intraday") {
      intradayRange = null;
      apply(sets[set], false);
      baseNote = `${BAR_NOTES[set][0].toUpperCase()}${BAR_NOTES[set].slice(1)}`;
      note.textContent = baseNote;
      view(Number(button.dataset.bars));
      return;
    }
    note.textContent = "Laen…";
    const body = await fetchIntraday(button.dataset.range, 0);
    intradayRange = button.dataset.range;
    apply(body, true);
    baseNote = `${BAR_NOTES[body.interval][0].toUpperCase()}${BAR_NOTES[body.interval].slice(1)}, Tallinna aeg, umbes 15 minutit hilinenud. Keskmised ja SPY võrdlus on päevaandmetest.`;
    note.textContent = baseNote;
    view(0);
  }

  // Reaching the left edge of an intraday view loads the next longer period at the same bar size,
  // keeping the visible window where it was.
  async function loadOlder() {
    if (loading || !intradayRange || !current.more) return;
    loading = true;
    const range = intradayRange;
    note.textContent = "Laen vanemaid tulpi…";
    try {
      const body = await fetchIntraday(range, current.depth + 1);
      if (intradayRange !== range) return; // another range was picked meanwhile
      const visible = chart.timeScale().getVisibleRange();
      apply(body, true);
      if (visible) chart.timeScale().setVisibleRange(visible);
      note.textContent = baseNote;
    } catch (err) {
      note.textContent = err.message;
    } finally {
      loading = false;
    }
  }

  let pending = false;
  chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (!range || !times.length || pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const atEdge = range.from < 1;
      if (intraday) {
        if (atEdge && current.more) loadOlder();
        else if (atEdge && !loading) note.textContent = `${baseNote} Nii vanu tulpi Yahoo selle suurusega ei hoia; vanemate hindade jaoks vali pikem vahemik.`;
        else if (!loading) note.textContent = baseNote;
        return;
      }
      note.textContent = atEdge ? `${baseNote}. Salvestatud ajaloo algus.` : baseNote;
      rebase(times[Math.min(times.length - 1, Math.max(0, Math.ceil(range.from)))]);
    });
  });

  for (const b of buttons) {
    b.addEventListener("click", async () => {
      try {
        await select(b);
        for (const o of buttons) o.setAttribute("aria-pressed", String(o === b));
      } catch (err) {
        note.textContent = err.message; // the old view stays
      }
    });
  }

  // Colours follow the theme.
  on("theme:changed", () => {
    c = colours();
    chart.applyOptions({ layout: { textColor: c.ink, fontFamily: c.font }, grid: { horzLines: { color: c.line } } });
    candles.applyOptions({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down });
    volume.applyOptions({ color: c.line });
    lines.sma_50?.applyOptions({ color: c.fast });
    lines.sma_200?.applyOptions({ color: c.slow });
    relative?.applyOptions({ color: c.fast });
    markers.setMarkers(intraday ? [] : snapMarkers());
  });

  await select(buttons.find((b) => b.dataset.range === "1Y"));
}
