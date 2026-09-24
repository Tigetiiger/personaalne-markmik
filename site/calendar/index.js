// Calendar page (FullCalendar) and the reminder rules section of Seaded. Owner: WP-D.
// The API speaks local wall-clock time: '2026-10-03T14:15', or '2026-10-03' for all-day events
// (end exclusive, as FullCalendar has it).
import { api, emit, on, registerPage, registerSettingsSection, toast } from "/static/app.js";

const KINDS = [
  ["sündmus", "Sündmus"],
  ["tund", "Tund"],
  ["kontrolltöö", "Kontrolltöö"],
  ["kodutöö", "Kodutöö"],
  ["tähtaeg", "Tähtaeg"],
  ["isiklik", "Isiklik"],
];
// CSS class names stay ASCII.
const KIND_CLASS = { tund: "tund", kontrolltöö: "kontrolltoo", kodutöö: "kodutoo", sündmus: "sundmus", tähtaeg: "tahtaeg", isiklik: "isiklik" };

const pad = (n) => String(n).padStart(2, "0");
const localDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localDateTime = (d) => `${localDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
function addDays(day, n) {
  const d = new Date(`${day}T12:00`);
  d.setDate(d.getDate() + n);
  return localDate(d);
}

function toFc(o) {
  const classNames = [`cal-${KIND_CLASS[o.kind] || "sundmus"}`, `cal-${o.status}`];
  if (o.done) classNames.push("cal-done");
  if (o.task_id) classNames.push("cal-task");
  return {
    id: o.task_id ? `task-${o.task_id}` : o.occurrence ? `${o.id}@${o.occurrence}` : String(o.id),
    title: o.title,
    start: o.start,
    end: o.end || undefined,
    allDay: o.all_day,
    // A series moves in its source; a task moves in its note.
    editable: !o.task_id && !o.recurring,
    classNames,
    extendedProps: o,
  };
}

/** The times of a dragged or resized FullCalendar event, as the API wants them. */
function timesOf(ev) {
  if (ev.allDay) return { all_day: true, start: localDate(ev.start), end: ev.end ? localDate(ev.end) : null };
  const end = ev.end || new Date(ev.start.getTime() + 60 * 60 * 1000);
  return { all_day: false, start: localDateTime(ev.start), end: localDateTime(end) };
}

async function saveMove(info) {
  try {
    await api(`/api/events/${info.event.extendedProps.id}`, { method: "PATCH", body: timesOf(info.event) });
    emit("events:changed");
  } catch (err) {
    info.revert();
    toast(err.message, "error");
  }
}

registerPage("calendar", async () => {
  const el = document.getElementById("kalender");
  // Loaded here, not at the top: every page imports this module, only this one needs the 260 kB.
  const fc = await import("/static/vendor/fullcalendar.js");
  const calendar = new fc.Calendar(el, {
    plugins: [fc.dayGridPlugin, fc.timeGridPlugin, fc.listPlugin, fc.interactionPlugin],
    locale: fc.etLocale,
    timeZone: "local",
    firstDay: 1,
    initialView: "timeGridWeek",
    headerToolbar: { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,timeGridDay,listWeek" },
    buttonText: { today: "Täna", month: "Kuu", week: "Nädal", day: "Päev", list: "Nimekiri" },
    // etLocale leaves these (tooltips and screen-reader labels) in English.
    buttonHints: { prev: "Eelmine", next: "Järgmine", today: "Täna" },
    viewHint: "$0",
    closeHint: "Sulge",
    timeHint: "Aeg",
    eventHint: "Sündmus",
    moreLinkHint: (n) => `Näita veel ${n}`,
    height: "100%",
    scrollTime: "07:30:00",
    nowIndicator: true,
    eventDisplay: "block",
    dayMaxEvents: true,
    editable: true,
    selectable: true,
    selectMirror: true,
    events(info, success, failure) {
      api(`/api/events?start=${localDate(info.start)}&end=${localDate(info.end)}`).then(
        (list) => success(list.map(toFc)),
        (err) => {
          toast(err.message, "error");
          failure(err);
        },
      );
    },
    select(info) {
      calendar.unselect();
      openEditor(null, {
        all_day: info.allDay,
        start: info.allDay ? localDate(info.start) : localDateTime(info.start),
        end: info.allDay ? localDate(info.end) : localDateTime(info.end),
      });
    },
    eventClick(info) {
      info.jsEvent.preventDefault();
      const o = info.event.extendedProps;
      if (o.task_id) location.href = `/m/${encodeURIComponent(o.note_slug)}`;
      else openEditor(o);
    },
    eventDrop: saveMove,
    eventResize: saveMove,
    eventDidMount(info) {
      if (info.event.extendedProps.source !== "ai") return;
      const title = info.el.querySelector(".fc-event-title, .fc-list-event-title a");
      const badge = document.createElement("span");
      badge.className = "cal-ai-badge";
      badge.textContent = "AI";
      badge.title = "Lisatud AI poolt";
      title?.prepend(badge);
    },
  });
  calendar.render();
  on("events:changed", () => calendar.refetchEvents());
});

const EDITOR = `
<form method="dialog" class="cal-form">
  <h2 id="cal-dialog-title"></h2>
  <label>Pealkiri <input name="title" required maxlength="200" autocomplete="off"></label>
  <div class="cal-row">
    <label>Liik <select name="kind"></select></label>
    <label class="cal-check"><input type="checkbox" name="all_day"> Terve päev</label>
  </div>
  <div class="cal-row">
    <label>Algus <input name="start" required></label>
    <label>Lõpp <input name="end"></label>
  </div>
  <p class="cal-note" hidden></p>
  <label>Koht <input name="location" autocomplete="off"></label>
  <label>Kirjeldus <textarea name="description" rows="3"></textarea></label>
  <p class="cal-error error" role="alert" hidden></p>
  <div class="cal-actions">
    <button type="button" class="cal-delete" hidden>Kustuta</button>
    <span class="cal-spacer"></span>
    <button type="button" class="cal-cancel">Loobu</button>
    <button type="submit" class="primary">Salvesta</button>
  </div>
