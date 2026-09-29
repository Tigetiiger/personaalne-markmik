// Shared by the stocks-market browser specs: a fake market (no Yahoo from the browser tests) and
// seeded stock data through the package's test-only routes (MARKMIK_TEST_PAGES=1).
import { api } from "./helpers.mjs";

export const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
};

export async function fakeMarket(page, body = {}) {
  const r = await api(page, "/_test/stocks/fake-market", { method: "POST", body });
  if (r.status !== 204) throw new Error(`fake market: ${r.status}`);
}

export async function seed(page, body) {
  const r = await api(page, "/_test/stocks/seed", { method: "POST", body });
  if (r.status !== 200) throw new Error(`seed: ${r.status}`);
  return r.data;
}

export const SPY = { symbol: "SPY", name: "SPDR S&P 500 ETF Trust", kind: "etf", exchange: "NYSE ARCA", watch: false, first: 300, drift: 0.0003, seed: 2 };
