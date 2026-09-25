// Entry of WP-E2 (Postkast + Ühendused): the Postkast count in the top bar on every page, and
// the pages Postkast, Seaded, Ühendused and the first-run wizard (loaded only where needed).
import { api, on, registerPage } from "/static/app.js";

function setBadge(count) {
  const badge = document.getElementById("postkast-arv");
  if (!badge) return;
  badge.textContent = String(count);
  badge.hidden = !count;
}

async function refreshBadge() {
  if (!document.getElementById("postkast-arv")) return; // guests and the login page have none
  try {
    setBadge((await api("/api/inbox/count")).count);
  } catch (err) {
    console.info("postkast count not loaded:", err.message);
  }
}

on("postkast:count", ({ count }) => setBadge(count));
// The calendar can confirm or remove a proposed event too.
on("events:changed", refreshBadge);
refreshBadge();

registerPage("inbox", async () => (await import("./postkast.js")).mount());
registerPage("settings", async () => (await import("./settings.js")).mount());
registerPage("connections", async () => (await import("./cards.js")).mountPage());
registerPage("wizard", async () => (await import("./wizard.js")).mount());
