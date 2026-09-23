// Shared browser-test helpers. Owner: lead; packages add their own helpers in their own files.
import { expect } from "@playwright/test";

export const PASSWORD = "vt-omaniku-parool";

/** Log in as the owner and wait until the app's modules have loaded. */
export async function login(page, next = "/") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.fill("#parool", PASSWORD);
  await page.click("button[type=submit]");
  await waitReady(page);
}

export async function waitReady(page) {
  await expect(page.locator("html")).toHaveAttribute("data-ready", "1");
}

/** Call the JSON API from inside the page (uses the page's session and CSRF token). */
export async function api(page, path, { method = "GET", body } = {}) {
  return page.evaluate(
    async ({ path, method, body }) => {
      const csrf = document.querySelector('meta[name="csrf"]').content;
      const r = await fetch(path, {
        method,
        headers: { "X-CSRF": csrf, "Content-Type": "application/json", Accept: "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: r.status, data: r.status === 204 ? null : await r.json().catch(() => null) };
    },
    { path, method, body },
  );
}
