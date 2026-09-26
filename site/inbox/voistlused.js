// Võistluste otsija (docs/VOISTLUSED-PLAN.md): the Seaded section, and the Postkast button that
// starts a search in the background and waits for it. Imported by settings.js and postkast.js.
import { api, registerSettingsSection, toast } from "/static/app.js";
import { busy, h, when } from "./dom.js";

const POLL_MS = 1500;
const LEVEL = { põhikool: "põhikool", gümnaasium: "gümnaasium", open: "kõigile", university: "üliõpilased" };
const WHO = { self: "õpilane ise", school: "kool", teacher: "õpetaja" };
const GRADES = [...Array.from({ length: 12 }, (_, i) => [i + 1, `${i + 1}. klass`]), [13, "Kool on lõpetatud"]];
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

/** A finder card's lines on taking part: fits or not and why, the round it qualifies through,
 *  and the facts the page gave (who, registration, team, fee, place, requirements). */
export function participation(item, dayText) {
  const e = item.eligibility;
  if (!e) return [];
  const f = item.facts || {};
  const [lo, hi] = f.grades || [];
  const who = [lo && (lo === hi ? `${lo}. klass` : `${lo}.–${hi}. klass`), LEVEL[f.level]].filter(Boolean).join(", ");
  const facts = [
    ["Kes", who],
    ["Registreerib", WHO[f.registration?.who]],
    ["Registreerimine", f.registration?.how],
    ["Osalemine", f.team && (f.team.kind === "team" ? `võistkonnana${f.team.size ? ` (${f.team.size})` : ""}` : "üksi")],
    ["Osavõtutasu", f.fee],
    ["Koht", f.location],
    ["Nõuded", f.requirements],
  ].filter(([, value]) => value);
  const note = (result, label, text) => h("p", { class: "pk-sobivus", "data-tulemus": result }, h("strong", {}, label), text);
  const through = e.qualifier ? `${e.qualifier.title}, ${dayText(e.qualifier.start)}` : e.through || "eelvooru või valiku kaudu";
  return [
    e.result === "no" && note("no", "Ei sobi: ", e.reason),
    e.result === "qualify" && note("qualify", "Pääseb edasi: ", through),
    e.unknown && note("unknown", "Tingimused teadmata", ""),
    facts.length && h("ul", { class: "pk-tingimused" }, facts.map(([label, value]) => h("li", {}, h("span", {}, `${label}: `), value))),
  ].filter(Boolean);
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
    h(
      "label",
      { class: "vali vo-klass" },
      h("span", {}, "Klass"),
      h("select", { name: "klass" }, GRADES.map(([value, label]) => h("option", { value, selected: value === Math.min(cfg.klass, 13) }, label))),
      h("small", {}, "Tõuseb iga 1. septembril ise. Võistlused, kuhu sel klassil ei saa minna, jäävad Postkastis eraldi loendisse."),
    ),
    h("label", { class: "linnuke" }, h("input", { type: "checkbox", name: "iganadal", checked: cfg.iganadal }), " Otsi kord nädalas ise ja teata uutest telefoni"),
    state,
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Salvesta")),
  );
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    const body = { huvid: [...form.querySelectorAll("[name=huvid]:checked")].map((c) => c.value), markus: f.markus.value, iganadal: f.iganadal.checked, klass: Number(f.klass.value) };
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
