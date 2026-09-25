// Sharing (WP-G): the Jaga action in the note's metadata row, and the pages a share link opens
// (/j/<token>). docs/PLAN-OVERVIEW.md §10; contract in docs/CONTRACTS.md §5 row G.
import { registerMetaAction, registerPage } from "/static/app.js";

registerMetaAction("share", {
  order: 20,
  mount: async (el, ctx) => (await import("/static/share/dialog.js")).mountShareAction(el, ctx),
});

registerPage("share-view", async () => (await import("/static/share/guest.js")).mountView());
registerPage("share-edit", async () => (await import("/static/share/guest.js")).mountEdit());
