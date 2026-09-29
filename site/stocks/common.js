// Shared by the Aktsiad tabs: DOM building, Estonian number and date formats, and the sections
// other packages add to a stock's page. Owner: lead.

/** h("button", {type: "button", onclick}, "Lisa"). Children are text nodes, never HTML, so data
 *  from the server cannot inject markup. null/false attributes and children are skipped. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  el.append(...children.flat(Infinity).filter((c) => c != null && c !== false && c !== ""));
  return el;
}

/** SVG element (charts drawn by hand). */
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, v);
  el.append(...children.flat(Infinity).filter((c) => c != null && c !== false && c !== ""));
  return el;
}

const nf = new Map();
function numberFormat(digits) {
  if (!nf.has(digits)) nf.set(digits, new Intl.NumberFormat("et-EE", { minimumFractionDigits: digits, maximumFractionDigits: digits }));
  return nf.get(digits);
}

/** The API sends Decimals as strings: "1234.5" -> "1 234,50". "" for null. */
export function num(value, digits = 2) {
  if (value == null || value === "") return "";
  return numberFormat(digits).format(Number(value));
}

/** money("1234.5", "EUR") -> "1 234,50 EUR" */
export function money(value, currency, digits = 2) {
  return value == null || value === "" ? "" : `${num(value, digits)}${currency ? ` ${currency}` : ""}`;
}

/** A fraction as a signed percentage: "0.0123" -> "+1,23 %". */
export function pct(fraction, digits = 2) {
  if (fraction == null || fraction === "") return "";
  const v = Number(fraction) * 100;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${numberFormat(digits).format(Math.abs(v))} %`;
}

/** "up" | "down" | "flat" for a signed value, for data-suund attributes (never colour alone). */
export function direction(value) {
  const v = Number(value);
  return !value || !v ? "flat" : v > 0 ? "up" : "down";
}

const dayFmt = new Intl.DateTimeFormat("et-EE", { day: "numeric", month: "numeric", year: "numeric" });
const stampFmt = new Intl.DateTimeFormat("et-EE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** "2026-09-30" -> "30.09.2026" */
export function day(iso) {
  return iso ? dayFmt.format(new Date(`${iso.slice(0, 10)}T12:00`)) : "";
}

/** "2026-09-30T06:15:00Z" -> "30.09.2026 09:15" (local) */
export function when(utc) {
  return utc ? stampFmt.format(new Date(utc)) : "";
}

// The verdict as words and a shape, never colour alone (docs/AKTSIAD-PLAN.md).
export const VERDICT = { buy: ["Ostaks", "▲"], hold: ["Hoiaks", "◆"], sell: ["Müüks", "▼"] };
export const CONVICTION = { low: "madal kindlus", medium: "keskmine kindlus", high: "kõrge kindlus" };

export function verdictBadge(v) {
  if (!v) return null;
  const [word, shape] = VERDICT[v.verdict] || [v.verdict, ""];
  return h("span", { class: "ak-hinnang", "data-hinnang": v.verdict, title: CONVICTION[v.conviction] || "" }, h("span", { "aria-hidden": "true" }, shape), ` ${word}`);
}

// Sections other packages add to a stock's page (the research and verdict cards come from the AI
// package). mount(el, ctx) with ctx = {symbol, instrument}; the page gives each its own element.
const sections = new Map();
export function registerTickerSection(id, spec) {
  sections.set(id, { order: 50, ...spec });
}
export const listTickerSections = () => [...sections.entries()].map(([id, v]) => ({ id, ...v })).sort((a, b) => a.order - b.order);
