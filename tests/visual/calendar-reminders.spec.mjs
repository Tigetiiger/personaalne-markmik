// Own reminders on the calendar (docs/KODU-PLAN.md): a bell item at its time that opens that day's
// Täna, where it is ticked or removed.
import { expect, test } from "@playwright/test";

import { api, login } from "./helpers.mjs";

const pad = (n) => String(n).padStart(2, "0");

test("a reminder shows on the calendar and opens its day's Täna", async ({ page }) => {
  await login(page, "/kalender");
  const d = new Date();
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const text = `Kalendri meeldetuletus ${Date.now().toString(36)}`;
  const made = await api(page, "/api/reminders", { method: "POST", body: { text, remind_at: `${day}T21:30` } });
  expect(made.status).toBe(201);
  await page.reload();
  const item = page.locator(".fc-event.cal-meeldetuletus").filter({ hasText: text });
  await expect(item).toHaveCount(1);
  await item.click();
  await expect(page).toHaveURL(`/tana?paev=${day}`);
  await expect(page.locator(".today-reminders li").filter({ hasText: text }).locator(".agenda-time")).toHaveText("21:30");
  await api(page, `/api/reminders/${made.data.id}`, { method: "DELETE" });
});
