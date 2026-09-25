// Streams from /api/ai/* (server-sent events answering a POST, so no EventSource): onEvent(ev)
// runs for every event, ev being the runner event ({type: "delta" | "tool" | ...}).
import { csrfToken } from "/static/app.js";

const STATUS = { 401: "Sisselogimine aegus.", 404: "Märget ei leitud.", 429: "Liiga palju päringuid, oota veidi." };

export async function streamAi(path, body, onEvent, signal) {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    signal,
    headers: { "X-CSRF": csrfToken(), "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => null);
    throw new Error(data?.error || STATUS[r.status] || `Viga ${r.status}`);
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const data = buffer.slice(0, end).split("\n").find((line) => line.startsWith("data: "));
      buffer = buffer.slice(end + 2);
      if (data) onEvent(JSON.parse(data.slice(6)));
    }
  }
}
