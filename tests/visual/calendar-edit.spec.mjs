// Calendar: create by clicking, drag to move, resize, switch views, edit and delete in the dialog.
// "Now" is fixed on Wed 7.10.2026, so the week view shows Mon 5.10 - Sun 11.10.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const NOW = new Date("2026-10-07T11:20:00+03:00");

async function openCalendar(page, events = []) {
  await page.clock.setFixedTime(NOW);
  await login(page, "/");
  for (const body of events) expect((await api(page, "/api/events", { method: "POST", body })).status).toBe(201);
  await page.goto("/kalender");
  await waitReady(page);
  await expect(page.locator(".fc-timeGridWeek-view")).toBeVisible();
}

/** Centre of the half-hour slot `time` in the column of `day`, in the week view. */
async function slotPoint(page, day, time) {
  const col = await page.locator(`.fc-timegrid-col[data-date="${day}"]`).boundingBox();
  const row = await page.locator(`td.fc-timegrid-slot-lane[data-time="${time}:00"]`).boundingBox();
  return { x: col.x + col.width / 2, y: row.y + row.height / 2 };
}

async function slotHeight(page) {
  return (await page.locator('td.fc-timegrid-slot-lane[data-time="12:00:00"]').boundingBox()).height;
}

async function drag(page, from, to) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 15 });
  await page.mouse.up();
}

async function stored(page, title) {
  const { data } = await api(page, "/api/events?start=2026-09-28&end=2026-11-02");
  return data.find((e) => e.title === title) || null;
}

test("clicking an empty slot opens the dialog and creates the event", async ({ page }) => {
  await openCalendar(page);
  const p = await slotPoint(page, "2026-10-09", "14:00");
  await page.mouse.click(p.x, p.y);
  const dlg = page.locator("dialog.cal-dialog");
  await expect(dlg.getByRole("heading")).toHaveText("Uus sündmus");
  await expect(dlg.locator("input[name=start]")).toHaveValue("2026-10-09T14:00");
  await expect(dlg.locator("input[name=end]")).toHaveValue("2026-10-09T14:30");
  await dlg.getByLabel("Pealkiri").fill("Bioloogia referaat");
  await dlg.getByLabel("Liik").selectOption("kodutöö");
  await dlg.getByRole("button", { name: "Salvesta" }).click();
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".fc-event.cal-kodutoo", { hasText: "Bioloogia referaat" })).toBeVisible();
  const e = await stored(page, "Bioloogia referaat");
  expect([e.start, e.end, e.kind, e.all_day]).toEqual(["2026-10-09T14:00", "2026-10-09T14:30", "kodutöö", false]);
});

test("a day in the month view makes an all-day event; errors show in the dialog", async ({ page }) => {
  await openCalendar(page);
  await page.getByRole("button", { name: "Kuu", exact: true }).click();
  await page.locator('.fc-daygrid-day[data-date="2026-10-14"] .fc-daygrid-day-frame').click({ position: { x: 20, y: 70 } });
  const dlg = page.locator("dialog.cal-dialog");
  await expect(dlg.getByLabel("Terve päev")).toBeChecked();
  await expect(dlg.locator("input[name=start]")).toHaveValue("2026-10-14");
  await expect(dlg.locator("input[name=end]")).toHaveValue("2026-10-14"); // inclusive in the form
  // The server's Estonian message appears in the dialog.
  await dlg.locator("input[name=end]").fill("2026-10-12");
  await dlg.getByLabel("Pealkiri").fill("Matkapäev");
  await dlg.getByRole("button", { name: "Salvesta" }).click();
  await expect(dlg.locator(".cal-error")).toHaveText("Lõpp ei saa olla enne algust.");
  await dlg.locator("input[name=end]").fill("2026-10-15");
  await dlg.getByRole("button", { name: "Salvesta" }).click();
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".fc-event", { hasText: "Matkapäev" }).first()).toBeVisible();
  const e = await stored(page, "Matkapäev");
  expect([e.start, e.end, e.all_day]).toEqual(["2026-10-14", "2026-10-16", true]);
});

