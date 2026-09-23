import { expect, test } from "@playwright/test";

import { login } from "./helpers.mjs";

test("login shows the shell with no console errors and no CSP violations", async ({ page }) => {
  const problems = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  await login(page);
  await expect(page.locator(".brand")).toHaveText("Märkmik");
  await expect(page.locator("#app")).toBeVisible();
  expect(problems.filter((p) => !p.includes("Failed to load resource"))).toEqual([]);
});

test("theme toggle switches and remembers", async ({ page }) => {
  await login(page);
  const before = await page.locator("html").getAttribute("data-teema");
  await page.click("#teema");
  const after = await page.locator("html").getAttribute("data-teema");
  expect(after).not.toBe(before);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-teema", after);
});

test("the Muya build is served with immutable caching", async ({ page, request }) => {
  await login(page);
  const muya = await page.locator('meta[name="muya"]').getAttribute("content");
  const r = await request.get(`${muya}/es/index.js`);
  expect(r.status()).toBe(200);
  expect(r.headers()["cache-control"]).toContain("immutable");
});
