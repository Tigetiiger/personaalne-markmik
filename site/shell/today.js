// Täna: today's agenda, own reminders, tasks due today or overdue, tests in the next 7 days, the daily note and
// the Postkast count, from GET /api/today (WP-D); Eile / Homme show another day the same way. A banner points to Seaded while connections are
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
  // Eile / Homme: /tana?paev=YYYY-MM-DD shows another day the same way.
  const asked = new URLSearchParams(location.search).get("paev") || "";
  const day = /^\d{4}-\d{2}-\d{2}$/.test(asked) ? asked : today;
  const d = asDate(day);
  const weekday = WEEKDAYS[d.getDay()];
  document.getElementById("tana-kuupaev").textContent = `${weekday[0].toUpperCase()}${weekday.slice(1)}, ${d.getDate()}. ${MONTHS[d.getMonth()]}`;
  const dayHref = (other) => (other === today ? "/tana" : `/tana?paev=${other}`);
  document.getElementById("tana-eile").href = dayHref(addDays(day, -1));
  document.getElementById("tana-homme").href = dayHref(addDays(day, 1));

  const [data, onboarding] = await Promise.all([
    api(day === today ? "/api/today" : `/api/today?date=${day}`).catch((err) => ({ error: err.message })),
    api("/api/onboarding").catch(() => null),
  ]);
  root.removeAttribute("aria-busy");
  const remaining = onboarding?.remaining || 0;
  content.replaceChildren(
    ...[
      remaining > 0 && h("p", { class: "today-banner sub" }, h("a", { href: "/seaded/uhendused" }, `${remaining} ühendust tegemata → Seaded`)),
      ...(data.error ? [h("p", { class: "error" }, `Päevakava ei saanud laadida: ${data.error}`), dailyBlock(null, day, day === today)] : sections(data, day, day === today)),
    ].filter(Boolean),
  );
});

