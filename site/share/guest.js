// The pages a share link opens: the view page follows the note, the edit page is the owner's
// editor on the same live session.
import { api, muyaUrl, toast } from "/static/app.js";

const POLL_MS = 5000;

/** View link: the server-rendered note, replaced whenever the note's rev moves on. Polls only
 *  while the tab is visible, and stops for good once the link no longer works. */
export function mountView() {
  const doc = document.getElementById("share-doc");
  let rev = Number(doc.dataset.rev);
  let timer = null;
  let busy = false;
  let over = false;

  const schedule = (ms = POLL_MS) => {
    clearTimeout(timer);
    timer = over || document.hidden ? null : setTimeout(poll, ms);
  };
  async function poll() {
    if (busy) return;
    busy = true;
    try {
      const data = await api("/api/share/current");
      if (data.rev !== rev) {
        rev = data.rev;
        // Sanitised on the server (nh3, links to other notes made plain); parsed into inert
        // nodes, and the CSP would not run a script in them anyway.
        const parsed = new DOMParser().parseFromString(data.html, "text/html");
        doc.replaceChildren(...parsed.body.childNodes);
        document.title = `${data.note.title} · Märkmik`;
      }
    } catch (err) {
      if ([401, 403, 404].includes(err.status)) {
        over = true;
        document.getElementById("share-bar").textContent = "See link ei kehti enam.";
      }
    } finally {
      busy = false;
    }
    schedule();
  }
  document.addEventListener("visibilitychange", () => schedule(document.hidden ? POLL_MS : 0));
  schedule();
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
  const slot = document.getElementById("share-actions"); // the test host page (tests/visual/share-*)
  if (slot) {
    const [{ mountShareAction }, note] = await Promise.all([import("/static/share/dialog.js"), api(`/api/notes/${encodeURIComponent(root.dataset.slug)}`)]);
    mountShareAction(slot, { note, editor });
  }
}
