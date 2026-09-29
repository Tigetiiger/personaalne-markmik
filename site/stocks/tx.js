// Aktsiad → Tehingud: add a transaction (only the fields its kind uses), accounts, the list with
// inline edit and delete, and the CSV import (upload, column mapping, preview, confirm).
// /aktsiad/tehingud#lisa focuses the add form. Owner: stocks-portfolio.
import { api, toast } from "/static/app.js";
import { day, h, money, num } from "./common.js";

export const KINDS = {
  buy: "Ost",
  sell: "Müük",
  dividend: "Dividend",
  interest: "Intress",
  split: "Split",
  deposit: "Sissemakse",
  withdrawal: "Väljamakse",
  fee: "Tasu",
};
const ACCOUNT_KINDS = { brokerage: "Väärtpaberikonto", investment_account: "Investeerimiskonto" };
const LABELS = {
  account_id: "Konto",
  kind: "Liik",
  date: "Kuupäev",
  instrument_id: "Väärtpaber",
  qty: "Kogus",
  price: "Hind",
  amount: "Summa",
  ratio: "Suhe",
  fee: "Vahendustasu",
  tax_withheld: "Kinni peetud maks",
  currency: "Valuuta",
  note: "Märkus",
};
const HINTS = {
  date: "nt 29.09.2026 või 2026-09-29 14:30",
  ratio: "uusi aktsiaid ühe vana kohta: 4-ks-1 on 4, 1-ks-10 on 0,1",
  currency: "tühi = väärtpaberi oma (muidu EUR)",
  amount: "dividendil ja intressil bruto",
};
const ALWAYS = ["account_id", "kind", "date", "currency", "note"];

let data = null; // GET /api/stocks/tx
let listBody = null;

/** The transaction form, for adding and for editing a row in place. */
function txForm(values, { submitLabel, onSubmit, onCancel, id }) {
  const inputs = {
    account_id: h("select", { name: "account_id" }, data.accounts.map((a) => h("option", { value: a.id, selected: String(a.id) === values.account_id }, a.name))),
    kind: h("select", { name: "kind" }, Object.entries(KINDS).map(([k, label]) => h("option", { value: k, selected: k === values.kind }, label))),
    date: h("input", { name: "date", value: values.date, autocomplete: "off", required: true }),
    instrument_id: h(
      "select",
      { name: "instrument_id" },
      h("option", { value: "" }, data.instruments.length ? "Vali…" : "Jälgimisnimekiri on tühi"),
      data.instruments.map((i) => h("option", { value: i.id, selected: String(i.id) === values.instrument_id }, `${i.symbol} · ${i.name} (${i.currency})`)),
    ),
    currency: h("input", { name: "currency", value: values.currency, maxlength: 3, autocomplete: "off", spellcheck: "false" }),
    note: h("input", { name: "note", value: values.note, maxlength: 500, autocomplete: "off" }),
  };
  for (const name of ["qty", "price", "amount", "ratio", "fee", "tax_withheld"]) {
    inputs[name] = h("input", { name, value: values[name], inputmode: "decimal", autocomplete: "off" });
  }
  const errors = {};
  const labels = {};
  for (const name of Object.keys(LABELS)) {
    const input = inputs[name];
    errors[name] = h("small", { class: "pf-viga", id: `${id}-${name}-viga`, hidden: true });
    input.id = `${id}-${name}`;
    input.setAttribute("aria-describedby", errors[name].id);
    labels[name] = h("label", { class: "vali", "data-field": name, for: input.id }, h("span", {}, LABELS[name]), input, HINTS[name] && h("small", {}, HINTS[name]), errors[name]);
  }
  const formError = h("p", { class: "pf-viga pf-vormiviga", role: "alert", hidden: true });
  const submit = h("button", { type: "submit", class: "primary" }, submitLabel);
  const form = h(
    "form",
    { class: "vorm pf-tehinguvorm", id, novalidate: true },
    formError,
    h("div", { class: "pf-valjad" }, Object.values(labels)),
    h("div", { class: "pf-nupud" }, submit, onCancel && h("button", { type: "button", onclick: onCancel }, "Loobu")),
  );

  function showKind() {
    const kind = inputs.kind.value;
    form.dataset.kind = kind;
    const used = new Set([...ALWAYS, ...data.uses[kind]]);
    for (const [name, label] of Object.entries(labels)) label.hidden = !used.has(name);
    labels.amount.querySelector("span").textContent = kind === "dividend" || kind === "interest" ? "Summa (bruto)" : "Summa";
    labels.instrument_id.querySelector("span").textContent = kind === "fee" ? "Väärtpaber (kui tasu käib ühe kohta)" : "Väärtpaber";
  }
  function clearErrors() {
    formError.hidden = true;
    for (const [name, el] of Object.entries(errors)) {
      el.hidden = true;
      labels[name].classList.remove("pf-vigane");
      inputs[name].removeAttribute("aria-invalid");
    }
  }
  inputs.kind.addEventListener("change", showKind);
  showKind();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearErrors();
    const body = Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value]));
    submit.disabled = true;
    try {
      await onSubmit(body);
    } catch (err) {
      const fields = err.data?.errors || {};
      for (const [name, text] of Object.entries(fields)) {
        if (!errors[name]) continue;
        errors[name].textContent = text;
        errors[name].hidden = false;
        labels[name].classList.add("pf-vigane");
        inputs[name].setAttribute("aria-invalid", "true");
      }
      const first = Object.keys(fields).find((n) => inputs[n]);
      if (fields.form || !first) {
        formError.textContent = fields.form || err.message;
        formError.hidden = false;
      }
      (first ? inputs[first] : inputs.kind).focus();
    } finally {
      submit.disabled = false;
    }
  });
  form.inputs = inputs;
  return form;
}

