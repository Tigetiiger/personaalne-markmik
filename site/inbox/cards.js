// Ühendused cards (overview §15), shared by Seaded → Ühendused and the first-run wizard. One card
// per entry of GET /api/connections. The server sends secrets only masked (••••1234), so secret
// inputs always start empty; an empty field on a stored secret keeps it.
import { api, toast } from "/static/app.js";
import { busy, h, when } from "./dom.js";

// Step-by-step instructions. Static HTML written here, never data from the server.
const GUIDES = {
  claude: [
    "Kui token on juba <code>.env</code> failis (<code>CLAUDE_CODE_OAUTH_TOKEN</code>), pole vaja midagi teha: vajuta <b>Testi</b>.",
    "Uue tokeni saamiseks ava VM-is terminal ja käivita <code>claude setup-token</code>.",
    "Logi avanenud lehel oma Claude'i kontoga sisse ja kinnita.",
    "Kopeeri terminali ilmunud token (algab <code>sk-ant-</code>), kleebi see siia ja vajuta <b>Salvesta</b>.",
    "<b>Testi</b> teeb ühe väikese päringu ja näitab mudelit ning vastamise aega.",
  ],
  ekool: [
    'Sisesta sama kasutajanimi ja parool, millega logid sisse aadressil <a href="https://ekool.eu" target="_blank" rel="noopener">ekool.eu</a>.',
    "Vajuta <b>Salvesta</b>. Parool hoitakse krüpteeritult ja seda ei näidata enam kunagi.",
    "<b>Testi</b> logib eKooli sisse ning näitab õpilase nime ja eelolevate kodutööde ja kontrolltööde arvu.",
  ],
  brain: [
    'Ava GitHubis <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener"><b>Settings → Developer settings → Fine-grained tokens → Generate new token</b></a>.',
    "<b>Repository access</b>: vali <b>Only select repositories</b> ja sealt <code>Tigetiiger/brain</code>.",
    "<b>Permissions → Contents</b>: <b>Read and write</b>.",
    "Vajuta <b>Generate token</b>, kopeeri token (algab <code>github_pat_</code>), kleebi see siia ja vajuta <b>Salvesta</b>.",
    "<b>Testi</b> kontrollib ligipääsu (<code>git ls-remote</code>), kloonib repo ja näitab viimast commit'i.",
  ],
  ntfy: [
    "Serveri aadress on juba täidetud (VM-i ntfy server).",
    "Mõtle välja teema nimi, mida keegi ära ei arva (nt <code>markmik-</code> ja paar juhuslikku sõna), ja telli sama teema telefoni ntfy rakenduses.",
    "Tokenit on vaja ainult siis, kui server nõuab sisselogimist.",
    "Vajuta <b>Salvesta</b> ja siis <b>Testi</b>: telefoni tuleb proovisõnum.",
  ],
  ics: [
    "Kopeeri kalendri ICS-aadress: eKooli kooliürituste voog, Moodle või mõni muu kalender. See algab tavaliselt <code>https://</code> või <code>webcal://</code>.",
    "Kleebi see siia, anna voole nimi ja vajuta <b>Lisa</b>. Sama moodi saad lisada mitu voogu.",
    "<b>Testi</b> näitab, mitu sündmust leiti.",
  ],
  phone: [
    "Vajuta <b>Kopeeri</b>.",
    "iPhone: <b>Seaded → Kalender → Kontod → Lisa konto → Muu → Lisa tellitud kalender</b> ja kleebi aadress.",
    "Android: paigalda rakendus <b>ICSx⁵</b> ja lisa seal uus tellimus selle aadressiga.",
    "Aadress on salajane: kes seda teab, näeb su kalendrit. Ära jaga seda.",
  ],
};
const GOOGLE_CLIENT = [
  'Ava <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Google Cloud Console</a> ja loo uus projekt (nt „Märkmik“).',
  "<b>APIs &amp; Services → Library</b>: otsi <b>Gmail API</b> ja vajuta <b>Enable</b>.",
  "<b>OAuth consent screen</b>: kasutajatüüp <b>External</b>; lisa ulatus <code>…/auth/gmail.readonly</code> ja mõlemad Google'i kontod testkasutajateks.",
  "Vajuta <b>Publish app → In production</b>. Muidu aegub ühendus 7 päevaga.",
  "<b>Credentials → Create credentials → OAuth client ID</b>, rakenduse tüüp <b>Desktop app</b>. Laadi JSON-fail alla.",
  "Laadi see fail siin üles.",
];
const GOOGLE_ACCOUNT = [
  "Vajuta <b>Lisa Google'i konto</b>: uues vahekaardis avaneb Google'i leht.",
  "Vali konto ja luba ligipääs. Kui Google hoiatab „Google hasn't verified this app“, vajuta <b>Advanced → Go to … (unsafe)</b>: see on sinu enda rakendus.",
  "Lõpuks jõuab brauser aadressile, mis algab <code>http://127.0.0.1</code>, ja leht ei avane. Nii peabki olema.",
  "Kopeeri kogu aadress aadressiribalt, kleebi see siia kasti ja vajuta <b>Ühenda konto</b>.",
  "Teise konto jaoks korda samme. Kui kooli konto ei luba ühendust, suuna kooli kirjad Gmaili seadetes isiklikule aadressile.",
];
// Cards with secrets that Katkesta deletes.
const HAS_SECRETS = new Set(["claude", "ekool", "google", "brain", "ntfy"]);

