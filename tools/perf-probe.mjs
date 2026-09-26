// Page-load probe (docs/PERF-PLAN.md): times each page at 85 ms added latency against a running app.
// Usage: BASE=http://100.122.97.7:8082 PW=<owner password> node tools/perf-probe.mjs
import { chromium } from "playwright";
const base = process.env.BASE || "http://100.122.97.7:8082";
const browser = await chromium.launch({ executablePath: "/usr/bin/chromium", args: ["--no-sandbox"] });
const ctx = await browser.newContext();
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
await cdp.send("Network.enable");
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 85, downloadThroughput: 5e6, uploadThroughput: 2e6 });
await page.goto(base + "/login");
await page.fill('input[name="parool"]', process.env.PW);
await Promise.all([page.waitForNavigation(), page.press('input[name="parool"]', "Enter")]);
for (const path of ["/", "/tana", "/kalender", "/graaf", "/postkast", "/seaded", "/"]) {
  const reqs = [];
  const on = (r) => reqs.push(r);
  page.on("requestfinished", on);
  const t0 = Date.now();
  await page.goto(base + path, { waitUntil: "load" });
  await page.waitForFunction(() => document.documentElement.dataset.ready === "1", null, { timeout: 30000 }).catch(() => {});
  const t = Date.now() - t0;
  page.off("requestfinished", on);
  let n304 = 0, n200 = 0, api = [];
  for (const r of reqs) { const s = (await r.response())?.status(); if (s === 304) n304++; else n200++; if (r.url().includes("/api/")) api.push(r.url().replace(base, "") + " " + Math.round(r.timing().responseEnd)); }
  console.log(`${path.padEnd(10)} ${t} ms  requests=${reqs.length} (200:${n200} 304:${n304})  api: ${api.join(", ")}`);
}
await browser.close();
