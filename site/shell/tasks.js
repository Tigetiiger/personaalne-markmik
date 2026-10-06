// Ülesanded (/ulesanded, docs/MARKMED-PLAN.md 15): every task of the notes in groups by due date,
// those without one included; a folder or a note narrows the list, "Näita tehtuid" adds the done
// ones. Templates' tasks (Mallid/) are left out by GET /api/tasks.
import { api, registerPage } from "/static/app.js";
import { h, store, stored, todayIso } from "./dom.js";
import { taskRow } from "./today.js";

const DAY_MS = 86_400_000;

/** The Sunday ending day's week, as YYYY-MM-DD (the week starts on Monday). */
function weekEnd(day) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setTime(d.getTime() + ((7 - d.getUTCDay()) % 7) * DAY_MS);
  return d.toISOString().slice(0, 10);
}

/** [[title, tasks]] in order, empty groups left out. */
export function groups(tasks, day) {
  const sunday = weekEnd(day);
  const open = tasks.filter((t) => !t.done);
  return [
    ["Üle tähtaja", open.filter((t) => t.due_date && t.due_date < day)],
    ["Täna", open.filter((t) => t.due_date === day)],
    ["See nädal", open.filter((t) => t.due_date > day && t.due_date <= sunday)],
    ["Hiljem", open.filter((t) => t.due_date > sunday)],
    ["Tähtajata", open.filter((t) => !t.due_date)],
    ["Tehtud", tasks.filter((t) => t.done)],
  ].filter(([, list]) => list.length);
}

registerPage("tasks", async () => {
  const root = document.getElementById("ulesanded");
  const content = document.getElementById("ulesanded-sisu");
  const filters = document.getElementById("ulesanded-filtrid");
  let tasks;
  try {
    tasks = await api("/api/tasks");
  } catch (err) {
    content.replaceChildren(h("p", { class: "error" }, `Ülesandeid ei saanud laadida: ${err.message}`));
    return;
  } finally {
    root.removeAttribute("aria-busy");
  }
  const day = todayIso();
  const folders = [...new Set(tasks.map((t) => t.folder))].sort((a, b) => a.localeCompare(b, "et"));
  const notes = [...new Map(tasks.map((t) => [t.note_slug, t.note_title])).entries()].sort((a, b) => a[1].localeCompare(b[1], "et"));
  const folder = h("select", { "aria-label": "Kaust" }, h("option", { value: "*" }, "Kõik kaustad"), folders.map((f) => h("option", { value: f }, f || "Kaustata")));
  const note = h("select", { "aria-label": "Märge" }, h("option", { value: "" }, "Kõik märkmed"), notes.map(([slug, title]) => h("option", { value: slug }, title)));
  const done = h("input", { type: "checkbox", checked: stored("ulesanded:tehtud") === "1" });
  filters.replaceChildren(folder, note, h("label", { class: "tasks-done" }, done, " Näita tehtuid"));

  const render = () => {
    const shown = tasks.filter((t) => (folder.value === "*" || t.folder === folder.value) && (!note.value || t.note_slug === note.value) && (done.checked || !t.done));
    const list = groups(shown, day);
    content.replaceChildren(
      ...(list.length
        ? list.map(([title, items]) => h("section", { class: "today-block" }, h("h2", { class: "sec" }, title, " ", h("span", { class: "side-count" }, String(items.length))),
            h("ul", { class: "today-tasks rows" }, items.map((t) => taskRow(t, day, { withDate: true })))))
        : [h("p", { class: "today-empty" }, tasks.length ? "Nende valikutega ülesandeid pole." : "Märkmetes pole veel ülesandeid. Kirjuta märkmesse „- [ ] tee midagi“.")]),
    );
  };
  for (const el of [folder, note]) el.addEventListener("change", render);
  done.addEventListener("change", () => {
    store("ulesanded:tehtud", done.checked ? "1" : "0");
    render();
  });
  render();
});