function guide(steps) {
  const ol = h("ol", { class: "uh-juhis" });
  for (const s of steps) {
    const li = document.createElement("li");
    li.innerHTML = s;
    ol.append(li);
  }
  return ol;
}

function status(c) {
  if (!c.available) return ["pole", "Pole veel saadaval"];
  if (c.ok === true) return ["ok", ["Ühendatud", c.summary, when(c.at)].filter(Boolean).join(" · ")];
  if (c.ok === false) return ["viga", [`Viga: ${c.error || "tundmatu viga"}`, when(c.at)].filter(Boolean).join(" · ")];
  return ["pole", c.configured ? "Seadistatud, pole veel testitud" : "Pole ühendatud"];
}

/** A form of text inputs that POSTs its fields as JSON. fields: [name, label, attrs, note]. */
function form(c, reload, fields, { submit = "Salvesta", done = "Salvestatud." } = {}) {
  const f = h(
    "form",
    { class: "vorm" },
    fields.map(([name, label, attrs = {}, note]) => h("label", { class: "vali" }, h("span", {}, label), h("input", { name, ...attrs }), note && h("small", {}, note))),
    h("div", {}, h("button", { type: "submit", class: "primary" }, submit)),
  );
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(f.querySelector("[type=submit]"), async () => {
      await api(`/api/connections/${c.key}`, { method: "POST", body: Object.fromEntries(new FormData(f)) });
      toast(done);
      await reload();
    });
  });
  return f;
}

// new-password: the browser must not fill in the owner's Märkmik password here.
const secret = { type: "password", autocomplete: "new-password", spellcheck: "false" };
const plain = { autocomplete: "off", spellcheck: "false" };
const kept = (masked) => masked && `Salvestatud: ${masked}. Jäta tühjaks, et see alles jätta.`;

/** One source's health as the worker left it: [class, text]. */
function health(s) {
  const ok = s.last_ok_at ? `viimati korras ${when(s.last_ok_at)}` : "";
  if (s.last_error) return ["error", [`Viga: ${s.last_error}`, ok].filter(Boolean).join(" · ")];
  return ["uh-vaike", ok || "pole veel andmeid toonud"];
}

function healthItem(name, s, ...extra) {
  const [cls, text] = health(s);
  return h("li", {}, h("span", { class: "uh-nimi" }, name), h("span", { class: cls }, text), ...extra);
}

function removeButton(label, question, path, done, reload) {
  return h(
    "button",
    {
      type: "button",
      onclick: (e) =>
        busy(e.currentTarget, async () => {
          if (!confirm(question)) return;
          await api(path, { method: "DELETE" });
          toast(done);
          await reload();
        }),
    },
    label,
  );
}