function summaryText(t) {
  const v = t.values;
  const c = v.currency;
  if (v.kind === "buy" || v.kind === "sell") return `${num(v.qty, Number.isInteger(Number(v.qty)) ? 0 : 4)} × ${money(v.price, c)}${v.fee ? ` + tasu ${money(v.fee, c)}` : ""}`;
  if (v.kind === "split") return `suhe ${num(v.ratio, Number.isInteger(Number(v.ratio)) ? 0 : 4)}`;
  if (v.kind === "dividend" || v.kind === "interest") return `${money(v.amount, c)}${v.tax_withheld ? `, kinni peetud ${money(v.tax_withheld, c)}` : ""}`;
  return money(v.amount, c);
}

function txRow(t) {
  const row = h(
    "tr",
    { id: `tx-${t.id}`, "data-kind": t.values.kind },
    h("td", {}, day(t.values.date.slice(0, 10)), t.values.date.length > 10 && h("small", { class: "pf-markus" }, t.values.date.slice(11))),
    h("td", {}, t.account),
    h("td", {}, KINDS[t.values.kind]),
    h("td", {}, t.symbol || ""),
    h("td", { class: "ak-num" }, summaryText(t)),
    h("td", { class: "pf-markusveerg" }, t.values.note),
    h(
      "td",
      { class: "pf-tegevused" },
      h("button", { type: "button", class: "ghost", onclick: () => edit(t, row), "aria-label": `Muuda tehingut ${day(t.values.date.slice(0, 10))} ${KINDS[t.values.kind]}` }, "Muuda"),
      h("button", { type: "button", class: "ghost", onclick: () => remove(t, row), "aria-label": `Kustuta tehing ${day(t.values.date.slice(0, 10))} ${KINDS[t.values.kind]}` }, "Kustuta"),
    ),
  );
  return row;
}

