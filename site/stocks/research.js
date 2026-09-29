// Aktsiad: two sections of a stock's page (stocks-ai). "Öine hinnang": the nightly would buy / hold
// / sell call, as words and a shape (GET /api/stocks/verdicts/<symbol>). "Uuring": the research
// notes with their sources, and Uuri kohe, whose steps stream from POST
// /api/stocks/research/<symbol>. Everything from the server goes into text nodes; only http(s)
// addresses become links.
import { api, toast } from "/static/app.js";
import { streamAi } from "/static/ai/sse.js";
import { CONVICTION, day, h, registerTickerSection, verdictBadge } from "./common.js";

const KIND = { deep: "öine süvauuring", on_demand: "Uuri kohe" };
export const CONFIRM_RUN = "See kasutab Claude'i tellimust: mõni päring, kokku mõni minut. Jätkan?";

/** The address if it is a web address, else null: a model's text never becomes a javascript: link. */
export function webHref(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

/** An outside link that opens in a new tab and gets no handle on this one. */
export function outLink(url, text) {
  const href = webHref(url);
  return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text || href) : h("span", {}, text || "");
}

/** [h3, ul] for a list with items, [] otherwise. */
export function listBlock(title, items) {
  return items && items.length ? [h("h3", {}, title), h("ul", {}, items.map((i) => h("li", {}, i)))] : [];
}

export function catalystText(c) {
  return `${c.date ? `${day(c.date)}: ` : ""}${c.event}`;
}

/**
 * Stream an on-demand run: onStep(text) for each step. Resolves with the done event's id; rejects
 * with the run's own Estonian message.
 */
export async function runStream(path, body, onStep) {
  let id = null;
  let failure = null;
  await streamAi(path, body, (ev) => {
    if (ev.type === "progress") onStep(ev.text);
    else if (ev.type === "done") id = ev.id;
    else if (ev.type === "error") failure = ev.message;
  });
  if (failure || id == null) throw new Error(failure || "Ühendus katkes enne lõppu.");
  return id;
}

// --- Öine hinnang -------------------------------------------------------------------------------------

function verdictView(data) {
  if (!data.verdicts.length) {
    return [h("p", { class: "ak-ai-tuhi" }, data.kind === "stock" ? "Hinnangut pole veel. Öösel antakse hinnang igale jälgitavale aktsiale." : "ETF-idele öist hinnangut ei anta.")];
  }
  const [v, ...earlier] = data.verdicts;
  return [
    h("p", { class: "ak-otsus" }, verdictBadge(v), h("span", { class: "ak-kindlus" }, CONVICTION[v.conviction] || v.conviction), h("span", { class: "ak-ai-kuup" }, day(v.as_of))),
    h("p", { class: "ak-ai-kokku" }, v.summary),
    h("div", { class: "ak-pooled" }, h("div", {}, ...listBlock("Põhjused", v.reasons_for)), h("div", {}, ...listBlock("Vastuargumendid", v.reasons_against))),
    ...listBlock("Mis muudaks hinnangut", v.what_would_change),
    earlier.length
      ? h(
          "details",
          { class: "ak-varasemad" },
          h("summary", {}, `Varasemad hinnangud (${earlier.length})`),
          h(
            "ol",
            { class: "ak-varasemad-loend" },
            earlier.map((e) => h("li", {}, h("span", { class: "ak-ai-kuup" }, day(e.as_of)), verdictBadge(e), h("span", { class: "ak-kindlus" }, CONVICTION[e.conviction] || e.conviction), h("span", { class: "ak-varasem-kokku" }, e.summary))),
          ),
        )
      : null,
  ];
}

registerTickerSection("verdict", {
  order: 20,
  async mount(el, { symbol }) {
    const box = h("section", { class: "ak-ai ak-hinnang-jaotis", "aria-labelledby": "ak-hinnang-pealkiri" }, h("h2", { class: "sec", id: "ak-hinnang-pealkiri" }, "Öine hinnang"));
    el.append(box);
    box.append(...verdictView(await api(`/api/stocks/verdicts/${encodeURIComponent(symbol)}`)));
  },
});

// --- Uuring -----------------------------------------------------------------------------------------

