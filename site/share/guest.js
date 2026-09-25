// The pages a share link opens: the view page follows the note, the edit page is the owner's
// editor on the same live session.
import { api, muyaUrl, toast } from "/static/app.js";

const POLL_MS = 5000;
const OTHER_LINK = "Selles brauseris avati vahepeal teine jagatud link. Selle märkme juurde saad tagasi, kui avad selle lingi uuesti.";

/** A browser holds one share link at a time, the last one opened (the session remembers it). A
 *  guest's tab of an earlier link hears of the new one and calls stop(). */
function onOtherLink(stop) {
  if (document.getElementById("nav") || !("BroadcastChannel" in window)) return; // the owner keeps every note
  const channel = new BroadcastChannel("markmik-share");
  channel.onmessage = (e) => {
    if (e.data === location.pathname) return;
    channel.close();
    stop();
  };
  channel.postMessage(location.pathname);
}

/** View link: the server-rendered note, replaced whenever the note's rev moves on. Polls only
 *  while the tab is visible, and stops for good once the link no longer works. */
export function mountView() {
  const doc = document.getElementById("share-doc");
  const bar = document.getElementById("share-bar");
  let rev = Number(doc.dataset.rev);
  let timer = null;
  let busy = false;
  let over = false;
  const end = (text) => {
    over = true;
    clearTimeout(timer);
    bar.textContent = text;
  };

  const schedule = (ms = POLL_MS) => {
    clearTimeout(timer);
    timer = over || document.hidden ? null : setTimeout(poll, ms);
  };
  async function poll() {
    if (busy) return;
    busy = true;
    try {
      const data = await api(`/api/share/current?slug=${encodeURIComponent(doc.dataset.slug)}&rev=${rev}`);
      if (data.html !== undefined) {
        rev = data.rev;
        // Sanitised on the server (nh3, links to other notes made plain); parsed into inert
        // nodes, and the CSP would not run a script in them anyway.
        const parsed = new DOMParser().parseFromString(data.html, "text/html");
        doc.replaceChildren(...parsed.body.childNodes);
        document.title = `${data.note.title} · Märkmik`;
      }
    } catch (err) {
      if (err.status === 403) end(err.message);
      else if ([401, 404].includes(err.status)) end("See link ei kehti enam.");
    } finally {
      busy = false;
    }
    schedule();
  }
  document.addEventListener("visibilitychange", () => schedule(document.hidden ? POLL_MS : 0));
  schedule();
  onOtherLink(() => end(OTHER_LINK));
}

/** Edit link: the editor, who else is here, and whether everything is saved. The guest's name
 *  labels their caret on the others' screens (the server adds "(külaline)"). */
export async function mountEdit() {
  const root = document.getElementById("share");
  const people = document.getElementById("share-people");
  const save = document.getElementById("share-save");
  const place = document.getElementById("share-editor");
  let editor;
  try {
    const { openNote } = await import(`${muyaUrl()}/note-editor.js`);
    editor = await openNote(place, {
      slug: root.dataset.slug,
      guest: true,
      // Links to the owner's other notes do not open for a guest.
      onOpenNote: () => toast("See link viib märkmesse, mida pole sinuga jagatud."),
      onState: ({ saved, participants }) => {
        save.textContent = saved ? "Salvestatud ✓" : "Salvestamata…";
        save.classList.toggle("unsaved", !saved);
        people.textContent = participants.length > 1 ? `Koos: ${participants.join(", ")}` : "";
      },
    });
  } catch (err) {
    place.textContent = `Märget ei saanud avada: ${err.message}`;
    place.classList.add("error");
    return;
  }
  // What could not be saved any more stays in this browser; the editor offers it back next time.
  onOtherLink(() => {
    editor.detach();
    root.querySelector(".share-bar").textContent = OTHER_LINK;
  });
}
