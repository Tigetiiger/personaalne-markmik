// Calendar look: kinds and states in light and dark. The only screenshot baselines of the
// calendar are this chromium pair. "Now" is Wed 18.11.2026, a week no other test uses.
import { expect, test } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

const NOW = new Date("2026-11-18T11:20:00+02:00");
// Raw events columns (UTC), made through the test-only route: the API cannot make AI proposals.
const EVENTS = [
  { title: "Matemaatika", kind: "tund", start_at: "2026-11-16T06:00:00Z", end_at: "2026-11-16T06:45:00Z", rrule: "FREQ=DAILY;COUNT=5" },
  { title: "Füüsika KT", kind: "kontrolltöö", start_at: "2026-11-19T08:00:00Z", end_at: "2026-11-19T08:45:00Z", location: "204" },
  { title: "Inglise essee", kind: "kodutöö", all_day: 1, start_date: "2026-11-20" },
  { title: "Hambaarst", kind: "isiklik", start_at: "2026-11-18T13:00:00Z", end_at: "2026-11-18T14:00:00Z" },
  { title: "Õppekäik Tartusse", kind: "sündmus", source: "ai", status: "pakutud", confidence: "med",
    start_at: "2026-11-17T09:00:00Z", end_at: "2026-11-17T12:00:00Z", source_quote: "Teisipäeval läheme Tartusse" },
  { title: "Koosolek", kind: "sündmus", source: "ai", start_at: "2026-11-20T11:00:00Z", end_at: "2026-11-20T12:30:00Z" },
];

test("light and dark themes paint the kinds and states", async ({ page, browserName, isMobile }) => {
  // The colours are checked everywhere; the screenshot baselines exist for desktop chromium only.
  const shot = browserName === "chromium" && !isMobile;
  await page.clock.setFixedTime(NOW);
  await login(page, "/");
  await page.evaluate(() => localStorage.setItem("teema", "hele"));
  // The next browser project runs on the same database: remove the last run's events first.
  const { data: old } = await api(page, "/api/events?start=2026-11-16&end=2026-11-23");
  const titles = EVENTS.map((e) => e.title);
  // Once per id: the series comes back as five occurrences.
  for (const id of new Set(old.filter((e) => titles.includes(e.title)).map((e) => e.id))) {
    expect((await api(page, `/api/events/${id}`, { method: "DELETE" })).status).toBe(204);
  }
  for (const body of EVENTS) expect((await api(page, "/_test/calendar/events", { method: "POST", body })).status).toBe(201);
  await page.goto("/kalender");
  await waitReady(page);

  const test_ = page.locator(".fc-event.cal-kontrolltoo", { hasText: "Füüsika KT" });
  const lesson = page.locator(".fc-event.cal-tund").first();
  const proposal = page.locator(".fc-event.cal-pakutud", { hasText: "Õppekäik Tartusse" });
  // The kind is the bar on the left: kontrolltöö --warn, a lesson --line on --paper-2.
  await expect(test_).toHaveCSS("border-left-color", "rgb(148, 100, 0)");
  await expect(test_).toHaveCSS("background-color", "rgb(232, 236, 234)");
  await expect(lesson).toHaveCSS("background-color", "rgb(232, 236, 234)");
  await expect(lesson).toHaveCSS("border-left-color", "rgb(201, 209, 214)");
  await expect(proposal).toHaveCSS("border-top-style", "dashed");
  await expect(proposal.locator(".cal-ai-badge")).toHaveText("AI");
  await expect(page.locator(".fc-event", { hasText: "Koosolek" }).locator(".cal-ai-badge")).toHaveText("AI");
  await expect(page.locator(".fc-timegrid-event.cal-tund")).toHaveCount(5); // the series, Mon-Fri
  await page.evaluate(() => document.fonts.ready);
  if (shot) await expect(page.locator("main.kalender")).toHaveScreenshot("kalender-hele.png");

  await page.click("#teema");
  await expect(page.locator("html")).toHaveAttribute("data-teema", "tume");
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(15, 17, 21)");
  await expect(lesson).toHaveCSS("background-color", "rgb(28, 32, 40)");
  await expect(test_).toHaveCSS("border-left-color", "rgb(242, 201, 76)");
  await expect(proposal).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  if (shot) await expect(page.locator("main.kalender")).toHaveScreenshot("kalender-tume.png");
});
