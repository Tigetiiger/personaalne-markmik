// The Meedia page: search and chips, the grid by month, selection with bulk actions, uploads
// and the files still being converted. The filters live in the address, so a reload keeps them.
import { api, toast } from "/static/app.js";
import { debounce, h } from "/static/shell/dom.js";

import { duration, monthKey, monthLabel, thumbUrl, when } from "./format.js";
import { shareDialog } from "./share.js";
import { upload } from "./upload.js";
import { openViewer } from "./viewer.js";

const PAGE = 120;
const POLL_MS = 10_000;

export async function mount(root) {
  const params = new URLSearchParams(location.search);
  const state = {
    q: params.get("q") || "",
    liik: params.get("liik") || "",
    sildita: params.get("sildita") === "1",
    jagatud: params.get("jagatud") === "1",
    alates: params.get("alates") || "",
    kuni: params.get("kuni") || "",
    tags: params.getAll("tag"),
    items: [],
    total: 0,
    selected: new Set(),
    known: [],
  };
  let loading = null;

  const search = h("input", {
    type: "search",
    id: "md-otsi",
    placeholder: "Otsi: sõna, #silt, -#silt",
    "aria-label": "Otsi meediat",
    value: state.q,
  });
  const kindButtons = [
    ["", "Kõik"],
    ["image", "Pildid"],
    ["video", "Videod"],
  ].map(([value, label]) =>
    h(
      "button",
      {
        type: "button",
        class: "ghost",
        "aria-pressed": String(state.liik === value),
        onclick: () => set({ liik: value }),
      },
      label,
    ),
  );
  const shared = h("button", { type: "button", class: "ghost", "aria-pressed": String(state.jagatud), onclick: () => set({ jagatud: !state.jagatud }) }, "Jagatud");
  const untagged = h(
    "button",
    {
      type: "button",
      class: "ghost",
      "aria-pressed": String(state.sildita),
      onclick: () => set({ sildita: !state.sildita }),
    },
    "Sildita",
  );
  const since = h("input", {
    type: "date",
    "aria-label": "Alates",
    value: state.alates,
    onchange: () => set({ alates: since.value }),
  });
  const until = h("input", {
    type: "date",
    "aria-label": "Kuni",
    value: state.kuni,
    onchange: () => set({ kuni: until.value }),
  });
  const picker = h("input", {
    type: "file",
    multiple: true,
    accept: "image/*,video/*,.heic,.heif",
    hidden: true,
    onchange: () => uploadAll([...picker.files]),
  });
  const uploadButton = h("button", { type: "button", class: "strong", onclick: () => picker.click() }, "+ Laadi üles");

  const chips = h("div", { class: "md-kiibid" });
  const waiting = h("div", { class: "md-ootel", "aria-live": "polite" });
  const uploads = h("div", { class: "md-laadimised", "aria-live": "polite" });
  const count = h("p", { class: "md-arv" });
  const grid = h("div", { class: "md-ruudustik" });
  const more = h(
    "button",
    {
      type: "button",
      class: "md-rohkem",
      hidden: true,
      onclick: () => load(true),
    },
    "Näita rohkem",
  );
  const selectionBar = h("div", { class: "md-valik", hidden: true });

  root.replaceChildren(
    h(
      "div",
      { class: "md-riba" },
      search,
      h("div", { class: "md-lylitid", role: "group", "aria-label": "Liik" }, kindButtons),
      untagged,
      shared,
      h("label", { class: "md-kuup" }, "Alates ", since),
      h("label", { class: "md-kuup" }, "Kuni ", until),
      uploadButton,
      picker,
    ),
    chips,
    waiting,
    uploads,
    count,
    grid,
    more,
    selectionBar,
  );

  search.addEventListener(
    "input",
    debounce(() => set({ q: search.value.trim() }), 250),
  );

  // Dropped files from anywhere on the page.
  document.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.types || [])].includes("Files")) {
      e.preventDefault();
      root.classList.add("md-lohistan");
    }
  });
  document.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) root.classList.remove("md-lohistan");
  });
  document.addEventListener("drop", (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    root.classList.remove("md-lohistan");
    uploadAll([...e.dataTransfer.files]);
  });

  function set(changes) {
    Object.assign(state, changes);
    kindButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(["", "image", "video"][i] === state.liik)));
    untagged.setAttribute("aria-pressed", String(state.sildita));
    shared.setAttribute("aria-pressed", String(state.jagatud));
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.liik) p.set("liik", state.liik);
    if (state.sildita) p.set("sildita", "1");
    if (state.jagatud) p.set("jagatud", "1");
    if (state.alates) p.set("alates", state.alates);
    if (state.kuni) p.set("kuni", state.kuni);
    state.tags.forEach((t) => p.append("tag", t));
    history.replaceState(null, "", p.size ? `/meedia?${p}` : "/meedia");
    renderChips();
    load(false);
  }

  function query(offset) {
    const p = new URLSearchParams(location.search);
    p.set("offset", String(offset));
    p.set("limit", String(PAGE));
    return `/api/meedia?${p}`;
  }

  async function load(append) {
    const offset = append ? state.items.length : 0;
    const mine = (loading = api(query(offset)));
    let data;
    try {
      data = await mine;
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    if (mine !== loading) return; // a newer search has started meanwhile
    state.items = append ? state.items.concat(data.items) : data.items;
    state.total = data.total;
    if (!append) state.selected.clear();
    renderGrid();
  }

  function renderChips() {
    const active = state.tags.map((tag) =>
      h(
        "span",
        { class: "chip md-aktiivne" },
        `#${tag}`,
        h(
          "button",
          {
            type: "button",
            class: "chip-x",
            "aria-label": `Eemalda filter ${tag}`,
            onclick: () => set({ tags: state.tags.filter((t) => t !== tag) }),
          },
          "×",
        ),
      ),
    );
    const offered = state.known
      .filter((k) => !state.tags.includes(k.tag))
      .slice(0, 14)
      .map((k) =>
        h(
          "button",
          {
            type: "button",
            class: "chip chip-add",
            onclick: () => set({ tags: [...state.tags, k.tag] }),
          },
          `#${k.tag}`,
        ),
      );
    chips.replaceChildren(...active, ...offered);
  }

  function renderGrid() {
    count.textContent = state.total === 1 ? "1 fail" : `${state.total} faili`;
    const months = new Map();
    state.items.forEach((item, i) => {
      const date = when(item);
      const key = monthKey(date);
      if (!months.has(key)) months.set(key, { label: monthLabel(date), tiles: [] });
      months.get(key).tiles.push(tile(item, i));
    });
    grid.replaceChildren(...[...months.values()].map((m) => h("section", { class: "md-kuu" }, h("h2", {}, m.label), h("div", { class: "md-ruudud" }, m.tiles))));
    if (!state.items.length) grid.append(h("p", { class: "empty-state" }, state.total === 0 && !location.search ? "Siin pole veel midagi. Laadi pilte või videoid üles." : "Midagi ei leitud."));
    more.hidden = state.items.length >= state.total;
    renderSelection();
  }

  function tile(item, index) {
    const selected = state.selected.has(item.name);
    const box = h("input", {
      type: "checkbox",
      class: "md-vali",
      "aria-label": `Vali ${item.name}`,
      checked: selected,
      onclick: (e) => {
        e.stopPropagation();
        toggle(item.name, e.target.checked);
      },
    });
    return h(
      "figure",
      {
        class: `md-ruut${selected ? " valitud" : ""}`,
        dataset: { name: item.name },
      },
      h(
        "button",
        {
          type: "button",
          class: "md-ava",
          title: item.title || item.name,
          "aria-label": `Ava ${item.title || item.name}`,
          onclick: () => (state.selected.size ? toggle(item.name, !state.selected.has(item.name)) : view(index)),
        },
        h("img", {
          src: thumbUrl(item.name),
          alt: "",
          loading: "lazy",
          decoding: "async",
        }),
        item.kind === "video" ? h("span", { class: "md-kestus" }, `▶ ${duration(item.duration)}`) : null,
      ),
      box,
    );
  }

  function toggle(name, on) {
    if (on) state.selected.add(name);
    else state.selected.delete(name);
    grid.querySelector(`figure[data-name="${CSS.escape(name)}"]`)?.classList.toggle("valitud", on);
    const box = grid.querySelector(`figure[data-name="${CSS.escape(name)}"] .md-vali`);
    if (box) box.checked = on;
    renderSelection();
  }

  function renderSelection() {
    const n = state.selected.size;
    selectionBar.hidden = n === 0;
    root.classList.toggle("md-valimas", n > 0);
    if (!n) return;
    const tagInput = h("input", {
      type: "text",
      placeholder: "silt",
      "aria-label": "Silt valitud failidele",
      list: "md-sildid",
    });
    const run = async (body, done) => {
      try {
        await api("/api/meedia/mitu", {
          method: "POST",
          body: { names: [...state.selected], ...body },
        });
        toast(done);
        await refreshKnown();
        load(false);
      } catch (err) {
        toast(err.message, "error");
      }
    };
    selectionBar.replaceChildren(
      h("span", {}, `${n} valitud`),
      tagInput,
      h(
        "button",
        {
          type: "button",
          onclick: () => tagInput.value.trim() && run({ add: [tagInput.value] }, "Silt lisatud."),
        },
        "+ Lisa silt",
      ),
      h(
        "button",
        {
          type: "button",
          onclick: () => tagInput.value.trim() && run({ remove: [tagInput.value] }, "Silt eemaldatud."),
        },
        "− Eemalda silt",
      ),
      h("button", { type: "button", onclick: () => shareDialog([...state.selected]) }, "Jaga"),
      h(
        "button",
        {
          type: "button",
          class: "md-ohtlik",
          onclick: () => confirm(`Kas viia ${n} faili prügikasti? 30 päeva jooksul saab need sealt tagasi.`) && run({ delete: true }, "Prügikastis."),
        },
        "Kustuta",
      ),
      h(
        "button",
        {
          type: "button",
          class: "ghost",
          onclick: () => {
            state.selected.clear();
            renderGrid();
          },
        },
        "Tühista",
      ),
    );
  }

  function view(index) {
    openViewer({
      list: () => state.items,
      index,
      changed: (item) => {
        const i = state.items.findIndex((x) => x.name === item.name);
        if (i >= 0) state.items[i] = item;
        refreshKnown();
      },
      removed: (name) => {
        state.items = state.items.filter((x) => x.name !== name);
        state.total -= 1;
        renderGrid();
      },
      more: async () => {
        if (state.items.length >= state.total) return false;
        await load(true);
        return true;
      },
    });
  }

  async function uploadAll(files) {
    if (!files.length) return;
    let converting = false;
    for (const file of files) {
      const bar = h("progress", { max: 1, value: 0 });
      const row = h("div", { class: "md-laadimine" }, h("span", { class: "md-nimi" }, file.name), bar);
      uploads.append(row);
      try {
        const result = await upload(file, (fraction) => (bar.value = fraction));
        if (result.duplicate) toast(`${file.name} on juba olemas (${result.name}).`);
        converting ||= result.status === "processing";
        row.remove();
      } catch (err) {
        bar.remove();
        row.classList.add("error");
        row.append(h("span", {}, err.message), h("button", { type: "button", class: "ghost", onclick: () => row.remove() }, "×"));
      }
    }
    picker.value = "";
    await refreshKnown();
    load(false);
    if (converting) pollWaiting();
  }

  let pollTimer = null;
  async function pollWaiting() {
    clearTimeout(pollTimer);
    let items = [];
    try {
      items = (await api("/api/meedia/ootel")).items;
    } catch {
      return;
    }
    const before = waiting.dataset.busy || "";
    const busy = items.filter((i) => !i.error);
    waiting.replaceChildren(
      ...(busy.length ? [h("p", { class: "md-tootlen" }, busy.length === 1 ? `Töötlen: ${busy[0].name}` : `Töötlen ${busy.length} faili…`)] : []),
      ...items
        .filter((i) => i.error)
        .map((i) =>
          h(
            "p",
            { class: "md-viga" },
            h("span", {}, `${i.name}: ${i.error}`),
            h(
              "button",
              {
                type: "button",
                class: "ghost",
                onclick: async () => {
                  await api(`/api/meedia/fail/${encodeURIComponent(i.name)}`, {
                    method: "DELETE",
                  });
                  pollWaiting();
                },
              },
              "Kustuta",
            ),
          ),
        ),
    );
    waiting.dataset.busy = busy.map((i) => i.name).join("\n");
    if (before && waiting.dataset.busy !== before) load(false); // something finished
    if (busy.length) pollTimer = setTimeout(pollWaiting, POLL_MS);
  }

  async function refreshKnown() {
    try {
      state.known = (await api("/api/meedia/sildid")).tags;
    } catch {
      state.known = [];
    }
    let list = document.getElementById("md-sildid");
    if (!list) document.body.append((list = h("datalist", { id: "md-sildid" })));
    list.replaceChildren(...state.known.map((k) => h("option", { value: k.tag })));
    renderChips();
  }

  await Promise.all([refreshKnown(), load(false), pollWaiting()]);
}
