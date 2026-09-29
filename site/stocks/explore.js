// Aktsiad → Avasta: stock ideas outside the watchlist (stocks-ai). A run streams its steps from
// POST /api/stocks/explore; the earlier runs come from GET /api/stocks/explore. "Lisa jälgimisse"
// looks the idea's ticker up with the market package's search and adds the listing the owner picks.
import { api, toast } from "/static/app.js";
import { CONVICTION, h, when } from "./common.js";
import { CONFIRM_RUN, catalystText, listBlock, outLink, runStream } from "./research.js";

const HORIZON = { weeks: "nädalad", months: "kuud", years: "aastad" };
const KIND = { stock: "aktsia", etf: "ETF" };
const DROPPED = { "already followed": "juba jälgid", duplicate: "kordus", "no verified links": "ühtegi loetud allikat", "not fetched": "lehte ei loetud", invalid: "vigane" };
const MIN = 3;
const MAX = 10;

export async function mount(el) {
  const focus = h("input", { type: "text", id: "ak-av-fookus", maxlength: 200, placeholder: "nt Euroopa kaitsetööstus, dividendid", autocomplete: "off" });
  const count = h("input", { type: "number", id: "ak-av-arv", min: MIN, max: MAX, value: 6, inputmode: "numeric", required: true });
  const go = h("button", { type: "submit", class: "primary" }, "Avasta");
  const status = h("p", { class: "ak-ai-olek", role: "status", "aria-live": "polite" });
  const form = h(
    "form",
    { class: "ak-avasta-vorm", novalidate: true },
    h("p", { class: "leht-juhend" }, "Avasta otsib veebist aktsiaid ja ETF-e, mida sa veel ei jälgi, loeb leitud lehed ja kirjutab iga idee kohta lühikese põhjenduse koos allikatega. Ideed pole soovitused."),
    h("div", { class: "ak-avasta-rida" }, h("label", { for: "ak-av-fookus" }, "Fookus (valikuline)"), focus),
    h("div", { class: "ak-avasta-rida ak-avasta-arv" }, h("label", { for: "ak-av-arv" }, `Ideid (${MIN}–${MAX})`), count),
    go,
    status,
  );
  const list = h("div", { class: "ak-avastused" });
  el.append(form, list);

  let followed = new Set();
  const load = async () => {
    const [data, watch] = await Promise.all([api("/api/stocks/explore"), api("/api/stocks/watchlist")]);
    followed = new Set(watch.map((r) => r.symbol));
    go.disabled = data.running;
    if (data.running && !status.textContent) status.textContent = "Üks AI-uuring juba käib.";
    list.replaceChildren(
      ...(data.explorations.length ? data.explorations.map(runView) : [h("p", { class: "ak-ai-tuhi" }, "Avastusi pole veel. Kirjuta soovi korral fookus ja vajuta „Avasta“.")]),
    );
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const n = Number(count.value);
    if (!Number.isInteger(n) || n < MIN || n > MAX) {
      status.classList.add("error");
      status.textContent = `Ideede arv peab olema ${MIN}–${MAX}.`;
      return;
    }
    if (!confirm(CONFIRM_RUN)) return;
    go.disabled = true;
    status.classList.remove("error");
    status.textContent = "Alustan…";
    try {
      await runStream("/api/stocks/explore", { focus: focus.value.trim(), count: n }, (text) => (status.textContent = text));
      status.textContent = "Valmis.";
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message;
      toast(err.message, "error");
    }
    await load();
  });

  function runView(run) {
    return h(
      "section",
      { class: "ak-avastus", "data-exploration": run.id },
      h("h2", { class: "sec" }, when(run.created_at), run.focus ? ` · ${run.focus}` : ""),
      h("p", { class: "ak-ai-meta" }, `Ideid: ${run.ideas.length} (küsiti ${run.requested_ideas})`),
      h("p", { class: "ak-ai-kokku" }, run.market_context),
      run.ideas.length ? run.ideas.map(ideaView) : h("p", { class: "ak-ai-tuhi" }, "Ükski idee ei läbinud kontrolli."),
      run.dropped.length
        ? h(
            "details",
            { class: "ak-eemaldatud" },
            h("summary", {}, `Kontrollis eemaldatud (${run.dropped.length})`),
            h("ul", {}, run.dropped.map((d) => h("li", {}, `${d.symbol || "?"}: ${d.kind === "link" ? "link" : "idee"}${d.url ? ` ${d.url}` : ""} (${DROPPED[d.reason] || d.reason})`))),
          )
        : null,
    );
  }

  function ideaView(idea) {
    const picker = h("div", { class: "ak-idee-lisa" });
    const add = h("button", { type: "button", disabled: followed.has(idea.symbol) }, followed.has(idea.symbol) ? "Jälgid" : "Lisa jälgimisse");
    add.addEventListener("click", () => pick(idea, add, picker));
    return h(
      "article",
      { class: "ak-idee", "data-symbol": idea.symbol },
      h(
        "h3",
        {},
        h("span", { class: "ak-symbol" }, idea.symbol),
        ` ${idea.name}`,
        h("span", { class: "ak-idee-sildid" }, [idea.exchange, KIND[idea.kind], idea.horizon && `horisont: ${HORIZON[idea.horizon] || idea.horizon}`, idea.conviction && (CONVICTION[idea.conviction] || idea.conviction)].filter(Boolean).join(" · ")),
      ),
      h("p", {}, h("strong", {}, "Mis see on: "), idea.what_it_is),
      h("p", {}, h("strong", {}, "Miks huvitav: "), idea.why_interesting),
      h("div", { class: "ak-pooled" }, h("div", {}, ...listBlock("Eelseisvad sündmused", (idea.catalysts || []).map(catalystText))), h("div", {}, ...listBlock("Riskid", idea.risks))),
      h("h4", {}, "Allikad"),
      h("ul", { class: "ak-idee-lingid" }, (idea.links || []).map((l) => h("li", {}, outLink(l.url, l.title), l.why ? h("span", { class: "ak-ai-tuhi" }, ` — ${l.why}`) : null))),
      add,
      picker,
    );
  }

  async function pick(idea, add, picker) {
    add.disabled = true;
    picker.replaceChildren(h("p", { class: "ak-ai-tuhi" }, "Otsin noteeringuid…"));
    let found;
    try {
      found = await api(`/api/stocks/search?q=${encodeURIComponent(idea.symbol)}`);
    } catch (err) {
      picker.replaceChildren(h("p", { class: "error" }, err.message));
      add.disabled = false;
      return;
    }
    if (!found.length) {
      picker.replaceChildren(h("p", { class: "ak-ai-tuhi" }, `„${idea.symbol}“ ei leidnud ühtegi noteeringut.`));
      add.disabled = false;
      return;
    }
    picker.replaceChildren(
      h("p", { class: "ak-ai-tuhi" }, "Vali noteering:"),
      h(
        "ul",
        { class: "ak-leiud" },
        found.map((m) => {
          const choose = h("button", { type: "button", class: "primary", disabled: followed.has(m.symbol) }, followed.has(m.symbol) ? "Jälgid" : "Lisa");
          choose.addEventListener("click", async () => {
            choose.disabled = true;
            try {
              const row = await api("/api/stocks/watchlist", { method: "POST", body: { symbol: m.symbol, name: m.name, exchange: m.exchange, currency: m.currency, kind: m.kind } });
              followed.add(row.symbol);
              toast(`${row.symbol} lisatud jälgimisse.`);
              choose.textContent = "Jälgid";
              add.textContent = "Jälgid";
              picker.replaceChildren(h("p", {}, h("a", { href: `/aktsiad/${encodeURIComponent(row.symbol)}` }, `Ava ${row.symbol}`)));
            } catch (err) {
              choose.disabled = false;
              toast(err.message, "error");
            }
          });
          return h("li", { "data-symbol": m.symbol }, h("span", { class: "ak-symbol" }, m.symbol), h("span", { class: "ak-firma" }, m.name), h("span", { class: "ak-tuhi" }, `${m.exchange} · ${KIND[m.kind] || m.kind}`), choose);
        }),
      ),
    );
  }

  return load();
}
