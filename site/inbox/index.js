// Entry of WP-E2 (Postkast + Ühendused): the Postkast count in the top bar on every page, and
// the pages Postkast, Seaded, Ühendused and the first-run wizard (loaded only where needed).
import { api, on, registerCommand, registerPage, toast } from "/static/app.js";

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

// Starts a search; Postkast shows it running and its finds when it ends.
registerCommand("otsi-voistlusi", {
  title: "Otsi võistlusi",
  keywords: "võistlused olümpiaadid teaduskool postkast",
  async run() {
    const button = document.getElementById("pk-otsi");
    if (button) return button.click();
    try {
      await api("/api/voistlused/run", { method: "POST" });
    } catch (err) {
      if (err.status !== 409) return toast(err.message, "error");
    }
    location.href = "/postkast";
  },
});

registerPage("inbox", async () => (await import("./postkast.js")).mount());
registerPage("settings", async () => (await import("./settings.js")).mount());
registerPage("connections", async () => (await import("./cards.js")).mountPage());
registerPage("wizard", async () => (await import("./wizard.js")).mount());
