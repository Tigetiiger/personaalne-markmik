// The review window for AI edits (docs/AI-REVIEW-PLAN.md): the note's blocks as the note shows
// them, removed text red and struck through, added text green, and each change kept or left out
// on its own. The server renders the Markdown (sanitised like every other page); this module
// only places the diff's markers in what came back.
import { api, toast } from "/static/app.js";
import { MARK, apply, diff, marked, words } from "/static/ai/diff.js";

const CONTEXT = 1; // unchanged blocks shown next to a change

function el(tag, cls, ...children) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  node.append(...children);
  return node;
}

function fromHtml(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const box = el("div");
  box.append(document.importNode(tpl.content, true));
  return box;
}

/** The first MARK character in box's text, as {node, offset, char}; after `from` if given. */
function findMark(box, chars, from) {
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  let started = !from;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    let start = 0;
    if (!started) {
      if (node !== from.node) continue;
      started = true;
      start = from.offset + 1;
    }
    for (let i = start; i < node.data.length; i++) if (chars.includes(node.data[i])) return { node, offset: i, char: node.data[i] };
  }
  return null;
}

/** The MARK pairs in box as <del class="ai-del"> / <ins class="ai-ins">. False when a pair does
 *  not enclose whole elements (it starts inside a link and ends after it, say). */
function placeMarks(box) {
  const pairs = { [MARK.delStart]: [MARK.delEnd, "del"], [MARK.insStart]: [MARK.insEnd, "ins"] };
  for (let a = findMark(box, Object.values(MARK)); a; a = findMark(box, Object.values(MARK))) {
    if (!pairs[a.char]) return false;
    const [endChar, tag] = pairs[a.char];
    const b = findMark(box, [endChar], a);
    if (!b) return false;
    b.node.deleteData(b.offset, 1);
    a.node.deleteData(a.offset, 1);
    const range = document.createRange();
    range.setStart(a.node, a.offset);
    range.setEnd(b.node, b.offset - (a.node === b.node ? 1 : 0));
    try {
      range.surroundContents(el(tag, `ai-${tag}`));
    } catch {
      return false;
    }
  }
  return true;
}

/** box's HTML with one side of the word diff taken out ("ins": the old block, "del": the new). */
function without(box, side) {
  const copy = box.cloneNode(true);
  for (const node of copy.querySelectorAll(`.ai-${side}`)) node.remove();
  for (const node of copy.querySelectorAll(`.ai-${side === "ins" ? "del" : "ins"}`)) node.replaceWith(...node.childNodes);
  copy.normalize();
  return copy.innerHTML;
}

/** One block changed word by word, or null when the markers could not be placed cleanly: taking
 *  the added words out must give the old block exactly, and taking the removed ones out the new. */
function wordBlock(html, oldHtml, newHtml) {
  const box = fromHtml(html);
  if (!placeMarks(box) || without(box, "ins") !== fromHtml(oldHtml).innerHTML || without(box, "del") !== fromHtml(newHtml).innerHTML) return null;
  return box;
}

const excerpt = (text) => {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > 40 ? `${line.slice(0, 40)}…` : line;
};

/**
 * Show how `markdown` would change the note `base` and let the owner keep or leave out each
 * change. On Aktsepteeri the kept ones go into the editor as ordinary edits (replaceMarkdown), onto
 * the note as it is by then. normalize(md): Markdown as the editor writes it, so the model's own
 * spacing and list markers do not show up as changes.
 * Resolves {applied, skipped} (numbers of changes) after Aktsepteeri, null after Loobu.
 */
