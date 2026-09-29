// Entry of Aktsiad (docs/AKTSIAD-PLAN.md). Each tab's code loads only on its page; the modules
// belong to the stocks packages (docs/CONTRACTS.md, "Aktsiad"), this file to the lead.
import { registerCommand, registerPage, registerSettingsSection } from "/static/app.js";

const root = () => document.getElementById("aktsiad");

registerPage("stocks-watchlist", async () => (await import("./watchlist.js")).mount(root()));
registerPage("stocks-ticker", async () => {
  // Other packages' sections (registerTickerSection) register themselves when loaded: research
  // and verdict (stocks-ai), the position (stocks-portfolio), the news (stocks-sources).
  await Promise.all(
    [() => import("./research.js"), () => import("./position.js"), () => import("./news.js")].map((load) => load().catch((err) => console.info("stock page section not loaded:", err.message))),
  );
  (await import("./ticker.js")).mount(root(), root().dataset.symbol);
});
registerPage("stocks-portfolio", async () => (await import("./portfolio.js")).mount(root()));
registerPage("stocks-tx", async () => (await import("./tx.js")).mount(root()));
registerPage("stocks-explore", async () => (await import("./explore.js")).mount(root()));
registerPage("stocks-sources", async () => (await import("./sources.js")).mount(root()));

// Täna's "Turg" card (stocks-night), for today only (not Eile / Homme).
registerPage("today", async () => {
  if (new URLSearchParams(location.search).get("paev")) return;
  (await import("./today.js")).mount(document.getElementById("tana-lisad"));
});

registerSettingsSection("stocks", { title: "Aktsiad", order: 55, mount: async (el) => (await import("./settings.js")).mount(el) });

registerCommand("aktsiad", { title: "Aktsiad", keywords: "aktsiad börs jälgin", run: () => (location.href = "/aktsiad") });
registerCommand("portfell", { title: "Portfell", keywords: "aktsiad portfell investeerimiskonto", run: () => (location.href = "/aktsiad/portfell") });
registerCommand("lisa-tehing", { title: "Lisa tehing", keywords: "aktsiad tehing ost müük dividend", run: () => (location.href = "/aktsiad/tehingud#lisa") });