function edit(t, row) {
  const cell = h("td", { colspan: 7 });
  const editor = h("tr", { id: `tx-${t.id}`, class: "pf-muudan" }, cell);
  const cancel = () => {
    editor.replaceWith(row);
    row.querySelector("button").focus();
  };
  const form = txForm(t.values, {
    id: `muuda-${t.id}`,
    submitLabel: "Salvesta",
    onCancel: cancel,
    onSubmit: async (body) => {
      const saved = await api(`/api/stocks/tx/${t.id}`, { method: "PUT", body });
      const fresh = txRow(saved);
      editor.replaceWith(fresh);
      toast("Tehing salvestatud.");
      fresh.querySelector("button").focus();
    },
  });
  form.addEventListener("keydown", (e) => e.key === "Escape" && cancel());
  cell.append(form);
  row.replaceWith(editor);
  form.inputs.kind.focus();
}

async function remove(t, row) {
  if (!confirm(`Kustutan tehingu: ${KINDS[t.values.kind]} ${day(t.values.date.slice(0, 10))}${t.symbol ? ` ${t.symbol}` : ""}?`)) return;
  try {
    await api(`/api/stocks/tx/${t.id}`, { method: "DELETE" });
    row.remove();
    toast("Tehing kustutatud.");
    if (!listBody.children.length) await reload();
  } catch (err) {
    toast(err.message, "error");
  }
}

function list(rows) {
  listBody = h("tbody", { id: "tx-read" }, rows.map(txRow));
  if (!rows.length) return h("p", { class: "leht-juhend", id: "tx-tuhi" }, "Tehinguid pole veel.");
  return h(
    "div",
    { class: "ak-tabel-kerib" },
    h(
      "table",
      { class: "ak-tabel pf-tehingud" },
      h("thead", {}, h("tr", {}, ["Kuupäev", "Konto", "Liik", "Väärtpaber", "Summa", "Märkus", ""].map((t, i) => h("th", { class: i === 4 ? "ak-num" : null, scope: "col" }, t)))),
      listBody,
    ),
    rows.length >= data.shown && h("p", { class: "leht-juhend" }, `Näha on viimased ${data.shown} tehingut.`),
  );
}

