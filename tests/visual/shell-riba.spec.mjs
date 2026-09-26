// The "Riba" design's behaviour (docs/KUJUNDUS-PLAN.md): the theme follows the system, the page
// keys, Täna's Eile / Homme, and Logi välja on Seaded and in the palette instead of the bar.
import { expect, test } from "@playwright/test";

import { login } from "./helpers.mjs";
import { enter, opened, waitReady, watchErrors } from "./shell-helpers.mjs";

watchErrors(test);

const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
const plus = (days) => {
  const d = new Date(`${today}T12:00`);
  d.setDate(d.getDate() + days);
  return new Intl.DateTimeFormat("sv-SE").format(d);
};

test("the theme follows the system until the toggle chooses one, which then stays", async ({ page }) => {
  const html = page.locator("html");
  await page.emulateMedia({ colorScheme: "dark" });
  await enter(page, "/");
  await expect(html).toHaveAttribute("data-teema", "tume");
  await expect(page.locator("#teema-varv")).toHaveAttribute("content", "#15181e");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(html).toHaveAttribute("data-teema", "hele");
  await expect(page.locator("#teema-varv")).toHaveAttribute("content", "#ffffff");
  await page.click("#teema");
  await expect(html).toHaveAttribute("data-teema", "tume");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.emulateMedia({ colorScheme: "light" });
  await expect(html).toHaveAttribute("data-teema", "tume");
  await page.reload();
  await waitReady(page);
  await expect(html).toHaveAttribute("data-teema", "tume");
});

test("g and a letter go to a page, n makes a note; neither while typing", async ({ page, isMobile }) => {
  test.skip(isMobile, "keys are for a keyboard");
  await enter(page, "/tana");
  for (const [key, path] of [["k", "/kalender"], ["p", "/postkast"], ["g", "/graaf"], ["m", "/"], ["t", "/tana"]]) {
    await page.keyboard.press("g");
    await page.keyboard.press(key);
    await expect(page).toHaveURL(path);
    await waitReady(page);
  }
  // In a field the letters are text.
  await page.locator(".side-search").focus();
  await page.keyboard.type("gk");
  await expect(page.locator(".side-search")).toHaveValue("gk");
  await expect(page).toHaveURL("/tana");
  await page.locator(".side-search").blur();

  await page.keyboard.press("n");
  await expect(page).toHaveURL(/\/m\/marge(-\d+)?$/);
  const slug = new URL(page.url()).pathname.slice(3);
  await opened(page, slug);
  // The caret is in the new note's title: the letters are its text.
  await page.keyboard.type("gkn");
  await expect(page.locator("#editor .mu-editor h1")).toContainText("gkn");
  await expect(page).toHaveURL(`/m/${slug}`);
});

test("Eile and Homme show another day; the page asks the API for that day", async ({ page }) => {
  await enter(page, "/");
  const asked = [];
  await page.route(/\/api\/today(\?|$)/, (route) => {
    const date = new URL(route.request().url()).searchParams.get("date");
    asked.push(date);
    return route.fulfill({ json: { date: date || today, events: [], tasks: [], tests_soon: [], daily: null, postkast: 0 } });
  });
  await page.goto("/tana");
  await waitReady(page);
  const title = page.locator("#tana-kuupaev");
  const todays = await title.textContent();
  await page.locator("#tana-homme").click();
  await expect(page).toHaveURL(`/tana?paev=${plus(1)}`);
  await waitReady(page);
  await expect(title).not.toHaveText(todays);
  await expect(page.locator(".today-stats .stat").first()).toHaveText("0 sündmust sel päeval");
  expect(asked.at(-1)).toBe(plus(1));
  await page.locator("#tana-eile").click();
  await expect(page).toHaveURL("/tana");
  await waitReady(page);
  await expect(title).toHaveText(todays);
  expect(asked.at(-1)).toBeNull();
});

test("Logi välja is on Seaded and in the palette, not in the top bar", async ({ page }) => {
  // Its own login: the session other tests share stays as it is.
  await login(page, "/seaded");
  await expect(page.locator(".topbar")).not.toContainText("Logi välja");
  await expect(page.locator("#konto").getByRole("button", { name: "Logi välja" })).toBeVisible();
  await page.goto("/");
  await waitReady(page);
  await page.keyboard.press("Control+k");
  await page.keyboard.type("logi välja");
  await expect(page.locator(".palette-item").first()).toContainText("Logi välja");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/login/);
});
