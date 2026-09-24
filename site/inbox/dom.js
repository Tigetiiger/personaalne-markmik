// Small DOM helpers for the inbox package. h() appends children as text nodes, never as HTML,
// so data from the server cannot inject markup.
import { toast } from "/static/app.js";

/** h("button", {type: "button", onclick}, "Kinnita"). null/false attributes and children are
 *  skipped; true sets an empty attribute. */
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

/** Runs fn with the button disabled; an error becomes a toast with the server's message. */
export async function busy(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.disabled = false;
  }
}

const stamp = new Intl.DateTimeFormat("et-EE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** '2026-09-24T08:15:00Z' -> '24.09.2026 11:15' (local), or "" */
export function when(utc) {
  return utc ? stamp.format(new Date(utc)) : "";
}
