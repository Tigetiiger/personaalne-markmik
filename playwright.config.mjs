// Browser tests. Each work package runs its own server on its own VT_PORT, so packages built in
// parallel do not collide. Owner: lead.
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.VT_PORT || 8780);
// External fonts/CDNs must not matter (and headless Chromium on this machine hangs on them).
const blockExternal = "MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND, MAP cdn.jsdelivr.net ~NOTFOUND";
const chromium = { executablePath: "/usr/bin/chromium", args: ["--no-sandbox", `--host-resolver-rules=${blockExternal}`] };

export default defineConfig({
  testDir: "tests/visual",
  testMatch: /.*\.spec\.mjs$/,
  // One server and one database per run: tests run in order and set up their own data.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45_000,
  reporter: [["list"], ["html", { open: "never", outputFolder: "tests/visual/report" }]],
  outputDir: "tests/visual/results",
  expect: {
    toHaveScreenshot: { animations: "disabled", caret: "hide", maxDiffPixelRatio: 0.01 },
  },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    timezoneId: "Europe/Tallinn",
    locale: "et-EE",
    serviceWorkers: "block",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium", viewport: { width: 1280, height: 800 }, launchOptions: chromium } },
    { name: "firefox", use: { browserName: "firefox", viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { ...devices["Pixel 7"], browserName: "chromium", viewport: { width: 390, height: 844 }, launchOptions: chromium } },
  ],
  webServer: {
    command: `rm -rf .vt-data-${port} && uv run flask --app app run --port ${port}`,
    url: `http://127.0.0.1:${port}/login`,
    reuseExistingServer: false,
    stdout: "ignore",
    timeout: 60_000,
    env: {
      DATABASE_PATH: `.vt-data-${port}/test.db`,
      DATA_DIR: `.vt-data-${port}`,
      OWNER_PASSWORD: "vt-omaniku-parool",
      SECRET_KEY: "visuaaltestid",
      // A fixed test key, never the real one.
      MARKMIK_KEY: "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMTIzNDU=",
      AI_FAKE: "1",
    },
  },
});