function noteView(a, { sourcesOpen = false } = {}) {
  const cites = a.citations || [];
  const cite = (i) => {
    const c = cites[i];
    if (!c) return null;
    const href = webHref(c.url);
    const mark = `[${i + 1}]`;
    return h("sup", { class: "ak-viide" }, href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", title: c.title || href }, mark) : mark);
  };
  const claims = (items, title) =>
    h("div", {}, h("h3", {}, title), items.length ? h("ul", {}, items.map((c) => h("li", {}, c.text, " ", cite(c.citation)))) : h("p", { class: "ak-ai-tuhi" }, "Ükski väide ei läbinud allikate kontrolli."));
  return h(
    "article",
    { class: "ak-uuring", "data-analysis": a.id },
    a.what_changed ? h("p", { class: "ak-muutus" }, h("strong", {}, "Mis muutus: "), a.what_changed) : null,
    h("p", { class: "ak-ai-kokku" }, a.summary),
    h("div", { class: "ak-pooled" }, claims(a.bull || [], "Poolt"), claims(a.bear || [], "Vastu")),
    ...listBlock("Eelseisvad sündmused", (a.catalysts || []).map(catalystText)),
    ...listBlock("Riskid", a.risks),
    ...listBlock("Mis muudaks vaadet", a.what_would_change_my_mind),
    cites.length
      ? h(
          "details",
          { class: "ak-allikad-loend", open: sourcesOpen },
          h("summary", {}, `Allikad (${cites.length})`),
          h(
            "ol",
            {},
            cites.map((c) => h("li", {}, outLink(c.url, c.title), c.published_at ? h("span", { class: "ak-ai-kuup" }, ` ${day(c.published_at)}`) : null, c.quote ? h("q", {}, c.quote) : null)),
          ),
        )
      : null,
  );
}

const meta = (a) => `${day(a.as_of)} · ${KIND[a.kind] || a.kind}`;

registerTickerSection("research", {
  order: 40,
  async mount(el, { symbol }) {
    const path = `/api/stocks/research/${encodeURIComponent(symbol)}`;
    const status = h("p", { class: "ak-ai-olek", role: "status", "aria-live": "polite" });
    const button = h("button", { type: "button", class: "primary" }, "Uuri kohe");
    const body = h("div", { class: "ak-uuring-sisu" });
    const box = h(
      "section",
      { class: "ak-ai ak-uuring-jaotis", "aria-labelledby": "ak-uuring-pealkiri" },
      h("div", { class: "ak-ai-pais" }, h("h2", { class: "sec", id: "ak-uuring-pealkiri" }, "Uuring"), button),
      status,
      body,
    );
    el.append(box);
    let data = await api(path);

    const render = () => {
      button.hidden = !data.can_run;
      button.disabled = data.running;
      if (data.running && !status.textContent) status.textContent = "Üks AI-uuring juba käib.";
      const [latest, ...earlier] = data.analyses;
      if (!latest) {
        body.replaceChildren(h("p", { class: "ak-ai-tuhi" }, `Uuringut pole veel. Süvauuring tehakse seatud öödel${data.can_run ? ", või vajuta „Uuri kohe“" : ""}.`));
        return;
      }
      body.replaceChildren(
        h("p", { class: "ak-ai-meta" }, meta(latest)),
        noteView(latest, { sourcesOpen: true }),
        earlier.length
          ? h(
              "div",
              { class: "ak-ajalugu" },
              h("h3", {}, "Varasemad uuringud"),
              earlier.map((a) => h("details", { class: "ak-varasem-uuring" }, h("summary", {}, h("span", { class: "ak-ai-kuup" }, meta(a)), " ", h("span", { class: "ak-varasem-kokku" }, a.summary)), noteView(a))),
            )
          : null,
      );
    };
    render();

    button.addEventListener("click", async () => {
      if (!confirm(CONFIRM_RUN)) return;
      button.disabled = true;
      status.classList.remove("error");
      status.textContent = "Alustan…";
      try {
        await runStream(path, {}, (text) => (status.textContent = text));
        status.textContent = "Valmis.";
      } catch (err) {
        status.classList.add("error");
        status.textContent = err.message;
        toast(err.message, "error");
      }
      data = await api(path);
      render();
    });
  },
});