function googleBody(c, reload) {
  const upload = h(
    "form",
    { class: "vorm uh-klient" },
    h("label", { class: "vali" }, h("span", {}, "Kliendi JSON-fail"), h("input", { type: "file", name: "file", accept: ".json,application/json" })),
    h("label", { class: "vali" }, h("span", {}, "…või kleebi faili sisu"), h("textarea", { name: "client_json", rows: 3, ...plain })),
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Laadi üles")),
  );
  upload.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(upload.querySelector("[type=submit]"), async () => {
      const file = upload.elements.file.files[0];
      let body = { client_json: upload.elements.client_json.value };
      if (file) {
        body = new FormData();
        body.append("file", file);
      }
      await api("/api/connections/google/client", { method: "POST", body });
      toast("OAuth-klient salvestatud.");
      await reload();
    });
  });
  const client = h(
    "div",
    { class: "uh-osa" },
    h("h3", {}, "1. OAuth-klient (üks kord)"),
    c.data.has_client ? [h("p", { class: "uh-korras" }, "OAuth-klient on üles laaditud."), h("details", {}, h("summary", {}, "Juhised ja kliendi vahetamine"), guide(GOOGLE_CLIENT), upload)] : [guide(GOOGLE_CLIENT), upload],
  );

  const accounts = c.data.accounts.length
    ? h(
        "ul",
        { class: "uh-nimekiri uh-kontod" },
        c.data.accounts.map((a) =>
          healthItem(a.email, a.ok === false && !a.last_error ? { ...a, last_error: "ühenda uuesti" } : a,
            removeButton("Eemalda", `Eemaldada konto ${a.email}?`, `/api/connections/google/${encodeURIComponent(a.email)}`, "Konto eemaldatud.", reload)),
        ),
      )
    : null;
  const pasted = h("input", { name: "pasted_url", required: true, placeholder: "http://127.0.0.1:…/?state=…&code=…", ...plain });
  const consentLink = h("a", { target: "_blank", rel: "noopener" }, "ava see uuesti");
  const paste = h(
    "form",
    { class: "vorm uh-kleebi", hidden: true },
    h("p", {}, "Luba ligipääs uues vahekaardis. Kui vahekaarti ei avanenud, ", consentLink, "."),
    h("label", { class: "vali" }, h("span", {}, "Aadress, mis algab http://127.0.0.1"), pasted),
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Ühenda konto")),
  );
  paste.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(paste.querySelector("[type=submit]"), async () => {
      const { email } = await api("/api/connections/google/finish", { method: "POST", body: { pasted_url: pasted.value } });
      toast(`Ühendatud: ${email}`);
      await reload();
    });
  });
  const add = h(
    "button",
    {
      type: "button",
      class: "primary uh-lisa-konto",
      disabled: !c.data.has_client,
      onclick: (e) =>
        busy(e.currentTarget, async () => {
          const { url } = await api("/api/connections/google/start", { method: "POST" });
          consentLink.href = url;
          paste.hidden = false;
          window.open(url, "_blank", "noopener");
          pasted.focus();
        }),
    },
    "Lisa Google'i konto",
  );
  const filter = h(
    "form",
    { class: "vorm uh-filter" },
    h(
      "label",
      { class: "vali" },
      h("span", {}, "Saatjad, kelle kirju lugeda (domeen või aadress, üks rea kohta)"),
      h("textarea", { name: "senders", rows: 3, placeholder: "gag.ee\nekool.eu", ...plain }, (c.data.senders || []).join("\n")),
      h("small", {}, "Tühjaks jättes kehtib vaikimisi kooli saatjate loend."),
    ),
    h("div", {}, h("button", { type: "submit" }, "Salvesta filter")),
  );
  filter.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(filter.querySelector("[type=submit]"), async () => {
      await api("/api/connections/google", { method: "POST", body: { senders: filter.elements.senders.value } });
      toast("Filter salvestatud.");
      await reload();
    });
  });
  return [
    client,
    h("div", { class: "uh-osa" }, h("h3", {}, "2. Kontod (korda iga konto jaoks)"), guide(GOOGLE_ACCOUNT), accounts, h("div", {}, add), paste),
    h("div", { class: "uh-osa" }, h("h3", {}, "3. Kirjade filter"), filter),
  ];
}

function icsBody(c, reload) {
  const feeds = c.data.feeds.length
    ? h(
        "ul",
        { class: "uh-nimekiri uh-vood" },
        c.data.feeds.map((f) =>
          healthItem(!f.name || f.name === f.host ? f.host : `${f.name} (${f.host})`, f,
            removeButton("Eemalda", `Eemaldada voog „${f.name || f.host}“?`, `/api/connections/ics/${f.id}`, "Voog eemaldatud.", reload)),
        ),
      )
    : null;
  return [
    feeds,
    form(
      c,
      reload,
      [
        ["url", "Kalendri aadress", { required: true, placeholder: "https://… või webcal://…", ...plain }],
        ["name", "Nimi", { placeholder: "nt Kooli üritused", ...plain }],
      ],
      { submit: "Lisa", done: "Voog lisatud." },
    ),
  ];
}

