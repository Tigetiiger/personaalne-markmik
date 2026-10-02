// The lightbox: one file large (a picture's preview, or the video playing), arrows and keys to
// move, and beside it what is known about the file: title, description, tags, date, place.
import { api, toast } from "/static/app.js";
import { h } from "/static/shell/dom.js";

import { bytes, dateTime, duration, fileUrl, thumbUrl, when } from "./format.js";
import { shareBox } from "./share.js";

export function openViewer({ list, index, changed, removed, more }) {
  // list() is the page's current list: loading more or deleting replaces it.
  let i = index;
  let detail = null;
  const opener = document.activeElement;

  const stage = h("div", { class: "md-lava" });
  const side = h("aside", { class: "md-info", "aria-label": "Faili andmed" });
  const prev = h(
    "button",
    {
      type: "button",
      class: "md-nool md-eelmine",
      "aria-label": "Eelmine",
      onclick: () => go(-1),
    },
    "‹",
  );
  const next = h(
    "button",
    {
      type: "button",
      class: "md-nool md-jargmine",
      "aria-label": "Järgmine",
      onclick: () => go(1),
    },
    "›",
  );
  const close = h(
    "button",
    {
      type: "button",
      class: "md-sulge icon-button",
      "aria-label": "Sulge",
      onclick: () => shut(),
    },
    "×",
  );
  const box = h(
    "div",
    {
      class: "md-vaataja",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Vaataja",
    },
    h("div", { class: "md-lava-raam" }, stage, prev, next, close),
    side,
  );
  document.body.append(box);
  document.body.classList.add("md-vaataja-lahti");
  document.addEventListener("keydown", onKey);
  show();

  function onKey(e) {
    if (e.key === "Escape") shut();
    else if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === "ArrowRight") go(1);
  }

  function shut() {
    stage.querySelector("video")?.pause();
    document.removeEventListener("keydown", onKey);
    document.body.classList.remove("md-vaataja-lahti");
    box.remove();
    opener?.focus?.();
  }

  async function go(step) {
    if (i + step >= list().length && step > 0 && !(await more())) return;
    if (i + step < 0 || i + step >= list().length) return;
    i += step;
    show();
  }

  async function show() {
    const item = list()[i];
    prev.disabled = i === 0;
    stage.replaceChildren(
      item.kind === "video"
        ? h("video", {
            src: fileUrl(item.name),
            controls: true,
            autoplay: true,
            playsinline: true,
            preload: "metadata",
            poster: thumbUrl(item.name, 1600),
          })
        : h("img", {
            src: thumbUrl(item.name, 1600),
            alt: item.description || item.title || item.name,
          }),
    );
    side.replaceChildren(h("p", { class: "md-nimi" }, item.name));
    try {
      detail = await api(`/api/meedia/fail/${encodeURIComponent(item.name)}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    if (list()[i] !== item) return; // moved on meanwhile
    renderSide();
    close.focus();
  }

  async function save(body) {
    try {
      detail = await api(`/api/meedia/fail/${encodeURIComponent(detail.name)}`, { method: "PATCH", body });
      changed({ ...list()[i], ...detail });
      renderSide();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function renderSide() {
    const d = detail;
    const title = h("input", {
      type: "text",
      class: "md-pealkiri",
      placeholder: "Pealkiri",
      "aria-label": "Pealkiri",
      value: d.title,
    });
    title.addEventListener("change", () => save({ title: title.value }));
    const description = h("textarea", { rows: 3, placeholder: "Kirjeldus", "aria-label": "Kirjeldus" }, d.description);
    description.addEventListener("change", () => save({ description: description.value }));
    const tagNames = d.tags.map((t) => t.tag);
    const addTag = h("input", {
      type: "text",
      class: "md-uus-silt",
      placeholder: "+ silt",
      "aria-label": "Lisa silt",
      list: "md-sildid",
    });
    addTag.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && addTag.value.trim()) {
        e.preventDefault();
        save({ tags: [...tagNames, addTag.value] });
      }
    });
    const tags = h(
      "div",
      { class: "md-sildid" },
      d.tags.map((t) =>
        h(
          "span",
          {
            class: `chip${t.auto ? " md-auto" : ""}`,
            title: t.auto ? "AI pakutud silt" : null,
          },
          t.auto ? h("i", { class: "badge ai", "aria-label": "AI" }, "AI") : null,
          t.tag,
          h(
            "button",
            {
              type: "button",
              class: "chip-x",
              "aria-label": `Eemalda silt ${t.tag}`,
              onclick: () => save({ tags: tagNames.filter((x) => x !== t.tag) }),
            },
            "×",
          ),
        ),
      ),
      addTag,
    );
    const facts = [
      ["Aeg", d.taken_at ? dateTime(when(d)) : `lisatud ${dateTime(when(d))}`],
      ["Mõõdud", d.width ? `${d.width} × ${d.height}` : null],
      ["Kestus", d.kind === "video" ? duration(d.duration) : null],
      ["Suurus", bytes(d.bytes)],
      ["Kaamera", d.camera],
      [
        "Asukoht",
        d.gps
          ? h(
              "a",
              {
                href: `https://www.openstreetmap.org/?mlat=${d.gps.lat}&mlon=${d.gps.lon}#map=16/${d.gps.lat}/${d.gps.lon}`,
                target: "_blank",
                rel: "noopener noreferrer",
              },
              `${d.gps.lat}, ${d.gps.lon}`,
            )
          : null,
      ],
      ["Teisendatud", d.converted_from ? d.converted_from.name : null],
    ].filter(([, v]) => v);
    side.replaceChildren(
      ...[
        title,
        description,
        tags,
        h(
          "dl",
          { class: "md-faktid" },
          facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]),
        ),
        d.ocr ? h("details", { class: "md-ocr" }, h("summary", {}, "Pildil olev tekst"), h("p", {}, d.ocr)) : null,
        d.ai?.error ? h("p", { class: "md-viga" }, `Automaatsed sildid puuduvad: ${d.ai.error}`) : null,
        h(
          "div",
          { class: "md-tegevused" },
          h("button", { type: "button", "aria-expanded": "false", onclick: (e) => toggleShare(e.currentTarget) }, "Jaga"),
          h(
            "a",
            {
              class: "button",
              href: `${fileUrl(d.name)}?lae=1`,
              download: d.name,
            },
            "Laadi alla",
          ),
          h("button", { type: "button", class: "md-ohtlik", onclick: () => remove() }, "Kustuta"),
        ),
        h("p", { class: "md-nimi" }, d.name),
      ].filter(Boolean),
    );
  }

  function toggleShare(button) {
    const open = side.querySelector(".md-jaga");
    if (open) open.remove();
    else button.closest(".md-tegevused").after(shareBox([detail.name], { name: detail.name }));
    button.setAttribute("aria-expanded", String(!open));
  }

  async function remove() {
    if (!confirm(`Kas viia ${detail.name} prügikasti? 30 päeva jooksul saab selle sealt tagasi.`)) return;
    try {
      await api(`/api/meedia/fail/${encodeURIComponent(detail.name)}`, {
        method: "DELETE",
      });
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    removed(detail.name);
    if (!list().length) return shut();
    i = Math.min(i, list().length - 1);
    show();
  }
}
