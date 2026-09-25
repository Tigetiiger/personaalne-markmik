// First-run wizard /tere: the Ühendused cards one at a time, in order; each step is answered
// Jätka (done) or Jäta vahele (skipped) and stored in onboarding.
import { api, toast } from "/static/app.js";
import { renderCard } from "./cards.js";
import { busy, h } from "./dom.js";

export async function mount() {
  const state = await api("/api/onboarding");
  let byKey = {};
  try {
    byKey = Object.fromEntries((await api("/api/connections")).map((c) => [c.key, c]));
  } catch (err) {
    toast(err.message, "error"); // the steps still render, and can be skipped
  }
  const progress = document.getElementById("tere-sammud");
  const host = document.getElementById("tere-samm");
  let steps = state.steps;

  function draw() {
    const i = steps.findIndex((s) => !s.status);
    progress.replaceChildren(
      ...steps.map((s, n) => h("li", { "data-status": s.status, "aria-current": n === i ? "step" : null }, s.title)),
    );
    if (i < 0) {
      host.replaceChildren(
        h(
          "div",
          { class: "plokk tere-valmis" },
          h("h2", {}, "Valmis!"),
          h("p", {}, "Kõik sammud on läbitud. Ühendusi saad alati muuta lehel ", h("a", { href: "/seaded/uhendused" }, "Seaded → Ühendused"), "."),
          h("a", { class: "button primary", href: "/tana" }, "Mine Täna lehele"),
        ),
      );
      return;
    }
    const step = steps[i];
    const answer = (status) => (e) =>
      busy(e.currentTarget, async () => {
        steps = (await api(`/api/onboarding/${step.key}`, { method: "POST", body: { status } })).steps;
        draw();
        window.scrollTo(0, 0);
      });
    host.replaceChildren(
      h("p", { class: "tere-loendur" }, `Samm ${i + 1}/${steps.length}`),
      byKey[step.key]
        ? renderCard(byKey[step.key])
        : h("p", { class: "plokk tere-puudub" }, `„${step.title}“ kaarti ei õnnestunud laadida. Jäta see samm vahele ja tee see hiljem lehel Seaded → Ühendused.`),
      h(
        "div",
        { class: "tere-nupud" },
        h("button", { type: "button", onclick: answer("skipped") }, "Jäta vahele"),
        h("button", { type: "button", class: "primary", onclick: answer("done") }, "Jätka"),
      ),
    );
  }
  draw();
}