function accountForm() {
  const name = h("input", { name: "name", required: true, maxlength: 80, autocomplete: "off", id: "konto-nimi" });
  const kind = h("select", { name: "kind", id: "konto-liik" }, Object.entries(ACCOUNT_KINDS).map(([k, label]) => h("option", { value: k }, label)));
  const broker = h("input", { name: "broker", value: "LHV", maxlength: 80, autocomplete: "off", id: "konto-maakler" });
  const form = h(
    "form",
    { class: "vorm pf-kontovorm" },
    h("label", { class: "vali", for: name.id }, h("span", {}, "Nimi"), name),
    h("label", { class: "vali", for: kind.id }, h("span", {}, "Liik"), kind, h("small", {}, "Investeerimiskonto tulu maksustatakse alles väljavõtmisel.")),
    h("label", { class: "vali", for: broker.id }, h("span", {}, "Pank või maakler"), broker),
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Lisa konto")),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const account = await api("/api/stocks/accounts", { method: "POST", body: { name: name.value, kind: kind.value, broker: broker.value } });
      toast(`Konto „${account.name}“ lisatud.`);
      await reload();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  return form;
}

// --- CSV import --------------------------------------------------------------------------------

function importer() {
  const file = h("input", { type: "file", name: "file", accept: ".csv,text/csv,text/plain", id: "import-fail" });
  const account = h("select", { name: "account_id", id: "import-konto" }, data.accounts.map((a) => h("option", { value: a.id }, a.name)));
  const out = h("div", { class: "pf-eelvaade", "aria-live": "polite" });
  const form = h(
    "form",
    { class: "vorm pf-importvorm" },
    h("label", { class: "vali", for: file.id }, h("span", {}, "CSV-fail"), file, h("small", {}, "Panga või maakleri eksport, kuni 2 MB. Veerud tuvastatakse nime järgi; neid saad eelvaates muuta.")),
    h("label", { class: "vali", for: account.id }, h("span", {}, "Konto"), account),
    h("div", {}, h("button", { type: "submit", class: "primary" }, "Eelvaata")),
  );
  let last = null;

  async function preview(body) {
    try {
      last = await api("/api/stocks/import/preview", { method: "POST", body });
      renderPreview();
    } catch (err) {
      out.replaceChildren(h("p", { class: "pf-viga", role: "alert" }, err.message));
    }
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!file.files.length) {
      out.replaceChildren(h("p", { class: "pf-viga", role: "alert" }, "Vali CSV-fail."));
      return;
    }
    const body = new FormData();
    body.append("file", file.files[0]);
    body.append("account_id", account.value);
    preview(body);
  });

  function renderPreview() {
    const p = last;
    const selects = {};
    const mapping = h(
      "fieldset",
      { class: "pf-vastavus", id: "import-vastavus" },
      h("legend", {}, "Veerud"),
      p.fields.map((f) => {
        selects[f] = h("select", { name: `map_${f}`, id: `map-${f}` }, h("option", { value: "" }, "—"), p.headers.map((header) => h("option", { value: header, selected: p.columns[f] === header }, header)));
        return h("label", { class: "vali", for: selects[f].id }, h("span", {}, p.labels[f]), selects[f]);
      }),
      h(
        "div",
        {},
        h(
          "button",
          {
            type: "button",
            onclick: () => preview({ csv_text: p.csv_text, account_id: account.value, mapping: Object.fromEntries(Object.entries(selects).map(([f, s]) => [f, s.value])) }),
          },
          "Eelvaata uuesti",
        ),
      ),
    );
    const facts = [
      p.unmapped.length ? `Kasutamata veerud: ${p.unmapped.join(", ")}.` : null,
      p.decimal_mark ? `Kümnendmärk „${p.decimal_mark}“.` : null,
      `Eraldaja „${p.delimiter === "\t" ? "tab" : p.delimiter}“.`,
    ].filter(Boolean);
    const confirmButton = h("button", { type: "button", class: "strong", disabled: !p.ready }, p.ready ? `Impordi ${p.ready} tehingut` : "Pole midagi importida");
    confirmButton.addEventListener("click", async () => {
      confirmButton.disabled = true;
      try {
        const r = await api("/api/stocks/import/confirm", { method: "POST", body: { csv_text: p.csv_text, account_id: account.value, mapping: Object.fromEntries(Object.entries(selects).map(([f, s]) => [f, s.value])) } });
        const text = r.imported
          ? `Imporditud ${r.imported} tehingut kontole ${r.account}.${r.skipped ? ` ${r.skipped} rida jäi vahele (vead või juba imporditud).` : ""}`
          : "Midagi uut pole importida.";
        await reload(text);
      } catch (err) {
        out.querySelector(".pf-vormiviga")?.remove();
        out.prepend(h("p", { class: "pf-viga pf-vormiviga", role: "alert" }, err.message));
        confirmButton.disabled = false;
      }
    });
    out.replaceChildren(
      p.errors.map((e) => h("p", { class: "pf-viga", role: "alert" }, e)),
      mapping,
      h("p", { class: "leht-juhend" }, facts.join(" ")),
      h("p", { id: "import-valmis" }, `${p.ready} / ${p.total} rida on valmis importima.`),
      p.rows.length > 0 &&
        h(
          "div",
          { class: "ak-tabel-kerib" },
          h(
            "table",
            { class: "ak-tabel", id: "import-read" },
            h("thead", {}, h("tr", {}, ["Rida", "Kuupäev", "Liik", "Väärtpaber", "Kogus", "Hind", "Summa", "Valuuta", "Olek"].map((t) => h("th", { scope: "col" }, t)))),
            h(
              "tbody",
              {},
              p.rows.map((r) =>
                h(
                  "tr",
                  { "data-line": r.line, "data-olek": r.importable ? "valmis" : r.errors.length ? "viga" : "kordus" },
                  h("td", { class: "ak-num" }, String(r.line)),
                  h("td", {}, r.date ? day(r.date) : ""),
                  h("td", {}, r.kind ? KINDS[r.kind] : ""),
                  h("td", {}, r.symbol || ""),
                  h("td", { class: "ak-num" }, r.qty ? num(r.qty, 4) : ""),
                  h("td", { class: "ak-num" }, r.price ? num(r.price, 4) : ""),
                  h("td", { class: "ak-num" }, r.amount ? num(r.amount) : ""),
                  h("td", {}, r.currency || ""),
                  h("td", {}, h("span", { "aria-hidden": "true" }, r.importable ? "✓ " : r.errors.length ? "✕ " : "↺ "), r.status),
                ),
              ),
            ),
          ),
        ),
      h("div", { class: "pf-nupud" }, confirmButton),
    );
  }
  return h("div", {}, form, out);
}

