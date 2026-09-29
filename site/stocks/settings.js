// Seaded → Aktsiad (GET/PUT /api/stocks/settings). Owner: lead.
import { api, toast } from "/static/app.js";
import { h } from "./common.js";

const DAYS = [
  ["mon", "E"],
  ["tue", "T"],
  ["wed", "K"],
  ["thu", "N"],
  ["fri", "R"],
  ["sat", "L"],
  ["sun", "P"],
];

function field(label, input, note) {
  return h("label", { class: "vali" }, h("span", {}, label), input, note && h("small", {}, note));
}

// "2025: 0.22" per line <-> {"2025": "0.22"}
const ratesText = (rates) => Object.entries(rates).map(([y, r]) => `${y}: ${r}`).join("\n");
function parseRates(text) {
  const out = {};
  for (const line of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
    const m = line.match(/^(\d{4})\s*[:=]\s*(\S+)$/);
    if (!m) throw new Error(`Rida „${line}“: kirjuta „aasta: määr“, nt „2026: 0.22“.`);
    out[m[1]] = m[2].replace(",", ".");
  }
  return out;
}

// "NVDA https://a https://b" per line <-> {"NVDA": ["https://a", "https://b"]}
const feedsText = (feeds) => Object.entries(feeds).map(([s, urls]) => [s, ...urls].join(" ")).join("\n");
function parseFeeds(text) {
  const out = {};
  for (const line of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
    const [symbol, ...urls] = line.split(/\s+/);
    out[symbol.toUpperCase()] = urls;
  }
  return out;
}

export async function mount(el) {
  const cfg = await api("/api/stocks/settings");
  const form = h(
    "form",
    { class: "vorm ak-seaded" },
    h(
      "fieldset",
      { class: "ak-paevad" },
      h("legend", {}, "Süvauuringu ööd"),
      DAYS.map(([value, label]) => h("label", { class: "linnuke" }, h("input", { type: "checkbox", name: "deep_days", value, checked: cfg.deep_days.includes(value) }), label)),
      h("small", {}, "Öö kuupäev on hommikune: E = pühapäeva öö. Süvauuring otsib veebist ja kirjutab analüüsi."),
    ),
    field(
      "Uuringu mudel",
      h("select", { name: "research_model" }, h("option", { value: "sonnet", selected: cfg.research_model === "sonnet" }, "Sonnet"), h("option", { value: "opus", selected: cfg.research_model === "opus" }, "Opus")),
      "Opuse nädalapiir on sama, mida kasutab su enda Claude Code.",
    ),
    field("Hinnaliikumise teade (%)", h("input", { type: "number", name: "alert_move_pct", min: 0.5, max: 50, step: 0.5, value: cfg.alert_move_pct }), "Teade, kui sinu aktsia liigub päevaga vähemalt nii palju."),
    field("Tähelepanu teade (z)", h("input", { type: "number", name: "alert_attention_z", min: 1, max: 10, step: 0.5, value: cfg.alert_attention_z }), "Teade, kui uudiseid on tavalisest nii palju standardhälbeid rohkem."),
    field("ntfy teema", h("input", { name: "ntfy_topic", value: cfg.ntfy_topic, autocomplete: "off", spellcheck: "false" }), "Aktsiate teated lähevad sellele teemale (server on sama, mis meeldetuletustel)."),
    field("Öine AI-päringute piir", h("input", { type: "number", name: "night_cap", min: 0, max: 300, value: cfg.night_cap })),
    field("Investeerimiskonto maksumäärad", h("textarea", { name: "tax_rates", rows: 3, spellcheck: "false" }, ratesText(cfg.tax_rates)), "Iga rida „aasta: määr“. Kontrolli EMTA lehelt enne, kui edasilükkunud maksu numbrile tugined."),
    field("Investeerimiskonto algsaldo (EUR)", h("input", { name: "opening_balance", value: cfg.opening_balance, inputmode: "decimal" }), "Sissemaksete jääk ajast, mille tehinguid siin pole."),
    field("Investorisuhete RSS-vood", h("textarea", { name: "ir_feeds", rows: 3, spellcheck: "false", placeholder: "NVDA https://nvidianews.nvidia.com/rss" }, feedsText(cfg.ir_feeds)), "Iga rida: sümbol ja üks või mitu voo aadressi."),
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Salvesta")),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = form.elements;
    const button = form.querySelector("[type=submit]");
    button.disabled = true;
    try {
      const body = {
        deep_days: [...form.querySelectorAll("[name=deep_days]:checked")].map((c) => c.value),
        research_model: f.research_model.value,
        alert_move_pct: Number(f.alert_move_pct.value),
        alert_attention_z: Number(f.alert_attention_z.value),
        ntfy_topic: f.ntfy_topic.value,
        night_cap: Number(f.night_cap.value),
        tax_rates: parseRates(f.tax_rates.value),
        opening_balance: f.opening_balance.value,
        ir_feeds: parseFeeds(f.ir_feeds.value),
      };
      await api("/api/stocks/settings", { method: "PUT", body });
      toast("Aktsiate seaded salvestatud.");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      button.disabled = false;
    }
  });
  el.append(h("p", { class: "leht-juhend" }, "Öine aktsiate töö: hinnad, uudised, AI hinnang ja teated. Turuandmete võtmed on Ühenduste all."), form);
}
