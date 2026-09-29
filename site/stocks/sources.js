// Aktsiad → Allikad (stocks-sources): per source its last run, failing streak and documents; the
// last 30 nights with their steps; the last 50 model runs. GET /api/stocks/sources; "Loe kohe"
// starts a read of every source (POST /api/stocks/sources/run) and the tab follows it.
import { api, toast } from "/static/app.js";
import { h, num, when } from "./common.js";

export const SOURCE_LABEL = {
  edgar: "SEC EDGAR",
  finnhub: "Finnhub uudised",
  earnings: "Finnhub tulemuste kalender",
  yahoo_rss: "Yahoo RSS",
  google_news: "Google News",
  ir_rss: "Investorisuhete vood",
  alphavantage: "Alpha Vantage",
};
// What a source lacks when it is not set up.
const MISSING = {
  edgar: "SEC User-Agent puudub",
  finnhub: "võti puudub",
  earnings: "Finnhubi võti puudub",
  alphavantage: "võti puudub",
  ir_rss: "vooge pole",
};
const STATUS = { succeeded: "õnnestus", partial: "osaliselt", failed: "ebaõnnestus", running: "käib", skipped: "vahele jäetud", success: "õnnestus", unavailable: "piirang" };
const STEP_LABEL = { prices: "Hinnad", ingest: "Uudised", attention: "Tähelepanu", extract: "Hindamine", research: "Uuringud", verdict: "Hinnangud", notes: "Märkmed", digest: "Kokkuvõte", night: "Öö" };
const PURPOSE = { extract: "Uudiste hindamine", discover: "Allikate otsing", research: "Uuring", deep_dive: "Uuri kohe", explore: "Avasta", verdict: "Hinnang" };
const POLL_MS = 3000;

