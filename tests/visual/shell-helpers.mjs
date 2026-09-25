// Helpers for the shell's browser tests (tests/visual/shell-*.spec.mjs). Owner: WP-C.
import { expect } from "@playwright/test";

import { api, login, waitReady } from "./helpers.mjs";

export { api, waitReady };

// Logged in once per spec file; later tests reuse the session cookie (/login is rate-limited).
let cookies = null;

/** Go to path as the owner, the app's modules loaded. */
export async function enter(page, path = "/") {
  if (cookies) {
    await page.context().addCookies(cookies);
    await page.goto(path);
    await waitReady(page);
  } else {
    await login(page, path);
    cookies = await page.context().cookies();
  }
}

export const unique = (name) => `${name}${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

/** Create a note through the API (the page must be on the site). */
export async function createNote(page, title, body) {
  const r = await api(page, "/api/notes", { method: "POST", body: body === undefined ? { title } : { title, body } });
  expect(r.status).toBe(201);
  return r.data;
}

/** Open /m/<slug> and wait for the editor to hold it. */
export async function openNote(page, slug) {
  await enter(page, `/m/${slug}`);
  await opened(page, slug);
}

export async function opened(page, slug) {
  await expect(page.locator("#note")).toHaveAttribute("data-open", slug);
  await expect(page.locator("#editor .mu-editor")).toBeVisible();
}

export const editor = (page) => page.locator("#editor .mu-editor");
export const saveState = (page) => page.locator("#note-meta .meta-save");

/** Caret at the end of the n-th element matching selector in the editor. */
export async function caretAtEnd(page, selector, n = 0) {
  await editor(page).locator(selector).nth(n).click();
  await page.keyboard.press("End");
}

/** Call fn(message) for every uncaught page error but one Firefox quirk: it reports a module
 *  import that a navigation away cut short. Chromium stays quiet there, and still catches a module
 *  that fails to load for real. */
export function onPageError(page, fn) {
  let navigatedAt = -Infinity;
  page.on("request", (r) => {
    if (r.isNavigationRequest() && r.frame() === page.mainFrame()) navigatedAt = Date.now();
  });
  page.on("pageerror", (e) => {
    if (/^error loading dynamically imported module/.test(e.message) && Date.now() - navigatedAt < 2000) return;
    fn(e.message);
  });
}

/** Fail the test on any uncaught page error. */
export function watchErrors(test) {
  test.beforeEach(async ({ page }) => {
    page.errors = [];
    onPageError(page, (message) => page.errors.push(message));
  });
  test.afterEach(async ({ page }, testInfo) => {
    if (testInfo.expectedStatus === "skipped") return; // skipped before beforeEach ran
    expect(page.errors, "JavaScript errors on the page").toEqual([]);
  });
}
