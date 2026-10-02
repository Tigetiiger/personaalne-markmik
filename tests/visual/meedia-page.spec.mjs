// Meedia (docs/MEEDIA-PLAN.md, step 4): upload, search, the viewer, tags, a playing video and
// the selection's bulk actions. Every project of a run shares one server, so each upload gets
// unique bytes (appended after the file's end, which JPEG and MP4 readers ignore) and names.
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { login } from "./helpers.mjs";

test.describe.configure({ mode: "serial" });

const run = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const fixture = (file, name, mimeType) => ({
  name,
  mimeType,
  buffer: Buffer.concat([readFileSync(new URL(`../fixtures/meedia/${file}`, import.meta.url)), Buffer.from(`\n${name}`)]),
});
const RED = `punane-${run}.jpg`;
const BLUE = `sinine-${run}.jpg`;
const CLIP = `klipp-${run}.mp4`;
const tile = (page, name) => page.locator(`figure[data-name="${name}"]`);

let page;

test.beforeAll(async ({ browser, baseURL }) => {
  page = await (await browser.newContext({ baseURL })).newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await login(page, "/meedia");
});

test.afterAll(async () => {
  expect(page.errors).toEqual([]);
  await page.context().close();
});

test("uploaded pictures and a video show up as tiles", async () => {
  await page.locator('#meedia input[type="file"]').setInputFiles([
    fixture("punane.jpg", RED, "image/jpeg"),
    fixture("sinine.jpg", BLUE, "image/jpeg"),
    fixture("klipp.mp4", CLIP, "video/mp4"),
  ]);
  for (const name of [RED, BLUE, CLIP]) await expect(tile(page, name)).toBeVisible();
  await expect(tile(page, CLIP).locator(".md-kestus")).toHaveText("▶ 0:02");
  await expect(tile(page, RED).locator("img")).toHaveJSProperty("complete", true);
  expect(await tile(page, RED).locator("img").evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);
});

test("search finds by name and keeps the filter in the address", async () => {
  await page.fill("#md-otsi", `punane ${run}`);
  await expect(page.locator(".md-ruut")).toHaveCount(1);
  await expect(tile(page, RED)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`q=punane`));
  await page.getByRole("button", { name: "Videod" }).click();
  await expect(page.locator(".md-ruut")).toHaveCount(0);
  await page.getByRole("button", { name: "Kõik" }).click();
  await page.fill("#md-otsi", run);
  await expect(page.locator(".md-ruut")).toHaveCount(3);
});

test("the viewer edits tags and the tag is searchable", async () => {
  await tile(page, RED).locator(".md-ava").click();
  const viewer = page.getByRole("dialog", { name: "Vaataja" });
  await expect(viewer.locator(".md-info .md-nimi").last()).toHaveText(RED);
  await viewer.getByLabel("Lisa silt").fill(`Punane${run}`);
  await viewer.getByLabel("Lisa silt").press("Enter");
  await expect(viewer.locator(".md-sildid .chip")).toContainText([`punane${run}`]);
  await viewer.getByLabel("Pealkiri").fill("Punane ring");
  await viewer.getByLabel("Pealkiri").press("Tab");
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await page.fill("#md-otsi", `#punane${run}`);
  await expect(page.locator(".md-ruut")).toHaveCount(1);
  await page.fill("#md-otsi", "ring");
  await expect(tile(page, RED)).toBeVisible();
});

test("a video plays in the viewer", async () => {
  await page.fill("#md-otsi", run);
  await expect(page.locator(".md-ruut")).toHaveCount(3);
  await tile(page, CLIP).locator(".md-ava").click();
  const video = page.locator(".md-lava video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((v) => v.duration)).toBeGreaterThan(1.5);
  await page.keyboard.press("Escape");
});

test("a share link opens without logging in and stops once revoked", async ({ browser, baseURL }) => {
  await page.fill("#md-otsi", run);
  await expect(page.locator(".md-ruut")).toHaveCount(3);
  await tile(page, CLIP).locator(".md-ava").click();
  await page.locator(".md-tegevused").getByRole("button", { name: "Jaga" }).click();
  await page.locator(".md-jaga").getByRole("button", { name: "Loo link" }).click();
  const url = await page.getByLabel("Jagamislink").inputValue();
  expect(url).toMatch(/\/s\/[\w-]{43}$/);
  const guest = await (await browser.newContext({ baseURL })).newPage();
  await guest.goto(url);
  await expect(guest.locator("video")).toBeVisible();
  await expect.poll(() => guest.locator("video").evaluate((v) => (v.load(), new Promise((r) => (v.onloadedmetadata = () => r(v.duration)))))).toBeGreaterThan(1.5);
  expect(await guest.locator('meta[property="og:video"]').getAttribute("content")).toContain(`/f/${CLIP}`);
  await page.locator(".md-lingid").getByRole("button", { name: "Tühista" }).click();
  await expect(page.locator(".md-lingid li")).toHaveCount(0);
  // Not guest.goto: the browser may keep the page for its max-age (5 min) by design.
  expect((await guest.request.get(url)).status()).toBe(404);
  await guest.context().close();
  await page.keyboard.press("Escape");
});

test("a selection gets a tag and is deleted", async () => {
  await tile(page, BLUE).locator(".md-vali").check();
  await tile(page, CLIP).locator(".md-vali").check();
  const bar = page.locator(".md-valik");
  await expect(bar).toContainText("2 valitud");
  await bar.getByLabel("Silt valitud failidele").fill(`kustutan${run}`);
  await bar.getByRole("button", { name: "+ Lisa silt" }).click();
  await page.fill("#md-otsi", `#kustutan${run}`);
  await expect(page.locator(".md-ruut")).toHaveCount(2);
  await page.locator(".md-ruut .md-vali").first().check();
  await page.locator(".md-ruut .md-vali").last().check();
  page.once("dialog", (d) => d.accept());
  await bar.getByRole("button", { name: "Kustuta" }).click();
  await expect(page.locator(".md-ruut")).toHaveCount(0);
  await page.fill("#md-otsi", run);
  await expect(page.locator(".md-ruut")).toHaveCount(1);
});