function phoneBody(c) {
  const address = h("input", { readonly: true, value: c.data.url, class: "uh-aadress", "aria-label": "Privaatne kalendriaadress" });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(c.data.url);
    } catch {
      // Over plain http (Tailscale) the page is not a secure context and has no clipboard API.
      address.select();
      document.execCommand("copy");
    }
    toast("Aadress kopeeritud.");
  };
  return h("div", { class: "uh-rida" }, address, h("button", { type: "button", class: "primary", onclick: copy }, "Kopeeri"));
}

const BODIES = {
  claude: (c, reload) =>
    form(c, reload, [
      ["token", "Claude'i token", { ...secret, required: true, placeholder: "sk-ant-…" }, c.data.from_env ? `Praegu kasutatakse .env failis olevat tokenit (${c.masked.claude_token}).` : c.masked.claude_token && `Salvestatud: ${c.masked.claude_token}`],
    ]),
  ekool: (c, reload) => [
    c.data.sources.length ? h("ul", { class: "uh-nimekiri uh-allikad" }, c.data.sources.map((s) => healthItem(s.name, s))) : null,
    form(c, reload, [
      ["username", "Kasutajanimi", { ...plain, required: true }, c.masked.ekool_username && `Salvestatud: ${c.masked.ekool_username}`],
      ["password", "Parool", { ...secret, required: true }],
    ]),
  ],
  google: googleBody,
  brain: (c, reload) =>
    form(c, reload, [
      ["remote", "Repo aadress", { ...plain, value: c.data.remote, required: true }],
      ["token", "GitHubi token", { ...secret, required: !c.masked.brain_token, placeholder: "github_pat_…" }, kept(c.masked.brain_token)],
    ]),
  ntfy: (c, reload) =>
    form(c, reload, [
      ["url", "Serveri aadress", { ...plain, value: c.data.url, required: true }],
      ["topic", "Teema", { ...plain, required: !c.data.topic, placeholder: "nt markmik-…" }, kept(c.data.topic)],
      ["token", "Token (kui server nõuab)", secret, kept(c.data.token)],
    ]),
  ics: icsBody,
  phone: phoneBody,
};

function content(c, reload) {
  const [state, text] = status(c);
  const parts = [h("header", { class: "uh-pais" }, h("h2", {}, c.title), h("p", { class: "uh-olek", "data-state": state, role: "status" }, text))];
  if (GUIDES[c.key]) parts.push(guide(GUIDES[c.key]));
  if (c.available && BODIES[c.key]) parts.push(BODIES[c.key](c, reload));
  const actions = h("div", { class: "uh-nupud" });
  if (c.available && c.testable) {
    actions.append(
      h(
        "button",
        {
          type: "button",
          class: "uh-testi",
          onclick: (e) =>
            busy(e.currentTarget, async () => {
              const r = await api(`/api/connections/${c.key}/test`, { method: "POST" });
              toast(r.ok ? "Test õnnestus." : "Test ebaõnnestus.", r.ok ? "info" : "error");
              await reload();
            }),
        },
        "Testi",
      ),
    );
  }
  if (c.available && c.configured && c.refreshable) {
    actions.append(
      h(
        "button",
        {
          type: "button",
          class: "uh-varskenda",
          onclick: (e) =>
            busy(e.currentTarget, async () => {
              const r = await api(`/api/connections/${c.key}/refresh`, { method: "POST" });
              toast(r.ok ? `Värskendatud (${r.items} kirjet).` : r.error || "Värskendamine ebaõnnestus.", r.ok ? "info" : "error");
              await reload();
            }),
        },
        "Värskenda",
      ),
    );
  }
  if (HAS_SECRETS.has(c.key) && c.configured && !c.data.from_env) {
    actions.append(removeButton("Katkesta", `Katkestada ühendus „${c.title}“? Salvestatud andmed kustutatakse.`, `/api/connections/${c.key}`, "Ühendus katkestatud.", reload));
  }
  if (actions.childElementCount) parts.push(actions);
  return parts.flat().filter(Boolean);
}

/** The card element for one entry of GET /api/connections; it redraws itself after each action. */
export function renderCard(card) {
  const el = h("section", { class: "plokk uh-kaart", "data-key": card.key });
  const draw = (c) => el.replaceChildren(...content(c, reload));
  async function reload() {
    draw((await api("/api/connections")).find((c) => c.key === card.key));
  }
  draw(card);
  return el;
}

export async function mountPage() {
  const cards = await api("/api/connections");
  document.getElementById("uh-kaardid").replaceChildren(...cards.map(renderCard));
}
