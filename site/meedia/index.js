// Entry of Meedia (docs/MEEDIA-PLAN.md): the page's code loads only on /meedia.
import { registerCommand, registerPage } from "/static/app.js";

registerPage("meedia", async () => (await import("./page.js")).mount(document.getElementById("meedia")));

registerCommand("meedia", {
  title: "Meedia",
  keywords: "meedia pildid fotod videod galerii",
  run: () => (location.href = "/meedia"),
});
