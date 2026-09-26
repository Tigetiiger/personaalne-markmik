// Võistluste otsija (docs/VOISTLUSED-PLAN.md): the Seaded section, and the Postkast button that
// starts a search in the background and waits for it. Imported by settings.js and postkast.js.
import { api, registerSettingsSection, toast } from "/static/app.js";
import { busy, h, when } from "./dom.js";

const POLL_MS = 1500;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function found(n) {
  if (!n) return "uusi võistlusi ei leitud";
  return n === 1 ? "1 uus võistlus" : `${n} uut võistlust`;
}

/** A search's result in one sentence, errors included. */
export function summary(last) {
  const where = last.new ? ` (teaduskoolist ${last.teaduskool}, veebist ${last.veeb})` : "";
  const text = found(last.new);
  return [`${text[0].toUpperCase()}${text.slice(1)}${where}.`, ...last.errors].join(" ");
}

function lastRun(status) {
  if (status.running) return "Otsing käib…";
  if (!status.last) return "Veel pole otsitud.";
  return `Viimati otsitud ${when(status.last.at)}${status.last.weekly ? " (iganädalane)" : ""}: ${summary(status.last)}`;
}

/** The Postkast button: starts a search, shows "Otsin…" until it ends, then reloads the cards. A
 *  search already running when the page opens (the palette, the weekly job) is waited for too. */
export function mountSearch(button, reload) {
  const label = button.textContent;
  let waiting = false;
  async function wait() {
    if (waiting) return;
    waiting = true;
    button.disabled = true;
    button.textContent = "Otsin…";
    try {
      let status;
      do {
        await sleep(POLL_MS);
        status = await api("/api/voistlused/status");
      } while (status.running);
      await reload();
      if (status.last) toast(summary(status.last), status.last.errors.length ? "error" : "info");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      waiting = false;
      button.disabled = false;
      button.textContent = label;
    }
  }
  button.addEventListener("click", async () => {
    try {
      await api("/api/voistlused/run", { method: "POST" });
    } catch (err) {
      if (err.status !== 409) return toast(err.message, "error"); // 409: one is running already, wait for it
    }
    wait();
  });
  api("/api/voistlused/status")
    .then((status) => status.running && wait())
    .catch((err) => console.info("search status not loaded:", err.message));
}

async function mountSettings(el) {
  const cfg = await api("/api/voistlused/settings");
  const state = h("p", { class: "vo-viimati", role: "status" });
  const form = h(
    "form",
    { class: "vorm vo-seaded" },
    h(
      "fieldset",
      { class: "vo-huvid" },
      h("legend", {}, "Huvialad"),
      cfg.choices.map((c) => h("label", { class: "linnuke" }, h("input", { type: "checkbox", name: "huvid", value: c.key, checked: cfg.huvid.includes(c.key) }), h("span", {}, `${c.label} `, h("small", {}, c.covers)))),
    ),
    h(
      "label",
      { class: "vali" },
      h("span", {}, "Lisasoovid"),
      h("textarea", { name: "markus", rows: 2, maxlength: 500, placeholder: "nt 9. klass, Tallinna Reaalkool; võistkonnavõistlused" }, cfg.markus),
      h("small", {}, "Läheb koos huvialadega veebiotsingusse. Ära kirjuta siia midagi salajast."),
    ),
    h("label", { class: "linnuke" }, h("input", { type: "checkbox", name: "iganadal", checked: cfg.iganadal }), " Otsi kord nädalas ise ja teata uutest telefoni"),
    state,
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Salvesta")),
  );
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    const body = { huvid: [...form.querySelectorAll("[name=huvid]:checked")].map((c) => c.value), markus: f.markus.value, iganadal: f.iganadal.checked };
    busy(form.querySelector("[type=submit]"), async () => {
      await api("/api/voistlused/settings", { method: "PUT", body });
      toast("Võistluste otsingu seaded salvestatud.");
    });
  });
  el.append(
    h("p", { class: "leht-juhend" }, "Võistluste otsija vaatab läbi Tartu Ülikooli teaduskooli kalendri ja otsib veebist muid võistlusi. Leitud võistlused jõuavad Postkasti; kalendrisse lähevad need alles siis, kui need kinnitad."),
    form,
  );
  state.textContent = lastRun(await api("/api/voistlused/status"));
}

registerSettingsSection("voistlused", { title: "Võistlused", order: 45, mount: mountSettings });
