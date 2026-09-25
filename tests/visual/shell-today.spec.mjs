// Täna page (WP-C) with /api/today (WP-D) and /api/onboarding (WP-E2) stubbed.
import { expect, test } from "@playwright/test";

import { createNote, enter, unique, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
const plus = (days) => {
  const d = new Date(`${today}T12:00`);
  d.setDate(d.getDate() + days);
  return new Intl.DateTimeFormat("sv-SE").format(d);
};

function stub(page, { today: data, onboarding }) {
  return Promise.all([
    page.route("**/api/today", (route) => route.fulfill({ json: data })),
    page.route("**/api/onboarding", (route) => route.fulfill({ json: onboarding })),
  ]);
}

const event = (fields) => ({ id: 1, uid: "u", end: null, all_day: false, kind: "sündmus", status: "kinnitatud", source: "käsitsi", note_id: null, occurrence: null, done: false, location: "", description: "", source_quote: null, confidence: null, ...fields });

test("agenda, tasks with checkboxes, tests soon, daily note, Postkast, onboarding banner", async ({ page, isMobile }) => {
  await enter(page, "/");
  const note = await createNote(page, unique("Ülesannetega "), `- [ ] esimene @${today}\n- [ ] hilinenud @${plus(-2)}`);
  const tasks = (await page.evaluate((slug) => fetch(`/api/tasks?done=0`).then((r) => r.json()), note.slug)).filter((t) => t.note_slug === note.slug);
  expect(tasks).toHaveLength(2);
  await stub(page, {
    onboarding: { remaining: 3 },
    today: {
      date: today,
      events: [
        event({ id: 2, title: "Matemaatika tund", start: `${today}T10:15`, end: `${today}T11:00`, kind: "tund" }),
        event({ id: 3, title: "Kooli ekskursioon", start: today, all_day: true, source: "ai" }),
        event({ id: 4, title: "Trenn?", start: `${today}T18:00`, status: "pakutud", source: "ai" }),
      ],
      tasks: tasks.map((t) => ({ ...t })),
      tests_soon: [event({ id: 5, title: "Füüsika kontrolltöö", start: `${plus(2)}T09:00`, kind: "kontrolltöö" }), event({ id: 6, title: "Keemia kt", start: plus(1), all_day: true, kind: "kontrolltöö" })],
      daily: { slug: "paevik-x", title: "Päevik" },
      postkast: 2,
    },
  });
  await page.goto("/tana");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  const root = page.locator("#tana");
  await expect(root.locator(".today-banner a")).toHaveText("3 ühendust tegemata → Seaded");

  const agenda = root.locator(".agenda .agenda-item");
  await expect(agenda.locator(".agenda-title")).toHaveText(["Kooli ekskursioon", "Matemaatika tund", "Trenn?"]);
  await expect(agenda.nth(0).locator(".agenda-time")).toHaveText("terve päev");
  await expect(agenda.nth(0).locator(".badge.ai")).toHaveText("AI");
  await expect(agenda.nth(1).locator(".agenda-time")).toHaveText("10:15–11:00");
  await expect(agenda.nth(2)).toHaveClass(/pakutud/);
  expect(await agenda.nth(2).evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");

  const rows = root.locator(".today-tasks li");
  await expect(rows).toHaveCount(2);
  const first = rows.filter({ hasText: "esimene" });
  await expect(rows.filter({ hasText: "hilinenud" }).locator(".overdue")).toHaveText(/^hilinenud · \d+\.\d+\.$/);
  await expect(first.locator(".overdue")).toHaveCount(0);
  await expect(first.locator(".task-note")).toHaveText(note.title);
  await first.locator("input").check();
  await expect(first).toHaveClass(/done/);
  await expect.poll(() => page.evaluate((slug) => fetch(`/api/notes/${slug}`).then((r) => r.json()).then((n) => n.body), note.slug)).toContain(`- [x] esimene @${today}`);

  const soon = root.locator(".tests-soon li");
  await expect(soon.locator(".days-left")).toHaveText(["2 päeva pärast", "homme"]);
  await expect(root.locator("a.today-daily")).toHaveAttribute("href", "/m/paevik-x");
  await expect(root.locator(".today-postkast")).toContainText("Postkast 2 ootavad kinnitamist");
  await expect(root.locator(".today-postkast a")).toHaveAttribute("href", "/postkast");
  // The sidebar is there (on a phone, in the drawer), the right pane is not.
  if (isMobile) await page.locator(".toggle-left").click();
  await expect(page.locator("#left .side-new")).toBeVisible();
  await expect(page.locator("#app")).toHaveClass(/no-right/);
});

test("a busy note refuses the tick and the box goes back", async ({ page }) => {
  await enter(page, "/");
  await stub(page, {
    onboarding: { remaining: 0 },
    today: { date: today, events: [], tasks: [{ id: 999999, note_slug: "x", note_title: "X", text: "hõivatud", done: false, due_date: today, due_time: null }], tests_soon: [], daily: null, postkast: 0 },
  });
  await page.route("**/api/tasks/999999/toggle", (route) => route.fulfill({ status: 409, json: { error: "Märget muudetakse teises aknas." } }));
  await page.goto("/tana");
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
  await expect(page.locator(".today-banner")).toHaveCount(0);
  await expect(page.locator(".agenda")).toHaveCount(0);
  await expect(page.locator(".today-block", { hasText: "Päevakava" })).toContainText("Täna pole kalendris midagi.");
  const box = page.locator(".today-tasks input");
  await box.check();
  await expect(page.locator("#toast")).toHaveText("Märget muudetakse teises aknas.");
  await expect(box).not.toBeChecked();
  await expect(page.locator(".today-postkast")).toContainText("on tühi");

  // No daily note yet: the button creates it and opens it.
  await page.locator("button.today-daily").click();
  await expect(page).toHaveURL(new RegExp(`/m/${today}$`));
});