// --- The page ----------------------------------------------------------------------------------

let root = null;

async function reload(message) {
  data = await api("/api/stocks/tx");
  render(message);
}

function render(message) {
  const add = data.accounts.length
    ? txForm(
        { ...Object.fromEntries(Object.keys(LABELS).map((k) => [k, ""])), account_id: String(data.accounts[0].id), kind: "buy", date: data.today },
        {
          id: "lisa-vorm",
          submitLabel: "Lisa tehing",
          onSubmit: async (body) => {
            const saved = await api("/api/stocks/tx", { method: "POST", body });
            toast(`${KINDS[saved.values.kind]} ${day(saved.values.date.slice(0, 10))} salvestatud.`);
            await reload();
            // The next entry is often of the same kind, on the same day and account.
            const f = root.querySelector("#lisa-vorm");
            for (const k of ["account_id", "kind", "date"]) f.inputs[k].value = body[k];
            f.inputs.kind.dispatchEvent(new Event("change"));
            f.inputs.kind.focus();
          },
        },
      )
    : h("p", { class: "leht-juhend" }, "Lisa esmalt konto (allpool), siis saad tehinguid lisada.");
  root.replaceChildren(
    message ? h("p", { class: "pf-teade pf-korras", role: "status" }, message) : "",
    h(
      "section",
      { class: "pf-jaotis", id: "lisa", "aria-labelledby": "lisa-pealkiri" },
      h("h2", { id: "lisa-pealkiri" }, "Lisa tehing"),
      data.instruments.length ? null : h("p", { class: "leht-juhend" }, "Väärtpaberid tulevad Jälgin-lehelt: lisa aktsia või ETF sinna, siis saad selle siin valida. Sisse- ja väljamakseid saab lisada kohe."),
      add,
    ),
    h(
      "section",
      { class: "pf-jaotis", id: "tx-nimekiri", "aria-labelledby": "tx-pealkiri" },
      h("h2", { id: "tx-pealkiri" }, "Tehingud"),
      list(data.rows),
    ),
    h(
      "section",
      { class: "pf-jaotis", id: "import", "aria-labelledby": "import-pealkiri" },
      h("h2", { id: "import-pealkiri" }, "Impordi CSV-failist"),
      data.accounts.length ? importer() : h("p", { class: "leht-juhend" }, "Importimiseks on vaja kontot."),
    ),
    h(
      "section",
      { class: "pf-jaotis", id: "kontod", "aria-labelledby": "kontod-pealkiri" },
      h("h2", { id: "kontod-pealkiri" }, "Kontod"),
      data.accounts.length
        ? h("ul", { class: "pf-kontod" }, data.accounts.map((a) => h("li", {}, h("strong", {}, a.name), ` · ${ACCOUNT_KINDS[a.kind]} · ${a.broker} · ${a.tx_count} tehingut`)))
        : null,
      data.accounts.length ? h("details", {}, h("summary", {}, "Lisa konto"), accountForm()) : accountForm(),
    ),
  );
}

function focusAdd() {
  if (location.hash !== "#lisa") return;
  const target = root.querySelector("#lisa-vorm select[name=kind]") || root.querySelector("#konto-nimi");
  root.querySelector("#lisa").scrollIntoView({ block: "start" });
  target?.focus();
}

export async function mount(el) {
  root = h("div", { class: "pf-tx" });
  el.append(root);
  await reload();
  focusAdd();
  window.addEventListener("hashchange", focusAdd);
}