/** 95000 -> "1 min 35 s" */
function duration(ms) {
  if (ms == null) return "–";
  if (ms < 1000) return `${ms} ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${String(s % 60).padStart(2, "0")} s` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

const between = (from, to) => (from && to ? duration(new Date(to) - new Date(from)) : "–");
const status = (value, text) => h("span", { class: "ak-olek", "data-olek": value }, text || STATUS[value] || value);

function sourceRow(s) {
  const last = s.last;
  const name = h("td", {}, h("strong", {}, SOURCE_LABEL[s.name] || s.name));
  if (s.budget) name.append(h("small", { class: "ak-all-lisa" }, `täna ${s.budget.used}/${s.budget.limit} päringut`));
  const state = h("td", {});
  if (!s.configured && s.name in MISSING) state.append(status("skipped", MISSING[s.name]));
  else if (last) state.append(status(last.status));
  else state.append(h("span", { class: "ak-all-tuhi" }, "pole veel loetud"));
  if (s.failing_streak > 1) state.append(h("small", { class: "ak-all-lisa error" }, `${s.failing_streak} korda järjest ebaõnnestunud`));
  return h(
    "tr",
    { "data-allikas": s.name },
    name,
    h("td", { class: "ak-num" }, last ? when(last.started_at) : "–"),
    state,
    h("td", { class: "ak-num" }, last ? num(last.fetched, 0) : "–"),
    h("td", { class: "ak-num" }, last ? num(last.new, 0) : "–"),
    h("td", { class: "ak-num" }, num(s.documents_7d, 0)),
    h("td", { class: "ak-num" }, num(s.documents, 0)),
    h("td", { class: "ak-all-viga" }, last?.error ? h("span", { title: last.error }, last.error.length > 160 ? `${last.error.slice(0, 159)}…` : last.error) : ""),
  );
}

function table(id, heads, rows) {
  return h(
    "div",
    { class: "ak-tabel-kerib" },
    h("table", { class: "ak-tabel", id }, h("thead", {}, h("tr", {}, heads.map(([text, cls]) => h("th", { class: cls }, text)))), h("tbody", {}, rows)),
  );
}

function sourcesSection(list) {
  return h(
    "section",
    { "aria-labelledby": "ak-all-allikad" },
    h("h2", { class: "sec", id: "ak-all-allikad" }, "Allikad"),
    table(
      "ak-allikad",
      [["Allikas"], ["Viimati", "ak-num"], ["Olek"], ["Loetud", "ak-num"], ["Uusi", "ak-num"], ["7 päeva", "ak-num"], ["Kokku", "ak-num"], ["Viga"]],
      list.map(sourceRow),
    ),
  );
}

function nightsSection(nights) {
  const body = nights.length
    ? table(
        "ak-oo",
        [["Öö"], ["Olek"], ["Algus", "ak-num"], ["Kestus", "ak-num"], ["Sammud"]],
        nights.map((n) =>
          h(
            "tr",
            {},
            h("td", {}, n.run_date.split("-").reverse().join(".")),
            h("td", {}, status(n.status)),
            h("td", { class: "ak-num" }, when(n.started_at)),
            h("td", { class: "ak-num" }, between(n.started_at, n.finished_at)),
            h(
              "td",
              {},
              h(
                "div",
                { class: "ak-sammud" },
                n.steps.map((s) => {
                  const detail = s.detail == null ? "" : typeof s.detail === "string" ? s.detail : JSON.stringify(s.detail);
                  return h("span", { class: "ak-samm", "data-olek": s.status, title: `${STATUS[s.status] || s.status || ""}${detail ? `: ${detail}` : ""}` }, STEP_LABEL[s.name] || s.name);
                }),
              ),
            ),
          ),
        ),
      )
    : h("p", { class: "ak-all-tuhi" }, "Ühtegi ööd pole veel olnud. Öö algab kell 01.30.");
  return h("section", { "aria-labelledby": "ak-all-oo" }, h("h2", { class: "sec", id: "ak-all-oo" }, "Ööd"), body);
}

function aiSection(ai) {
  const t = ai.totals;
  const summary = t.runs
    ? `Viimase 30 päevaga ${num(t.runs, 0)} päringut: ${num(t.success, 0)} õnnestus, ${num(t.failed, 0)} ebaõnnestus, ${num(t.unavailable, 0)} jäi piirangu taha; kokku ${duration(t.duration_ms)}.`
    : "Viimase 30 päevaga pole mudelit kasutatud.";
  const body = ai.runs.length
    ? table(
        "ak-mudel",
        [["Algus", "ak-num"], ["Eesmärk"], ["Aktsia"], ["Mudel"], ["Olek"], ["Kestus", "ak-num"]],
        ai.runs.map((r) =>
          h(
            "tr",
            {},
            h("td", { class: "ak-num" }, when(r.started_at)),
            h("td", {}, PURPOSE[r.purpose] || r.purpose),
            h("td", {}, r.symbol ? h("a", { href: `/aktsiad/${encodeURIComponent(r.symbol)}` }, r.symbol) : ""),
            h("td", {}, r.model),
            h("td", {}, status(r.status), r.error ? h("small", { class: "ak-all-lisa" }, r.error) : null),
            h("td", { class: "ak-num" }, duration(r.duration_ms)),
          ),
        ),
      )
    : null;
  return h("section", { "aria-labelledby": "ak-all-mudel" }, h("h2", { class: "sec", id: "ak-all-mudel" }, "Mudeli päringud"), h("p", { class: "ak-all-kokku" }, summary), body);
}

export async function mount(el) {
  const button = h("button", { type: "button", class: "primary" }, "Loe kohe");
  const note = h("p", { class: "leht-juhend" }, "Uudised ja börsiteated loetakse igal ööl, SEC teated ka iga tund päeval. Võtmed: Seaded → Ühendused → Turuandmed.");
  const body = h("div", { class: "ak-all-sisu", "aria-live": "polite" });
  el.append(h("div", { class: "ak-allikad" }, h("div", { class: "leht-pais" }, note, button), body));
  let timer = null;

  async function load() {
    let data;
    try {
      data = await api("/api/stocks/sources");
    } catch (err) {
      body.replaceChildren(h("p", { class: "error" }, err.message));
      return;
    }
    body.replaceChildren(sourcesSection(data.sources), nightsSection(data.nights), aiSection(data.ai));
    button.disabled = data.running;
    button.textContent = data.running ? "Loen allikaid…" : "Loe kohe";
    clearTimeout(timer);
    if (data.running) timer = setTimeout(load, POLL_MS);
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api("/api/stocks/sources/run", { method: "POST" });
      toast("Loen allikaid. Tabel uueneb, kui valmis.");
    } catch (err) {
      toast(err.message, "error");
    }
    await load();
  });
  await load();
}
