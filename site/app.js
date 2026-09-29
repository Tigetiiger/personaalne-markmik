// App core: registries every work package plugs into, the fetch helper, an event bus and the
// theme toggle. Owner: lead. Contract: docs/CONTRACTS.md, "Browser".
//
// Each package has one entry module (MODULES below) that imports this file and registers what
// it adds. Pages are server-rendered; <body data-page="..."> says which page this is, and every
// function registered for that page runs once all modules have loaded.

const pages = new Map(); // page -> [mount(ctx)]
const panels = new Map(); // id -> {title, order, mount(el, ctx)}
const metaActions = new Map(); // id -> {order, mount(el, ctx)}
const settingsSections = new Map(); // id -> {title, order, mount(el)}
const commands = new Map(); // id -> {title, keywords, run()}
const listeners = new Map(); // event -> Set(fn)

/** Run `mount(ctx)` when <body data-page> is `page` ("notes", "today", "calendar", ...). */
export function registerPage(page, mount) {
  if (!pages.has(page)) pages.set(page, []);
  pages.get(page).push(mount);
}

/** A tab in the right panel. mount(el, ctx) is called when the tab is shown; ctx.note is the
 *  open note ({id, slug, title}) or null. Return an optional cleanup function. */
export function registerPanel(id, spec) {
  panels.set(id, { order: 50, ...spec });
}

/** A control in the note's metadata row (Jaga, ✦ AI, ...). mount(el, ctx) once per note. */
export function registerMetaAction(id, spec) {
  metaActions.set(id, { order: 50, ...spec });
}

/** A section on the Seaded page. */
export function registerSettingsSection(id, spec) {
  settingsSections.set(id, { order: 50, ...spec });
}

/** An entry in the command palette (Ctrl+K). */
export function registerCommand(id, spec) {
  commands.set(id, { keywords: "", ...spec });
}

const byOrder = (map) => [...map.entries()].map(([id, v]) => ({ id, ...v })).sort((a, b) => a.order - b.order);
export const listPanels = () => byOrder(panels);
export const listMetaActions = () => byOrder(metaActions);
export const listSettingsSections = () => byOrder(settingsSections);
export const listCommands = () => [...commands.entries()].map(([id, v]) => ({ id, ...v }));

/** Event bus. Events in use: "note:open" {note}, "note:saved" {note, rev},
 *  "note:changed" {note}, "events:changed" {}, "postkast:count" {count}, "theme:changed" {theme},
 *  "panel:toggle" {id} (the right panel on that tab, or closed when it already shows it). */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, detail = {}) {
  for (const fn of listeners.get(event) || []) {
    try {
      fn(detail);
    } catch (err) {
      console.error(`listener for ${event} failed`, err);
    }
  }
}

export function csrfToken() {
  return document.querySelector('meta[name="csrf"]')?.content || "";
}

export function muyaUrl() {
  return document.querySelector('meta[name="muya"]')?.content || "/muya/dev";
}

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** fetch() for our JSON API: CSRF header, JSON body, JSON answer. Errors throw ApiError with
 *  the server's Estonian message ({"error": "..."}) when it sent one. */
export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = { "X-CSRF": csrfToken(), Accept: "application/json" };
  const init = { method, headers, signal, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const r = await fetch(path, init);
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) {
    const fallback = { 401: "Sisselogimine aegus.", 403: "Sul pole selleks õigust.", 404: "Ei leitud.", 409: "Märget muudetakse teises aknas.", 429: "Liiga palju päringuid, oota veidi." }[r.status];
    throw new ApiError(r.status, data?.error || fallback || `Viga ${r.status}`, data);
  }
  return data;
}

let toastTimer = null;
/** Short message at the bottom of the screen. kind: "info" | "error". */
export function toast(text, kind = "info") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("error", kind === "error");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), kind === "error" ? 6000 : 3000);
}

function setTheme(t, remember) {
  document.documentElement.dataset.teema = t;
  document.getElementById("teema-varv").content = t === "tume" ? "#15181e" : "#ffffff"; // as in theme.js
  if (remember) {
    try {
      localStorage.setItem("teema", t);
    } catch {}
  }
  emit("theme:changed", { theme: t });
}

function setupTheme() {
  document.getElementById("teema")?.addEventListener("click", () => setTheme(document.documentElement.dataset.teema === "tume" ? "hele" : "tume", true));
  // Until the toggle has been used, the page follows the system setting as it changes too.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    let saved = null;
    try {
      saved = localStorage.getItem("teema");
    } catch {}
    if (saved !== "tume" && saved !== "hele") setTheme(e.matches ? "tume" : "hele", false);
  });
}

function markCurrentNav() {
  for (const a of document.querySelectorAll("#nav a")) {
    const href = a.getAttribute("href");
    if (href === location.pathname || (href !== "/" && location.pathname.startsWith(href))) a.setAttribute("aria-current", "page");
  }
}

// Entry module of each work package; one that fails to load is skipped. Written out as import()
// calls so that the bundler (tools/build-app.mjs) finds them; they load after this module has run.
const MODULES = [
  () => import("/static/shell/index.js"), // WP-C
  () => import("/static/calendar/index.js"), // WP-D
  () => import("/static/inbox/index.js"), // WP-E2
  () => import("/static/ai/index.js"), // WP-F
  () => import("/static/share/index.js"), // WP-G
  () => import("/static/related/index.js"), // WP-H
  () => import("/static/graph/index.js"), // WP-I
  () => import("/static/stocks/index.js"), // Aktsiad (lead; tabs by the stocks packages)
];

async function start() {
  setupTheme();
  markCurrentNav();
  const results = await Promise.allSettled(MODULES.map((load) => load()));
  results.forEach((r) => {
    if (r.status === "rejected") console.info("module not loaded:", r.reason?.message);
  });
  const page = document.body.dataset.page;
  const ctx = { page };
  // Side by side, in registration order: one mount's requests (the note, the notes list, Täna's
  // agenda) do not wait for another's.
  await Promise.all(
    (pages.get(page) || []).map(async (mount) => {
      try {
        await mount(ctx);
      } catch (err) {
        console.error(`page ${page} mount failed`, err);
        toast("Lehe osa laadimine ebaõnnestus.", "error");
      }
    }),
  );
  document.documentElement.dataset.ready = "1"; // tests wait for this
}

start();