test("dragging an event moves it", async ({ page }) => {
  await openCalendar(page, [{ title: "Liigutatav", start: "2026-10-07T10:00", end: "2026-10-07T11:00" }]);
  const box = await page.locator(".fc-timegrid-event", { hasText: "Liigutatav" }).boundingBox();
  const h = await slotHeight(page);
  const from = { x: box.x + box.width / 2, y: box.y + h / 2 };
  const thursday = await slotPoint(page, "2026-10-08", "12:00");
  await drag(page, from, { x: thursday.x, y: from.y + 4 * h }); // one day right, two hours down
  await expect.poll(async () => (await stored(page, "Liigutatav")).start).toBe("2026-10-08T12:00");
  expect((await stored(page, "Liigutatav")).end).toBe("2026-10-08T13:00");
  await expect(page.locator('.fc-timegrid-col[data-date="2026-10-08"] .fc-event', { hasText: "Liigutatav" })).toBeVisible();
});

test("resizing an event changes its end", async ({ page }) => {
  await openCalendar(page, [{ title: "Venitatav", start: "2026-10-06T09:00", end: "2026-10-06T10:00" }]);
  const ev = page.locator(".fc-timegrid-event", { hasText: "Venitatav" });
  await ev.hover();
  const handle = await ev.locator(".fc-event-resizer-end").boundingBox();
  const h = await slotHeight(page);
  const from = { x: handle.x + handle.width / 2, y: handle.y + handle.height / 2 - 2 };
  await drag(page, from, { x: from.x, y: from.y + 2 * h }); // one hour longer
  await expect.poll(async () => (await stored(page, "Venitatav")).end).toBe("2026-10-06T11:00");
  expect((await stored(page, "Venitatav")).start).toBe("2026-10-06T09:00");
});

test("the four views switch and show the events", async ({ page }) => {
  await openCalendar(page, [{ title: "Nähtav", start: "2026-10-07T13:00", end: "2026-10-07T14:00" }]);
  const views = [
    ["Kuu", "dayGridMonth", "oktoober 2026"],
    ["Päev", "timeGridDay", "7. oktoober 2026"],
    ["Nimekiri", "listWeek", "5 – 11. okt 2026"],
    ["Nädal", "timeGridWeek", "5 – 11. okt 2026"],
  ];
  for (const [button, view, title] of views) {
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.locator(`.fc-${view}-view`)).toBeVisible();
    await expect(page.locator(".fc-toolbar-title")).toHaveText(title);
    await expect(page.locator(".fc-event, .fc-list-event").filter({ hasText: "Nähtav" }).first()).toBeVisible();
  }
});

test("the dialog edits and deletes an event", async ({ page }) => {
  await openCalendar(page, [{ title: "Muudetav", start: "2026-10-08T15:00", end: "2026-10-08T16:00", location: "Aula" }]);
  await page.locator(".fc-event", { hasText: "Muudetav" }).click();
  const dlg = page.locator("dialog.cal-dialog");
  await expect(dlg.getByRole("heading")).toHaveText("Muuda sündmust");
  await expect(dlg.getByLabel("Pealkiri")).toHaveValue("Muudetav");
  await expect(dlg.getByLabel("Koht")).toHaveValue("Aula");
  await dlg.getByLabel("Pealkiri").fill("Keemia kontrolltöö");
  await dlg.getByLabel("Liik").selectOption("kontrolltöö");
  await dlg.getByLabel("Kirjeldus").fill("Peatükid 3-4");
  await dlg.getByRole("button", { name: "Salvesta" }).click();
  await expect(dlg).toHaveCount(0);
  const test_ = page.locator(".fc-event.cal-kontrolltoo", { hasText: "Keemia kontrolltöö" });
  await expect(test_).toBeVisible();
  const e = await stored(page, "Keemia kontrolltöö");
  expect([e.kind, e.description, e.start, e.location]).toEqual(["kontrolltöö", "Peatükid 3-4", "2026-10-08T15:00", "Aula"]);

  await test_.click();
  page.once("dialog", (d) => d.accept());
  await dlg.getByRole("button", { name: "Kustuta" }).click();
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".fc-event", { hasText: "Keemia kontrolltöö" })).toHaveCount(0);
  expect(await stored(page, "Keemia kontrolltöö")).toBeNull();
});
