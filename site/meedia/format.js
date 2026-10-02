// Numbers and dates as Meedia shows them.

const TZ = "Europe/Tallinn";

export const fileUrl = (name) => `/meedia/fail/${encodeURIComponent(name)}`;
export const thumbUrl = (name, size = 400) => `/meedia/pisi/${encodeURIComponent(name)}${size === 400 ? "" : `?s=${size}`}`;

/** When a file was taken (or added, without a date): a Date. */
export const when = (item) => new Date(item.taken_at || item.added_at);

export function monthLabel(date) {
  return new Intl.DateTimeFormat("et-EE", {
    timeZone: TZ,
    month: "long",
    year: "numeric",
  }).format(date);
}

export function monthKey(date) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
  }).format(date);
}

export function dateTime(date) {
  return new Intl.DateTimeFormat("et-EE", {
    timeZone: TZ,
    dateStyle: "long",
    timeStyle: "short",
  }).format(date);
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toLocaleString("et-EE", { maximumFractionDigits: value < 10 ? 1 : 0 })} ${units[i]}`;
}

export function duration(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}
