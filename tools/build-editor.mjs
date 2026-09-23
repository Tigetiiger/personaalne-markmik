// Copies MarkText's editor core (@muyajs/core) ES modules to site/muya so the browser loads
// them directly, without a bundler: esbuild broke the order of Muya's circular imports and
// Mermaid never started; native ESM orders the cycles the way the package was built.
// Ported from the Seljakotirobot site (tools/build-editor.mjs). Every patch below throws when
// its target text is missing, so a Muya upgrade fails loudly instead of silently.
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { transformSync } from "esbuild";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "node_modules", "@muyajs", "core", "lib");
const out = join(root, "site", "muya");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "es"), { recursive: true });

let index = readFileSync(join(src, "es", "index.js"), "utf8").replace(
  /import (\w+) from "(\.\.\/assets\/icons\/[^"]+\.png)";/g,
  (_, name, path) => `const ${name} = new URL("${path}", import.meta.url).href;`,
);
if (/from "[^"]+\.png"/.test(index)) throw new Error("PNG-import jäi asendamata");

// Muya 0.2.0 viga: lõigumenüü "Turn into → Order List" jätab loendile algusnumbri
// panemata ja Markdowni tuleb "undefined. tekst". Lisame start: 1 nagu uuel loendil.
const orderListMeta = 't === "order-list" ? s.meta = {\n\t\t\t\t\tdelimiter: d,';
if (!index.includes(orderListMeta)) throw new Error("Muya order-list meta koht muutus");
// Muya logija loeb process.env.NODE_ENV, mida brauseris pole; iga hoiatus (nt undo
// ajal) viskaks ReferenceErrori ja jätaks muudatuse pooleli. Pakkija asendaks selle.
if (!index.includes('process.env.NODE_ENV !== "production"')) throw new Error("Muya logija koht muutus");
index = index.replaceAll('process.env.NODE_ENV !== "production"', "false");
// Muya on töölauaprogrammi jaoks: pildi aadress "/pildid/x.png" on tema meelest failitee ja
// muutub "file:///pildid/x.png", mis brauseris ei lae. Veebis on see lehe enda aadress.
const imageSrc = "src: `file://${e}`";
if (index.split(imageSrc).length !== 2) throw new Error("Muya pildi aadressi koht muutus");
index = index.replace(imageSrc, "src: /^\\/(?!\\/)/.test(e) ? e : `file://${e}`");
// Plokkide menüü avaneb "@"-ga, mitte "/"-ga (kasutaja soov): "/" on tavalises tekstis
// sage ja Firefoxis ka kiirotsingu klahv. Vihje tühjal real ütleb sama eesti keeles.
const quickInsert = 'var SC = (e) => /^[/、]\\S*$/.test(e), CC = (e) => /^[/、]$/.test(e);';
if (index.split(quickInsert).length !== 2) throw new Error("Muya kiirlisamise koht muutus");
index = index.replace(quickInsert, quickInsert.replaceAll("[/、]", "[@、]"));
const hint = '"Type / to insert...": "Type / to insert..."';
if (index.split(hint).length !== 2) throw new Error("Muya vihje koht muutus");
index = index.replace(hint, '"Type / to insert...": "Kirjuta @, et lisada plokk…"');
// Pildi tööriistariba ja pildivalija said viiteks pildi asukoha koopia klõpsuhetkest.
// MarkTextis aken ei keri (kerib redaktori kast), aga siin kerib leht ja floating-ui
// arvutab kerimisel asukoha uuesti vana koopia järgi: riba jäi ekraanile paigale.
// Nüüd küsitakse asukohta elavalt elemendilt. Muya joonistab pildi klõpsamisel uuesti ja
// uue elemendi id on "<vana id>_<n>", nii et asendatud elemendi leiab id eesliite järgi.
for (const [stale, live] of [
  ["getBoundingClientRect: () => e,\n\t\t\t\twidth: t.offsetWidth,\n\t\t\t\theight: t.offsetHeight\n\t\t\t};\n\t\t\tif (n.emit(\"muya-image-toolbar\"",
   "getBoundingClientRect: () => { const s = t.isConnected ? t : document.querySelector(`[id^=\"${t.id}_\"]`), b = s && s.querySelector(\".mu-image-container\"); return b ? b.getBoundingClientRect() : e; },\n\t\t\t\twidth: t.offsetWidth,\n\t\t\t\theight: t.offsetHeight\n\t\t\t};\n\t\t\tif (n.emit(\"muya-image-toolbar\""],
  ["let e = t.getBoundingClientRect(), r = {\n\t\t\t\tgetBoundingClientRect: () => e,",
   "let e = t.getBoundingClientRect(), r = {\n\t\t\t\tgetBoundingClientRect: () => { const s = t.isConnected ? t : document.querySelector(`[id^=\"${t.id}_\"]`); return s ? s.getBoundingClientRect() : e; },"],
  ["n = t.getBoundingClientRect(), r = {\n\t\t\t\t\t\t\tgetBoundingClientRect: () => n,",
   "n = t.getBoundingClientRect(), r = {\n\t\t\t\t\t\t\tgetBoundingClientRect: () => { const s = t.isConnected ? t : document.querySelector(`[id^=\"${t.id}_\"]`); return s ? s.getBoundingClientRect() : n; },"],
]) {
  if (index.split(stale).length !== 2) throw new Error(`Muya pildi viite koht muutus: ${stale.slice(0, 40)}`);
  index = index.replace(stale, live);
}
// Samaaegne muutmine (tools/editor/collab.js): Ctrl+Z võtab tagasi ainult oma muudatused, mitte
// teiste omi (Muya ajalugu teisendab end siis võõraste muudatuste järgi), ja koostöökliendil
// on vaja Muya enda ot-json1 moodulit, et teisendada samaaegseid muudatusi.
const historyDefaults = "maxStack: 100,\n\tuserOnly: !1";
if (index.split(historyDefaults).length !== 2) throw new Error("Muya ajaloo vaikesätete koht muutus");
index = index.replace(historyDefaults, "maxStack: 100,\n\tuserOnly: !0");
if (!/\bry = \/\* @__PURE__ \*\/ Ae\(/.test(index) || !index.includes("ry.type.transform(")) throw new Error("Muya json1 moodul (ry) muutus");
index += "\nexport { ry as json1 };\n";
// "@"-menüüs on ka pilt (kasutaja soov): valik avab pildigalerii (tools/editor.js mountGallery
// püüab label "image" kinni) ja AI (tools/editor/inline-ai.js, label "ai"). Muya
// lõigumenüü "Turn into" neid pakkuma ei tohi, ta ei oskaks.
const menuTail = 'icon: _e\n\t\t\t}\n\t\t]\n\t},\n\t{\n\t\tname: "diagrams",';
if (index.split(menuTail).length !== 2) throw new Error("Muya plokimenüü koht muutus");
index = index.replace(menuTail, 'icon: _e\n\t\t\t},\n\t\t\t{\n\t\t\t\ttitle: "Image",\n\t\t\t\tsubTitle: "Pilt galeriist",\n\t\t\t\tlabel: "image",\n\t\t\t\tshortCut: `${lC}+${dC}+I`,\n\t\t\t\ticon: g\n\t\t\t},\n\t\t\t{\n\t\t\t\ttitle: "AI",\n\t\t\t\tsubTitle: "Kirjuta AI abil",\n\t\t\t\tlabel: "ai",\n\t\t\t\ticon: ce\n\t\t\t}\n\t\t]\n\t},\n\t{\n\t\tname: "diagrams",');
const turnInto = 'return hC.filter((e) => e.label !== "frontmatter");';
if (index.split(turnInto).length !== 2) throw new Error("Muya lõigumenüü koht muutus");
index = index.replace(turnInto, 'return hC.filter((e) => e.label !== "frontmatter" && e.label !== "image" && e.label !== "ai");');
// Visuaaltestide leitud vead (tests/visual):
function patchAll(from, to, what) {
  const n = index.split(from).length - 1;
  if (n < 1) throw new Error(`Muya koht muutus: ${what}`);
  index = index.replaceAll(from, to);
}
// Uuesti tegemine (Ctrl+Shift+Z) pärast kiiret "Enter + tekst" sammu taastas ainult tühja rea
// ja viskas vea: kokku liidetud tagasivõtu operatsioon kannab eemaldatud ploki vana sisu, nii
// et selle pöördoperatsioon tuleb arvutada dokumendi praeguse seisu vastu.
patchAll(
  "let { operation: n, selection: r } = this._stack[e].pop(), i = ry.type.invert(n);",
  "let { operation: n, selection: r } = this._stack[e].pop(), i = ry.type.invertWithDoc(n, this.muya.editor.jsonState.getState());",
  "ajaloo pöördoperatsioon",
);
// "@"-menüü kerib valitud rea nähtavale scrollIntoView-ga, kui kast on veel -9999px peal:
// terve leht hüppas üles. Nüüd keritakse menüükasti ennast (see, mis tegelikult kerib:
// scrollElement on kastis sees ega keri, nii et nooleklahvid ei toonud rida nähtavale).
patchAll(
  'e && e.scrollIntoView({\n\t\t\tbehavior: "smooth",\n\t\t\tblock: "center",\n\t\t\tinline: "start"\n\t\t});',
  'if (e) { let c = e.parentElement; while (c && c !== document.body && c.scrollHeight <= c.clientHeight) c = c.parentElement; if (c && c !== document.body) { const cr = c.getBoundingClientRect(), er = e.getBoundingClientRect(); if (er.top < cr.top) c.scrollTop -= cr.top - er.top; else if (er.bottom > cr.bottom) c.scrollTop += er.bottom - cr.bottom; } }',
  "menüü kerimine",
);
// Menüü nooleklahvid: Muya ei peata klahvi vaikimisi tegevust, nii et brauser keris nooltega
// ka lehte (kursor on rea lõpus, alla minna pole kuhugi: leht hüppas 250 px).
patchAll(
  'if (!(!this.status || !oa(e))) switch (e.key) {\n\t\t\t\tcase Ke.ArrowUp:',
  'if (!(!this.status || !oa(e))) switch (e.key) {\n\t\t\t\tcase Ke.ArrowUp: e.preventDefault();',
  "menüü nooled: leht ei keri",
);
patchAll(
  'case Ke.ArrowDown:\n\t\t\t\tcase Ke.Tab:\n\t\t\t\t\tthis.step("next");',
  'case Ke.ArrowDown:\n\t\t\t\tcase Ke.Tab: e.preventDefault();\n\t\t\t\t\tthis.step("next");',
  "menüü nooled alla: leht ei keri",
);
// Ctrl+Backspace (Alt+Backspace Macis) kustutas ainult ühe märgi: Muya Backspace'i käsitleja
// ei vaata muuteklahve ja rea lõpus (iga teksti-tüki serval) võtab ta ise ühe märgi maha.
// Muuteklahviga jääb brauseri oma sõnakustutus, mida Muya loeb DOM-ist nagu muu sisestuse;
// rea alguses (offset 0) käib plokkide liitmine ikka Muya kaudu.
patchAll(
  'backspaceHandler(e) {\n\t\tvar t;\n\t\tlet { start: n, end: r } = (t = this.getCursor()) == null ? {} : t;\n\t\tif (!n || !r || (n == null ? void 0 : n.offset) !== (r == null ? void 0 : r.offset)) return;\n',
  'backspaceHandler(e) {\n\t\tvar t;\n\t\tlet { start: n, end: r } = (t = this.getCursor()) == null ? {} : t;\n\t\tif (!n || !r || (n == null ? void 0 : n.offset) !== (r == null ? void 0 : r.offset)) return;\n\t\tif ((e.ctrlKey || e.altKey || e.metaKey) && n.offset > 0) return;\n',
  "Ctrl+Backspace kustutab sõna",
);
// Pildi muutmise kast: lisaks aadressile ka allkiri (pildi title, lehel figcaption) —
// Muyas seda väljana polnud, allkirja sai muuta ainult lähtekoodis.
patchAll(
  '\t\t\tz("span.confirm", { on: { click: () => this._handleConfirm() } }, r.t("Confirm Text"))\n\t\t]);\n\t\tTs(e || t, i), this._oldVNode = i;',
  '\t\t\tz("span.confirm", { on: { click: () => this._handleConfirm() } }, r.t("Confirm Text")),\n\t\t\tz("input.title", { props: { placeholder: "Allkiri pildi all (valikuline)", value: this._state.title || "" }, on: { input: (e) => { this._state.title = e.target.value; }, keydown: (e) => this._handleEnter(e) } })\n\t\t]);\n\t\tTs(e || t, i), this._oldVNode = i;',
  "pildi allkirja väli",
);
// Hüpikkast (nt vormindusriba) ei tohi minna ekraani servast välja (mobiilis x = -73).
patchAll(
  "}).then(({ x: e, y: t }) => {\n\t\t\t\tObject.assign(n.style, {\n\t\t\t\t\tleft: `${e}px`,",
  "}).then(({ x: e, y: t }) => {\n\t\t\t\tObject.assign(n.style, {\n\t\t\t\t\tleft: `${Math.max(window.scrollX + 4, Math.min(e, window.scrollX + document.documentElement.clientWidth - n.offsetWidth - 4))}px`,",
  "hüpikkasti asukoht",
);
// Pildi suurendusriba: asukoht akna koordinaatides, aga ribad on dokumendis; ja pildi element
// on klõpsu järel asendatud (uus id "<vana>_<n>").
patchAll(
  "let e = this._reference.getBoundingClientRect();\n\t\tLS.forEach((t) => {",
  "if (!this._reference.isConnected && this._imageInfo) { const s = document.querySelector(`[id^=\"${this._imageInfo.imageId}_\"] .mu-image-container`); if (s) this._reference = s; }\n\t\tlet e = this._reference.getBoundingClientRect(), sx = window.scrollX, sy = window.scrollY;\n\t\tLS.forEach((t) => {",
  "suurendusriba",
);
patchAll("n.style.left = `${e.left - RS}px`, n.style.top = `${e.top + e.height / 2 - zS / 2}px`;", "n.style.left = `${e.left + sx - RS}px`, n.style.top = `${e.top + sy + e.height / 2 - zS / 2}px`;", "suurendusriba vasak");
patchAll("n.style.left = `${e.left + e.width - RS}px`, n.style.top = `${e.top + e.height / 2 - zS / 2}px`;", "n.style.left = `${e.left + sx + e.width - RS}px`, n.style.top = `${e.top + sy + e.height / 2 - zS / 2}px`;", "suurendusriba parem");
// Tühi loendipunkt ("- " ilma tekstita) sai olekus lapsed [] ja redaktoris polnud kohta, kuhu
// kursor panna: punkti ei saanud muuta ega kustutada. Muya lisab tühjale tsitaadile tühja lõigu;
// sama nüüd tühjale loendipunktile. Markdown jääb samaks ("- ").
patchAll(
  'd[0].length === 0 && c.tokenType === "blockquote" && (l = {',
  'd[0].length === 0 && (c.tokenType === "blockquote" || c.tokenType === "list-item") && (l = {',
  "tühi loendipunkt",
);
// Koodiploki viimane tühi rida: kui tekst lõpeb reavahetusega (Enter rea lõpus), ei näita
// brauser <pre> sees seda rida ja Firefox pani kursori trükitud märgi ette ("lol =1\n|f"),
// järgmine Backspace kustutas vale märgi. Lõppu <br>: rida on nähtav, textContent sama.
// Ainult Firefoxis (Ye = Muya enda Firefoxi tuvastus): Chromium töötab ilma selleta õigesti ja
// <br>-iga luges ta kursori asukohaks 0, mis muutis koodiploki Backspace'iga lõiguks.
patchAll(
  "\t\t} else a.innerHTML = o;\n\t\tthis._updateLineNumbers(r);",
  "\t\t} else a.innerHTML = o;\n\t\tYe && r.endsWith(\"\\n\") && a.lastChild?.nodeName !== \"BR\" && a.append(document.createElement(\"br\"));\n\t\tthis._updateLineNumbers(r);",
  "koodiploki viimane rida",
);
index = index.replace(orderListMeta, 't === "order-list" ? s.meta = {\n\t\t\t\t\tstart: 1,\n\t\t\t\t\tdelimiter: d,');
writeFileSync(join(out, "es", "index.js"), index);

cpSync(src, out, {
  recursive: true,
  filter: (p) => {
    const rel = p.slice(src.length + 1);
    return rel === "" || rel.endsWith(".mjs") || rel === "core.css" || rel === "assets" || rel.startsWith("assets/");
  },
});

// Muya 0.2.0 enda Mermaidi-tükk ei käivitu brauseris (ringimport: chunk kutsub
// mermaid.core'i eksporti enne selle defineerimist). Muya laeb Mermaidi ainult
// dünaamiliselt sellest ühest failist, seega asendame selle päris Mermaidi
// ESM-ehitusega; Mermaid laeb oma diagrammitüübid ise kaustast chunks/.
const mermaidSrc = join(root, "node_modules", "mermaid", "dist");
mkdirSync(join(out, "mermaid", "chunks"), { recursive: true });
cpSync(join(mermaidSrc, "mermaid.esm.min.mjs"), join(out, "mermaid", "mermaid.esm.min.mjs"));
cpSync(join(mermaidSrc, "chunks", "mermaid.esm.min"), join(out, "mermaid", "chunks", "mermaid.esm.min"), { recursive: true });
const mermaidEntry = readFileSync(join(src, "es", "index.js"), "utf8").match(/import\("\.\.\/(mermaid\.core-[^"]+\.mjs)"\)/);
if (!mermaidEntry) throw new Error("Muya mermaid.core faili ei leitud");
writeFileSync(join(out, mermaidEntry[1]), 'export { default } from "./mermaid/mermaid.esm.min.mjs";\n');

// KaTeX avaliku lehe valemitele: samast kaustast nagu Mermaid, mitte CDN-ilt (CDN-i skript
// jookseks sisselogitud kasutaja lehel ilma terviklikkuse kontrollita). CSS viitab fontidele
// suhteliselt (fonts/), seega ka need.
const katexSrc = join(root, "node_modules", "katex", "dist");
mkdirSync(join(out, "katex", "contrib"), { recursive: true });
for (const f of ["katex.min.css", "katex.min.js"]) cpSync(join(katexSrc, f), join(out, "katex", f));
cpSync(join(katexSrc, "contrib", "auto-render.min.js"), join(out, "katex", "contrib", "auto-render.min.js"));
cpSync(join(katexSrc, "fonts"), join(out, "katex", "fonts"), { recursive: true });

// Our editor modules (tools/editor/*.js) sit next to Muya and import it relatively.
const editorDir = join(root, "tools", "editor");
for (const name of existsSync(editorDir) ? readdirSync(editorDir) : []) {
  if (!name.endsWith(".js")) continue;
  const code = readFileSync(join(editorDir, name), "utf8")
    .replaceAll('from "@muyajs/core";', 'from "./es/index.js";')
    .replace('import "@muyajs/core/lib/core.css";\n', "");
  writeFileSync(join(out, name), code);
}

// Kiirus: Muya tuleb npm-ist minimeerimata (index.js üksi ~2 MB). Minimeerime iga faili
// eraldi (transform, mitte pakkimine: importid ja ringimportide järjekord jäävad samaks),
// teeme igast tekstifailist valmis .gz koopia ja arvutame ehituse räsi. Server annab
// failid aadressil /muya/<räsi>/..., nii et brauser võib neid igavesti puhverdada.
const files = readdirSync(out, { recursive: true }).map((f) => join(out, f));
const hash = createHash("sha1");
for (const file of files.sort()) {
  if (!/\.(m?js|css|svg)$/.test(file)) continue;
  let code = readFileSync(file);
  if (/\.m?js$/.test(file) && !/\.min\.m?js$/.test(file)) {
    code = Buffer.from(transformSync(code.toString(), { minify: true, format: "esm", legalComments: "none" }).code);
    writeFileSync(file, code);
  }
  hash.update(file.slice(out.length)).update(code);
  writeFileSync(`${file}.gz`, gzipSync(code, { level: 9 }));
}
writeFileSync(join(out, "VERSION"), hash.digest("hex").slice(0, 12));
console.log("site/muya built");
