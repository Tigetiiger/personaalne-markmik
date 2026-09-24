// Postkast: events the AI proposed but did not add on its own. Kinnita / Muuda / Loobu per card,
// Kinnita kõik for the lot. Times are local wall-clock strings from the API.
import { api, emit, toast } from "/static/app.js";
import { busy, h } from "./dom.js";

const KINDS = [
  ["sündmus", "Sündmus"],
  ["tund", "Tund"],
  ["kontrolltöö", "Kontrolltöö"],
  ["kodutöö", "Kodutöö"],
  ["tähtaeg", "Tähtaeg"],
  ["isiklik", "Isiklik"],
];
const KIND_LABEL = Object.fromEntries(KINDS);
const SOURCE_LABEL = { käsitsi: "käsitsi", ekool: "eKool", gmail: "Gmail", ics: "ICS", ai: "AI" };
const CONFIDENCE_LABEL = { low: "madal", med: "keskmine", high: "kõrge" };
const dayFormat = new Intl.DateTimeFormat("et-EE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

function whenText(item) {
  if (!item.start) return "";
  const day = dayFormat.format(new Date(`${item.start.slice(0, 10)}T12:00`));
  if (item.all_day) return day;
  const end = item.end ? `–${item.end.slice(11, 16)}` : "";
  return `${day} kell ${item.start.slice(11, 16)}${end}`;
}

function editForm(item, save) {
  const timed = !item.all_day;
  const f = h(
    "form",
    { class: "vorm pk-muuda", hidden: true },
    h("label", { class: "vali" }, h("span", {}, "Pealkiri"), h("input", { name: "title", value: item.title, required: true, maxlength: 200 })),
    h("label", { class: "vali" }, h("span", {}, "Liik"), h("select", { name: "kind" }, KINDS.map(([v, label]) => h("option", { value: v, selected: v === item.kind }, label)))),
    h("label", { class: "linnuke" }, h("input", { type: "checkbox", name: "all_day", checked: item.all_day }), " Terve päev"),
    h(
      "div",
      { class: "pk-ajad" },
      h("label", { class: "vali" }, h("span", {}, "Kuupäev"), h("input", { type: "date", name: "date", value: item.start?.slice(0, 10), required: true })),
      h("label", { class: "vali pk-kell" }, h("span", {}, "Algus"), h("input", { type: "time", name: "start", value: timed ? item.start?.slice(11, 16) : null, required: timed })),
      h("label", { class: "vali pk-kell" }, h("span", {}, "Lõpp"), h("input", { type: "time", name: "end", value: timed && item.end ? item.end.slice(11, 16) : null })),
    ),
    h("div", { class: "pk-nupud" }, h("button", { type: "submit", class: "primary" }, "Salvesta ja kinnita"), h("button", { type: "button", class: "pk-tuhista" }, "Tühista")),
  );
  const x = f.elements;
  const syncAllDay = () => {
    for (const el of f.querySelectorAll(".pk-kell")) el.hidden = x.all_day.checked;
    x.start.required = !x.all_day.checked;
  };
  x.all_day.addEventListener("change", syncAllDay);
  syncAllDay();
  f.querySelector(".pk-tuhista").addEventListener("click", () => (f.hidden = true));
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const allDay = x.all_day.checked;
    const body = {
      title: x.title.value,
      kind: x.kind.value,
      all_day: allDay,
      start: allDay ? x.date.value : `${x.date.value}T${x.start.value}`,
      end: !allDay && x.end.value ? `${x.date.value}T${x.end.value}` : null,
    };
    busy(f.querySelector("[type=submit]"), () => save(body));
  });
  return f;
}

export async function mount() {
  const list = document.getElementById("pk-list");
  const confirmAll = document.getElementById("pk-koik");

  function changed() {
    const n = list.querySelectorAll(".pk-kaart").length;
    confirmAll.hidden = n === 0;
    if (n === 0) {
      list.replaceChildren(
        h("div", { class: "empty-state pk-tyhi" }, h("p", {}, h("strong", {}, "Postkast on tühi")), h("p", {}, "Kui AI pole mõne sündmuse aja või sisu osas kindel, jõuab see siia.")),
      );
    }
    emit("postkast:count", { count: n });
  }

  function settled(card, message) {
    card.remove();
    toast(message);
    changed();
    emit("events:changed", {});
  }

  function renderItem(item) {
    const card = h("article", { class: "plokk pk-kaart", "data-id": item.id });
    const confirm = (body) => api(`/api/inbox/${item.id}/confirm`, { method: "POST", body }).then(() => settled(card, "Lisatud kalendrisse."));
    const editor = editForm(item, confirm);
    const meta = [KIND_LABEL[item.kind] || item.kind, whenText(item), item.location, `allikas: ${SOURCE_LABEL[item.source] || item.source}`];
    if (item.confidence) meta.push(`kindlus: ${CONFIDENCE_LABEL[item.confidence] || item.confidence}`);
    card.append(
      h("h2", { class: "pk-pealkiri" }, item.title),
      h("p", { class: "pk-meta" }, meta.filter(Boolean).join(" · ")),
      item.source_quote && h("blockquote", { class: "pk-tsitaat" }, item.source_quote),
      item.description && h("p", { class: "pk-markus" }, h("strong", {}, "Märkus: "), item.description),
      h(
        "div",
        { class: "pk-nupud" },
        h("button", { type: "button", class: "primary", onclick: (e) => busy(e.currentTarget, () => confirm()) }, "Kinnita"),
        h(
          "button",
          {
            type: "button",
            "aria-expanded": "false",
            onclick: (e) => {
              editor.hidden = !editor.hidden;
              e.currentTarget.setAttribute("aria-expanded", String(!editor.hidden));
              if (!editor.hidden) editor.elements.title.focus();
            },
          },
          "Muuda",
        ),
        h(
          "button",
          {
            type: "button",
            onclick: (e) =>
              busy(e.currentTarget, async () => {
                await api(`/api/inbox/${item.id}/reject`, { method: "POST" });
                settled(card, "Loobutud. Seda sündmust enam ei pakuta.");
              }),
          },
          "Loobu",
        ),
      ),
      editor,
    );
    return card;
  }

  async function load() {
    const items = await api("/api/inbox");
    list.replaceChildren(...items.map(renderItem));
    changed();
  }

  confirmAll.addEventListener("click", () =>
    busy(confirmAll, async () => {
      await api("/api/inbox/confirm-all", { method: "POST" });
      toast("Kõik ettepanekud lisati kalendrisse.");
      await load();
      emit("events:changed", {});
    }),
  );
  await load();
}
