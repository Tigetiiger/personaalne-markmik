// OT fuzz: three editors on one page share a copy of the server's sequencer through a network
// with seeded random delays (site/editor-test/fuzz.js). Seeded random edits — Enter and Backspace
// at block edges, ticks, typing in table cells, multi-block paste, undo — happen while ops are in
// flight. Every editor, a late joiner and the saved text must end up the same.
import { expect, test } from "@playwright/test";

import { login } from "./helpers.mjs";

const BASE = `# Fuzz

Esimene lõik tekstiga.

- üks
- kaks

Vahelõik.

- [ ] ülesanne A
- [x] ülesanne B

| a | b |
| --- | --- |
| 1 | 2 |

Viimane lõik.
`;
const SEEDS = [11, 2026, 424242];
const STEPS = 70;
const WORDS = ["kass", "Õun", "x", "tere maailm", "12", "ülesanne @reede", "ä"];
const PASTES = ["uus lõik\n\nteine uus lõik", "- p1\n- p2", "## Vahepealkiri\n\ntekst", "üks rida"];
// [kind, weight]
const KINDS = [
  ["type", 8], ["enter-end", 3], ["enter-start", 2], ["backspace-start", 3], ["delete-end", 2],
  ["backspace-mid", 2], ["tick", 3], ["cell", 3], ["paste", 2], ["undo", 2], ["list", 1], ["task", 1],
];

function mulberry32(seed) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (rng, list) => list[Math.floor(rng() * list.length)];
function pickKind(rng) {
  const total = KINDS.reduce((n, [, w]) => n + w, 0);
  let x = rng() * total;
  for (const [kind, w] of KINDS) if ((x -= w) < 0) return kind;
  return KINDS[0][0];
}

const place = (page, i, r, where, options) => page.evaluate(([i, r, where, options]) => window.fuzz.place(i, r, where, options), [i, r, where, options]);

async function act(page, rng, i, kind) {
  const r = rng();
  switch (kind) {
    case "type":
      if (await place(page, i, r, rng())) await page.keyboard.type(pick(rng, WORDS));
      break;
    case "enter-end":
      if (await place(page, i, r, "end")) await page.keyboard.press("Enter");
      break;
    case "enter-start":
      if (await place(page, i, r, "start")) await page.keyboard.press("Enter");
      break;
    case "backspace-start":
      if (await place(page, i, r, "start")) await page.keyboard.press("Backspace");
      break;
    case "delete-end":
      if (await place(page, i, r, "end")) await page.keyboard.press("Delete");
      break;
    case "backspace-mid":
      if (await place(page, i, r, rng())) await page.keyboard.press("Backspace");
      break;
    case "tick":
      await page.evaluate(([i, r]) => window.fuzz.tick(i, r), [i, r]);
      break;
    case "cell":
      if (await place(page, i, r, rng(), { cells: true })) await page.keyboard.type(pick(rng, WORDS));
      break;
    case "paste":
      if (await place(page, i, r, rng())) await page.evaluate(([i, text]) => window.fuzz.paste(i, text), [i, pick(rng, PASTES)]);
      break;
    case "undo":
      await page.evaluate((i) => window.fuzz.undo(i), i);
      break;
    case "list":
      if (await place(page, i, r, "end")) {
        await page.keyboard.press("Enter");
        await page.keyboard.type(`- ${pick(rng, WORDS)}`);
      }
      break;
    case "task":
      if (await place(page, i, r, "end")) {
        await page.keyboard.press("Enter");
        await page.keyboard.type(`- [ ] ${pick(rng, WORDS)}`);
      }
      break;
  }
}

let cookies = null;

for (const seed of SEEDS) {
  test(`seed ${seed}: every editor, a late joiner and the saved text converge`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    if (cookies) {
      await page.context().addCookies(cookies);
      await page.goto("/_test/editor-fuzz");
    } else {
      await login(page, "/_test/editor-fuzz");
      cookies = await page.context().cookies();
    }
    await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
    await page.evaluate(([seed, base]) => window.fuzz.setup({ seed, base, count: 3, maxDelay: 80 }), [seed, BASE]);

    const rng = mulberry32(seed);
    const done = [];
    for (let step = 0; step < STEPS; step++) {
      const i = Math.floor(rng() * 3);
      const kind = pickKind(rng);
      done.push(`${i}:${kind}`);
      await act(page, rng, i, kind);
      await page.waitForTimeout(Math.floor(rng() * 60));
    }

    const report = await page.evaluate(() => window.fuzz.settle());
    const context = `seed ${seed}, steps ${done.join(" ")}`;
    expect(report.failures, context).toEqual([]);
    expect(report.pending, context).toEqual([false, false, false]);
    expect(report.seqs, context).toEqual([report.ops, report.ops, report.ops]);
    expect(report.markdown[1], context).toBe(report.markdown[0]);
    expect(report.markdown[2], context).toBe(report.markdown[0]);
    expect(report.ops, "the edits reached the server").toBeGreaterThan(20);

    expect(await page.evaluate(() => window.fuzz.lateJoiner()), context).toBe(report.markdown[0]);
    const saved = await page.evaluate(() => window.fuzz.save(0));
    expect(saved.savedSeq).toBe(saved.ops);
    expect(saved.body, context).toBe(report.markdown[0]);
    expect(errors, context).toEqual([]);
  });
}

test("a remote op arriving before Muya reports the last keystroke lands in the right place", async ({ page }) => {
  await page.context().addCookies(cookies || []);
  await page.goto("/_test/editor-fuzz");
  if (!cookies) {
    await login(page, "/_test/editor-fuzz");
    cookies = await page.context().cookies();
  }
  await expect(page.locator("body")).toHaveAttribute("data-editor", "ready");
  await page.evaluate(() => window.fuzz.setup({ seed: 1, base: "# R\n\nabc\n", count: 2, maxDelay: 0 }));
  const report = await page.evaluate(() => window.fuzz.remoteOpBeforeNextFrame());
  expect(report.landed, "the remote op arrived before the next frame").toBe(true);
  expect(report.failures).toEqual([]);
  expect(report.markdown).toEqual(["# R\n\nXYabcz\n", "# R\n\nXYabcz\n"]);
});
