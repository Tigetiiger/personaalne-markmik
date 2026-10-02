// Chunked upload (app/views/meedia.py): start, pieces of `chunk` bytes from where the server is,
// done. A lost piece is retried from what the server says it has. Pieces carry their SHA-256 when
// the browser can make one (crypto.subtle exists only on https and localhost).
import { api, csrfToken } from "/static/app.js";

const TRIES = 4;

async function sha256(buffer) {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function putPiece(id, offset, blob) {
  const body = await blob.arrayBuffer();
  const headers = {
    "X-CSRF": csrfToken(),
    "Content-Type": "application/octet-stream",
    Accept: "application/json",
  };
  const hash = await sha256(body);
  if (hash) headers["X-SHA256"] = hash;
  const r = await fetch(`/api/meedia/upload/${id}?offset=${offset}`, {
    method: "PUT",
    headers,
    body,
    credentials: "same-origin",
  });
  const data = await r.json().catch(() => null);
  if (r.ok) return data.received;
  if (r.status === 409 || (r.status === 400 && data?.received !== undefined)) return data.received; // resume from there
  throw new Error(data?.error || `Viga ${r.status}`);
}

/** Upload one File; progress(fraction) as it goes. Resolves to {name, status, duplicate?}. */
export async function upload(file, progress = () => {}) {
  const start = await api("/api/meedia/upload", {
    method: "POST",
    body: { name: file.name, size: file.size },
  });
  let received = start.received;
  let failures = 0;
  while (received < file.size) {
    try {
      received = await putPiece(start.id, received, file.slice(received, received + start.chunk));
      failures = 0;
      progress(received / file.size);
    } catch (err) {
      failures += 1;
      if (failures >= TRIES) throw err;
      await new Promise((resolve) => setTimeout(resolve, 1000 * failures));
      received = (await api(`/api/meedia/upload/${start.id}`)).received;
    }
  }
  return api(`/api/meedia/upload/${start.id}/done`, {
    method: "POST",
    body: {},
  });
}
