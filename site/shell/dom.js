// DOM helpers for the shell. Text always goes in through textContent: titles, snippets, folders
// and tags are user text (the CSP stops inline scripts, not injected markup).

/** h("a", {href, class: "x", onclick}, "text", child, ...) -> element. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  el.append(...children.flat().filter((c) => c !== undefined && c !== null && c !== false));
  return el;
}

/** A search snippet as DOM: runs between \x02 and \x03 (search.HL_START/HL_END) become <mark>. */
export function snippet(text) {
  const out = document.createDocumentFragment();
  (text || "").split(/(\x02[^\x03]*\x03)/).forEach((part) => {
    if (part.startsWith("\x02")) out.append(h("mark", {}, part.slice(1, -1)));
    else if (part) out.append(part);
  });
  return out;
}

/** Today's date in Tallinn as YYYY-MM-DD: the server's notion of "today" (util.local_today). */
export function todayIso() {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Tallinn" }).format(new Date());
}

export const noteHref = (slug) => `/m/${encodeURIComponent(slug)}`;

/** A plain click: no modifier keys, left button (the others open a new tab as usual). */
export const plainClick = (e) => e.button === 0 && !(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey);

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function stored(key, fallback = null) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}