</form>`;

/** Create (o = null, times from the clicked slot) or edit an event in a modal dialog. */
function openEditor(o, times = {}) {
  const dlg = document.createElement("dialog");
  dlg.className = "cal-dialog";
  dlg.setAttribute("aria-labelledby", "cal-dialog-title");
  dlg.innerHTML = EDITOR;
  const form = dlg.querySelector("form");
  const f = form.elements;
  dlg.querySelector("h2").textContent = o ? "Muuda sündmust" : "Uus sündmus";
  for (const [value, label] of KINDS) f.kind.add(new Option(label, value));

  const ev = o || { title: "", kind: "sündmus", location: "", description: "", ...times };
  f.title.value = ev.title;
  f.kind.value = ev.kind;
  f.location.value = ev.location || "";
  f.description.value = ev.description || "";
  f.all_day.checked = ev.all_day;
  // All-day ends are exclusive in the data and inclusive in the form: 5.-7. is "7.", not "8.".
  setTimeInputs(f, ev.all_day, ev.start, ev.all_day && ev.end ? addDays(ev.end, -1) : ev.end || "");
  f.all_day.addEventListener("change", () => {
    const day = f.start.value.slice(0, 10);
    if (f.all_day.checked) setTimeInputs(f, true, day, (f.end.value || day).slice(0, 10));
    else setTimeInputs(f, false, `${day}T09:00`, `${day}T10:00`);
  });

  const note = dlg.querySelector(".cal-note");
  const notes = [];
  if (o?.recurring) notes.push("Korduv sündmus: aega muudetakse allikas, mitte siin.");
  if (o?.status === "pakutud") notes.push("Ootab kinnitamist Postkastis.");
  if (o?.source_quote) notes.push(`Allikas: «${o.source_quote}»`);
  note.textContent = notes.join(" ");
  note.hidden = !notes.length;
  if (o?.recurring) for (const name of ["all_day", "start", "end"]) f[name].disabled = true;

  const error = dlg.querySelector(".cal-error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { title: f.title.value, kind: f.kind.value, location: f.location.value, description: f.description.value };
    if (!o?.recurring) {
      body.all_day = f.all_day.checked;
      body.start = f.start.value;
      body.end = f.end.value ? (body.all_day ? addDays(f.end.value, 1) : f.end.value) : null;
    }
    try {
      if (o) await api(`/api/events/${o.id}`, { method: "PATCH", body });
      else await api("/api/events", { method: "POST", body });
      dlg.close();
      toast(o ? "Sündmus salvestatud." : "Sündmus lisatud.");
      emit("events:changed");
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });

  const del = dlg.querySelector(".cal-delete");
  del.hidden = !o;
  del.addEventListener("click", async () => {
    if (!confirm(`Kas kustutada «${o.title}»?`)) return;
    try {
      await api(`/api/events/${o.id}`, { method: "DELETE" });
      dlg.close();
      toast("Sündmus kustutatud.");
      emit("events:changed");
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
  dlg.querySelector(".cal-cancel").addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  f.title.focus();
}

function setTimeInputs(f, allDay, start, end) {
  for (const input of [f.start, f.end]) input.type = allDay ? "date" : "datetime-local";
  f.start.value = start || "";
  f.end.value = end || "";
}

const RULES_FORM = `
<form class="cal-rules">
  <p class="cal-hint">Teated saadetakse telefoni ntfy kaudu (Seaded → Ühendused → ntfy).</p>
  <label><input type="checkbox" name="test_on"> Kontrolltöö: eelmisel õhtul kell <input type="time" name="test_time" required></label>
  <label><input type="checkbox" name="homework_on"> Kodutöö ja ülesanded: eelmisel päeval kell <input type="time" name="homework_time" required></label>
  <label><input type="checkbox" name="before_on"> Muud sündmused (mitte tunnid): <input type="number" name="before_minutes" min="0" max="1440" required> min enne algust</label>
  <div><button type="submit" class="primary">Salvesta</button></div>
</form>`;

registerSettingsSection("reminders", {
  title: "Meeldetuletused",
  order: 40,
  async mount(el) {
    el.innerHTML = RULES_FORM;
    const form = el.querySelector("form");
    const f = form.elements;
    const rules = await api("/api/settings/reminders");
    f.test_on.checked = rules.test.on;
    f.test_time.value = rules.test.time;
    f.homework_on.checked = rules.homework.on;
    f.homework_time.value = rules.homework.time;
    f.before_on.checked = rules.before.on;
    f.before_minutes.value = rules.before.minutes;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/settings/reminders", {
          method: "PUT",
          body: {
            test: { on: f.test_on.checked, time: f.test_time.value },
            homework: { on: f.homework_on.checked, time: f.homework_time.value },
            before: { on: f.before_on.checked, minutes: Number(f.before_minutes.value) },
          },
        });
        toast("Meeldetuletused salvestatud.");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  },
});
