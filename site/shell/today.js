// Täna: today's agenda, tasks due today or overdue, tests in the next 7 days, the daily note and
// the Postkast count, from GET /api/today (WP-D). A banner points to Seaded while connections are
// still missing (GET /api/onboarding, WP-E2).
import { api, registerPage, toast } from "/static/app.js";
import { h, noteHref, todayIso } from "./dom.js";

const DAY_MS = 86_400_000;
const WEEKDAYS = ["pühapäev", "esmaspäev", "teisipäev", "kolmapäev", "neljapäev", "reede", "laupäev"];
const MONTHS = ["jaanuar", "veebruar", "märts", "aprill", "mai", "juuni", "juuli", "august", "september", "oktoober", "november", "detsember"];

const asDate = (iso) => new Date(`${iso.slice(0, 10)}T12:00`);
const clock = (iso) => (iso && iso.length > 10 ? iso.slice(11, 16) : "");
const shortDate = (iso) => `${asDate(iso).getDate()}.${asDate(iso).getMonth() + 1}.`;

registerPage("today", async () => {
  const root = document.getElementById("tana");
  const content = document.getElementById("tana-sisu");
  const today = todayIso();
  const d = asDate(today);
  document.getElementById("tana-kuupaev").textContent = `${WEEKDAYS[d.getDay()]}, ${d.getDate()}. ${MONTHS[d.getMonth()]}`;

  const [data, onboarding] = await Promise.all([
    api("/api/today").catch((err) => ({ error: err.message })),
    api("/api/onboarding").catch(() => null),
  ]);
  root.removeAttribute("aria-busy");
  const remaining = onboarding?.remaining || 0;
  content.replaceChildren(
    remaining > 0
      ? h("p", { class: "today-banner" }, h("a", { href: "/seaded/uhendused" }, `${remaining} ühendust tegemata → Seaded`))
      : null,
    ...(data.error ? [h("p", { class: "error" }, `Päevakava ei saanud laadida: ${data.error}`)] : sections(data, today)),
    data.error ? dailyBlock(null, today) : null,
  );
});

function sections(data, today) {
  const postkast = data.postkast || 0;
  return [
    block("Päevakava", data.events.length ? h("ol", { class: "agenda" }, sortEvents(data.events).map(eventRow)) : empty("Täna pole kalendris midagi.")),
    block("Ülesanded", data.tasks.length ? h("ul", { class: "today-tasks" }, data.tasks.map((t) => taskRow(t, today))) : empty("Tänaseks ülesandeid pole.")),
    block("Kontrolltööd 7 päeva jooksul", data.tests_soon.length ? h("ul", { class: "tests-soon" }, data.tests_soon.map((t) => testRow(t, today))) : empty("Lähinädalal kontrolltöid pole.")),
    dailyBlock(data.daily, today),
    h("p", { class: "today-postkast" },
      h("a", { href: "/postkast" }, "Postkast"), " ",
      postkast ? h("span", { class: "badge" }, String(postkast)) : null, " ",
      postkast ? (postkast === 1 ? "ootab kinnitamist" : "ootavad kinnitamist") : "on tühi"),
  ];
}

const block = (title, body) => h("section", { class: "today-block" }, h("h2", {}, title), body);
const empty = (text) => h("p", { class: "today-empty" }, text);

// All-day items first, then by start time.
const sortEvents = (events) => [...events].sort((a, b) => Number(b.all_day) - Number(a.all_day) || a.start.localeCompare(b.start));

function eventRow(e) {
  const time = e.all_day ? "terve päev" : [clock(e.start), clock(e.end)].filter(Boolean).join("–");
  return h("li", { class: `agenda-item kind-${e.kind}${e.status === "pakutud" ? " pakutud" : ""}${e.done ? " done" : ""}` },
    h("span", { class: "agenda-time" }, time),
    h("span", { class: "agenda-title" }, e.title),
    e.source === "ai" ? h("span", { class: "badge ai", title: "AI lisas selle kirja põhjal" }, "AI") : null,
    e.status === "pakutud" ? h("span", { class: "agenda-note" }, "pakutud") : null,
    e.location ? h("span", { class: "agenda-note" }, e.location) : null);
}

function taskRow(task, today) {
  const box = h("input", { type: "checkbox", checked: task.done });
  const row = h("li", { class: task.done ? "done" : "" },
    h("label", {}, box, " ", task.text),
    task.due_date && task.due_date < today ? h("span", { class: "overdue" }, `hilinenud · ${shortDate(task.due_date)}`) : null,
    task.due_time ? h("span", { class: "agenda-note" }, task.due_time) : null,
    h("a", { class: "task-note", href: noteHref(task.note_slug) }, task.note_title));
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await api(`/api/tasks/${task.id}/toggle`, { method: "POST" });
      row.classList.toggle("done", box.checked);
    } catch (err) {
      box.checked = !box.checked;
      toast(err.message, "error");
    } finally {
      box.disabled = false;
    }
  });
  return row;
}

function testRow(t, today) {
  const days = Math.round((asDate(t.start) - asDate(today)) / DAY_MS);
  const left = days <= 0 ? "täna" : days === 1 ? "homme" : `${days} päeva pärast`;
  return h("li", {},
    h("span", { class: "days-left" }, left),
    h("span", { class: "agenda-title" }, t.title),
    h("span", { class: "agenda-note" }, `${WEEKDAYS[asDate(t.start).getDay()].slice(0, 1).toUpperCase()} ${shortDate(t.start)}${clock(t.start) ? ` ${clock(t.start)}` : ""}`));
}

function dailyBlock(daily, today) {
  if (daily?.slug) return block("Päevik", h("p", {}, h("a", { href: noteHref(daily.slug), class: "today-daily" }, daily.title)));
  const button = h("button", { type: "button", class: "today-daily" }, "Loo tänane päevik");
  button.addEventListener("click", async () => {
    try {
      const note = await api(`/api/daily/${today}`);
      location.href = noteHref(note.slug);
    } catch (err) {
      toast(err.message, "error");
    }
  });
  return block("Päevik", h("p", {}, button));
}