export async function review({ base, markdown, editor, normalize = (md) => md }) {
  const d = diff(normalize(base), normalize(markdown));
  if (!d.changes.length) {
    toast("AI ei muutnud märkmes midagi.");
    return null;
  }

  // Everything to render, in one request: the context around the changes and the changes.
  const sources = new Map();
  const want = (md) => sources.set(md, sources.size);
  const shown = [];
  d.rows.forEach((row, n) => {
    if ("change" in row) {
      const c = d.changes[row.change];
      shown.push(row);
      for (const r of c.rows) {
        const oldMd = r.old !== undefined ? d.base[r.old].text : null;
        const newMd = r.new !== undefined ? d.next[r.new].text : null;
        r.words = oldMd !== null && newMd !== null ? words(oldMd, newMd) : null;
        if (r.words) want(marked(r.words));
        if (oldMd !== null) want(oldMd);
        if (newMd !== null) want(newMd);
      }
      return;
    }
    const near = d.rows.slice(Math.max(0, n - CONTEXT), n + CONTEXT + 1).some((r) => "change" in r);
    if (near) {
      shown.push(row);
      want(d.base[row.same[0]].text);
    } else if (shown.at(-1)?.fold) shown.at(-1).fold.push(row);
    else shown.push({ fold: [row] });
  });
  // A fold of one block would hide less than its own line takes.
  for (const part of shown) if (part.fold?.length === 1) want(d.base[part.fold[0].same[0]].text);

  let html;
  try {
    ({ html } = await api("/api/render/blocks", { method: "POST", body: { blocks: [...sources.keys()] } }));
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
  const rendered = (md, cls) => {
    const box = fromHtml(html[sources.get(md)]);
    box.className = `ai-review-block ${cls}`;
    return box;
  };

  const doc = el("div", "ai-review-doc");
  const changes = [];
  for (const part of shown) {
    if (part.fold?.length === 1) doc.append(rendered(d.base[part.fold[0].same[0]].text, "ai-review-same"));
    else if (part.fold) doc.append(el("p", "ai-review-fold", `… ${part.fold.length} plokki muutmata …`));
    else if ("same" in part) doc.append(rendered(d.base[part.same[0]].text, "ai-review-same"));
    else {
      const c = d.changes[part.change];
      const box = el("section", "ai-review-change");
      box.setAttribute("aria-label", `Muudatus ${changes.length + 1}`);
      for (const r of c.rows) {
        const oldMd = r.old !== undefined ? d.base[r.old].text : null;
        const newMd = r.new !== undefined ? d.next[r.new].text : null;
        const both = r.words && wordBlock(html[sources.get(marked(r.words))], html[sources.get(oldMd)], html[sources.get(newMd)]);
        if (both) {
          both.className = "ai-review-block ai-review-words";
          box.append(both);
        } else {
          if (oldMd !== null) box.append(rendered(oldMd, "ai-review-del"));
          if (newMd !== null) box.append(rendered(newMd, "ai-review-ins"));
        }
      }
      const toggle = el("button", "ai-review-toggle");
      toggle.type = "button";
      const change = { box, toggle, kept: true, excerpt: excerpt(d.base.slice(c.from, c.to).concat(d.next.slice(c.nFrom, c.nTo))[0].text) };
      const label = () => {
        box.classList.toggle("rejected", !change.kept);
        toggle.replaceChildren(el("span", null, change.kept ? "✕" : "↺"), change.kept ? " Jäta ära" : " Taasta");
        toggle.firstChild.setAttribute("aria-hidden", "true");
      };
      toggle.addEventListener("click", () => {
        change.kept = !change.kept;
        label();
      });
      label();
      box.append(toggle);
      changes.push(change);
      doc.append(box);
    }
  }
  // Links go nowhere here: following one would leave the page with the review open.
  for (const a of doc.querySelectorAll("a[href]")) a.removeAttribute("href");

  const accept = el("button", "primary", "Aktsepteeri");
  const cancel = el("button", null, "Loobu");
  accept.type = cancel.type = "button";
  const title = el("h2", null, "Vaata muudatused üle");
  title.id = "ai-review-title";
  const hint = el("p", "ai-review-hint", "Punane kaob, roheline tuleb juurde. Iga muudatuse saab ära jätta.");
  const dlg = el("dialog", "ai-review", title, hint, doc, el("div", "ai-review-actions", accept, cancel));
  dlg.setAttribute("aria-labelledby", title.id);

  return new Promise((resolve) => {
    const finish = (out) => {
      dlg.close();
      dlg.remove();
      resolve(out);
    };
    cancel.addEventListener("click", () => finish(null));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault(); // Esc = Loobu, and the window goes the same way
      finish(null);
    });
    // Tab stays in the window: past the last button to the first and back.
    dlg.addEventListener("keydown", (e) => {
      if (e.key !== "Tab") return;
      const buttons = [...dlg.querySelectorAll("button:not([disabled])")];
      const edge = e.shiftKey ? buttons[0] : buttons.at(-1);
      if (document.activeElement !== edge && dlg.contains(document.activeElement)) return;
      e.preventDefault();
      (e.shiftKey ? buttons.at(-1) : buttons[0]).focus();
    });
    accept.addEventListener("click", () => {
      const keep = changes.map((c) => c.kept);
      const current = normalize(editor.getMarkdown());
      const { markdown: out, skipped } = apply(d, keep, current);
      if (out !== current) editor.replaceMarkdown(out);
      finish({ applied: keep.filter(Boolean).length - skipped.length, skipped: skipped.length });
      if (skipped.length) {
        toast(`Tekst muutus vahepeal, see jäi muutmata: ${skipped.map((k) => `„${changes[k].excerpt}“`).join(", ")}`, "error");
      }
    });
    document.body.append(dlg);
    dlg.showModal();
    accept.focus();
  });
}