function addDays(iso, n) {
  const d = asDate(iso);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function sections(data, day, isToday) {
  return [
    stats(data, day, isToday),
    block("Päevakava", data.events.length ? h("ol", { class: "agenda rows" }, sortEvents(data.events).map(eventRow)) : empty(isToday ? "Täna pole kalendris midagi." : "Sel päeval pole kalendris midagi.")),
    remindersBlock(data, day),
    block("Ülesanded", data.tasks.length ? h("ul", { class: "today-tasks rows" }, data.tasks.map((t) => taskRow(t, day))) : empty(isToday ? "Tänaseks ülesandeid pole." : "Selleks päevaks ülesandeid pole.")),
    block("Kontrolltööd 7 päeva jooksul", data.tests_soon.length ? h("ul", { class: "tests-soon rows" }, data.tests_soon.map((t) => testRow(t, day))) : empty("Lähinädalal kontrolltöid pole.")),
    dailyBlock(data.daily, day, isToday),
  ];
}

/** The three numbers on top: events that day, days to the next test, what waits in Postkast. */
function stats(data, day, isToday) {
  const stat = (value, label) => h("div", { class: "card stat" }, h("b", {}, value), " ", h("span", {}, label));
  const days = (t) => Math.round((asDate(t.start) - asDate(day)) / DAY_MS);
  const next = [...data.tests_soon].sort((a, b) => days(a) - days(b))[0];
  const left = next && days(next);
  const postkast = data.postkast || 0;
  return h("div", { class: "today-stats" },
    stat(String(data.events.length), isToday ? "sündmust täna" : "sündmust sel päeval"),
    next ? stat(left <= 0 ? "täna" : left === 1 ? "1 päev" : `${left} päeva`, `kontrolltööni: ${next.title}`) : stat("–", "kontrolltöid lähinädalal pole"),
    h("a", { class: "card stat today-postkast", href: "/postkast" },
      h("b", {}, String(postkast)), " ",
      h("span", {}, postkast ? (postkast === 1 ? "ootab Postkastis" : "ootavad Postkastis") : "Postkast on tühi")),
  );
}

const block = (title, body) => h("section", { class: "today-block" }, h("h2", { class: "sec" }, title), body);
const empty = (text) => h("p", { class: "today-empty" }, text);

// All-day items first, then by start time.
const sortEvents = (events) => [...events].sort((a, b) => Number(b.all_day) - Number(a.all_day) || a.start.localeCompare(b.start));

function eventRow(e) {
  const time = e.all_day ? "terve päev" : [clock(e.start), clock(e.end)].filter(Boolean).join("–");
  return h("li", { class: `agenda-item kind-${e.kind}${e.status === "pakutud" ? " pakutud" : ""}${e.done ? " done" : ""}` },
    h("span", { class: "agenda-time" }, time),
    h("span", { class: "agenda-title" }, e.title),
    e.source === "ai" ? h("span", { class: "badge ai", title: "AI lisas selle kirja põhjal" }, "AI") : null,
    e.status === "pakutud" ? h("span", { class: "agenda-note agenda-proposed" }, "pakutud") : null,
    e.location ? h("span", { class: "agenda-note" }, e.location) : null);
}

function taskRow(task, day) {
  const box = h("input", { type: "checkbox", checked: task.done });
  const row = h("li", { class: task.done ? "done" : "" },
    h("label", {}, box, " ", task.text),
    task.due_date && task.due_date < day ? h("span", { class: "overdue" }, `hilinenud · ${shortDate(task.due_date)}`) : null,
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

// Own reminders (docs/KODU-PLAN.md): ✓ marks one done, ✕ removes it. Missed ones of earlier days
// stay on today's list until then.
function remindersBlock(data, day) {
  const list = data.reminders || [];
  if (!list.length) return null;
  const hint = data.ntfy ? null : h("p", { class: "today-banner sub" }, h("a", { href: "/seaded/uhendused" }, "ntfy pole seadistatud: meeldetuletused ei jõua telefoni → Seaded"));
  return block("Meeldetuletused", h("div", {}, hint, h("ul", { class: "today-reminders rows" }, list.map((r) => reminderRow(r, day)))));
}

function reminderRow(r, day) {
  const box = h("input", { type: "checkbox", checked: r.done, "aria-label": "Tehtud" });
  const remove = h("button", { type: "button", class: "icon-button linklike", title: "Kustuta", "aria-label": "Kustuta meeldetuletus" }, "✕");
  const row = h("li", { class: `reminder${r.done ? " done" : ""}${r.missed ? " missed" : ""}` },
    h("span", { class: "agenda-time" }, r.remind_at.slice(0, 10) === day ? clock(r.remind_at) : `${shortDate(r.remind_at)} ${clock(r.remind_at)}`),
    h("label", {}, box, " 🔔 ", r.text),
    r.missed ? h("span", { class: "overdue" }, "jäi saatmata") : null,
    r.event_title ? h("span", { class: "agenda-note" }, r.event_title) : null,
    remove);
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await api(`/api/reminders/${r.id}`, { method: "PATCH", body: { done: box.checked } });
      row.classList.toggle("done", box.checked);
    } catch (err) {
      box.checked = !box.checked;
      toast(err.message, "error");
    } finally {
      box.disabled = false;
    }
  });
  remove.addEventListener("click", async () => {
    try {
      await api(`/api/reminders/${r.id}`, { method: "DELETE" });
      row.remove();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  return row;
}

function testRow(t, day) {
  const days = Math.round((asDate(t.start) - asDate(day)) / DAY_MS);
  const left = days <= 0 ? "täna" : days === 1 ? "homme" : `${days} päeva pärast`;
  return h("li", {},
    h("span", { class: "days-left" }, left),
    h("span", { class: "agenda-title" }, t.title),
    h("span", { class: "agenda-note" }, `${WEEKDAYS[asDate(t.start).getDay()].slice(0, 1).toUpperCase()} ${shortDate(t.start)}${clock(t.start) ? ` ${clock(t.start)}` : ""}`));
}

function dailyBlock(daily, day, isToday = true) {
  if (daily?.slug) return block("Päevik", h("p", {}, h("a", { href: noteHref(daily.slug), class: "today-daily" }, daily.title)));
  const button = h("button", { type: "button", class: "today-daily" }, isToday ? "Loo tänane päevik" : "Loo selle päeva päevik");
  button.addEventListener("click", async () => {
    try {
      const note = await api(`/api/daily/${day}`);
      location.href = noteHref(note.slug);
    } catch (err) {
      toast(err.message, "error");
    }
  });
  return block("Päevik", h("p", {}, button));
}
