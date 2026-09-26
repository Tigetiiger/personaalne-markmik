// What an AI edit changes in a note, for the review window (docs/AI-REVIEW-PLAN.md). Pure: no DOM
// and no imports, so tests run it in Node. A note is split into top-level blocks, each list item
// on its own; a change is a run of adjacent changed blocks, kept or left out as a whole.

const FENCE = /^ {0,3}(`{3,}|~{3,}|\$\$)/;
const NESTED_FENCE = /^\s*(`{3,}|~{3,}|\$\$)/;
const HEADING = /^ {0,3}#{1,6}(\s|$)/;
const ITEM = /^ {0,3}([-*+]|\d{1,9}[.)])(\s|$)/;
// What ends a paragraph without a blank line (CommonMark: only a list starting at 1 may).
const INTERRUPTS = /^ {0,3}([-*+]|1[.)])\s/;
const TOKEN = /\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]+/gu;
const TABLE_MAX = 250_000; // cells of an LCS table; past that, no pairs
const SIMILAR = 0.4; // share of words two blocks must have in common to be shown word by word

/** Around removed (del) and added (ins) words in the Markdown the server renders for a changed
 *  block: private-use characters, which Markdown and the sanitiser leave alone. */
export const MARK = { delStart: "", delEnd: "", insStart: "", insEnd: "" };

const indent = (line) => line.match(/^ */)[0].length;
const startsBlock = (line) => FENCE.test(line) || HEADING.test(line) || INTERRUPTS.test(line);

/** Where a fence opened by `mark` on line i closes: the index after its closing line (or the
 *  end). A fence nested in a list item closes however far its closing line is indented. */
function fenceEnd(lines, i, mark, nested = false) {
  const lead = nested ? "\\s*" : " {0,3}";
  const close = new RegExp(`^${lead}${mark === "$$" ? "\\$\\$" : `${mark[0] === "`" ? "`" : "~"}{${mark.length},}`}\\s*$`);
  for (i++; i < lines.length; i++) if (close.test(lines[i])) return i + 1;
  return lines.length;
}

/** A list item with what belongs to it: lines indented past its marker (nested lists, more
 *  paragraphs after a blank line), lines that continue its text, and the whole of a fence opened
 *  in it: a code line there that looks like a heading or an item is still code. */
function itemEnd(lines, i) {
  const own = indent(lines[i]);
  const opens = (text, at) => {
    const fence = text.match(NESTED_FENCE);
    return fence ? fenceEnd(lines, at, fence[1], true) : at + 1;
  };
  let end = opens(lines[i].replace(ITEM, ""), i);
  let blank = false;
  for (i = end; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) blank = true;
    else if (indent(line) > own) {
      end = opens(line, i);
      i = end - 1;
      blank = false;
    } else if (blank || ITEM.test(line) || startsBlock(line)) break;
    else end = i + 1;
  }
  return end;
}

/** The note's top-level blocks: {kind, text, sep, after, end}. sep: the text between the block and
 *  the one before it (before the first: the note's leading blank lines); after: that block's key,
 *  null for the first; end: after the last block, the text that ends the note. joinBlocks puts
 *  them back together exactly. */
export function splitBlocks(md) {
  const lines = md.split("\n");
  const blocks = [];
  let gap = 0; // where the text since the last block starts
  for (let i = 0; i < lines.length; ) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const start = i;
    let kind;
    const fence = line.match(FENCE);
    if (fence) {
      kind = "code";
      i = fenceEnd(lines, i, fence[1]);
    } else if (HEADING.test(line)) {
      kind = "heading";
      i++;
    } else if (ITEM.test(line)) {
      kind = "item";
      i = itemEnd(lines, i);
    } else {
      kind = line.trimStart().startsWith("|") ? "table" : line.trimStart().startsWith(">") ? "quote" : "para";
      for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines[i]); i++);
    }
    const prev = blocks.at(-1);
    const between = lines.slice(gap, start).map((l) => `${l}\n`).join("");
    blocks.push({ kind, text: lines.slice(start, i).join("\n"), sep: prev ? `\n${between}` : between, after: prev ? key(prev) : null });
    gap = i;
  }
  if (blocks.length) blocks.at(-1).end = lines.slice(gap).map((l) => `\n${l}`).join("");
  return blocks;
}

/** Blocks back into Markdown. A block keeps the spacing it had where it came from while it
 *  follows the same block as there; after another one, an item after an item keeps its own and
 *  anything else gets a blank line, so it cannot run into what now comes before it. */
export function joinBlocks(blocks) {
  const out = blocks.map((b, i) => {
    const prev = blocks[i - 1];
    if (!prev) return (b.after === null ? b.sep : "") + b.text;
    if (b.after === key(prev)) return b.sep + b.text;
    const sep = b.after === null ? "\n" : b.sep;
    return (prev.kind === "item" && b.kind === "item") || /\n[ \t]*\n/.test(sep) ? sep + b.text : `\n\n${b.text}`;
  });
  return out.length ? out.join("") + (blocks.at(-1).end ?? "\n") : "";
}

// What makes two blocks the same: trailing spaces aside, and an ordered item's number aside
// (inserting an item renumbers the rest; the editor numbers them anyway).
const key = (b) => (b.kind === "item" ? b.text.replace(/^( {0,3})\d{1,9}([.)])/, "$1#$2") : b.text).trimEnd();

/** Longest common subsequence of x and y under `match`, as steps in order: {i, j} (a pair),
 *  {i} (only in x), {j} (only in y). The common start and end are paired first. */
function align(x, y, match) {
  let start = 0;
  while (start < x.length && start < y.length && match(x[start], y[start])) start++;
  let end = 0;
  while (end < x.length - start && end < y.length - start && match(x[x.length - 1 - end], y[y.length - 1 - end])) end++;
  const steps = [];
  for (let k = 0; k < start; k++) steps.push({ i: k, j: k });
  const n = x.length - start - end;
  const m = y.length - start - end;
  const table = n * m <= TABLE_MAX ? Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0)) : null;
  const same = (i, j) => match(x[start + i], y[start + j]);
  if (table) {
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--) table[i][j] = same(i, j) ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (table && i < n && j < m && same(i, j) && table[i][j] === table[i + 1][j + 1] + 1) steps.push({ i: start + i++, j: start + j++ });
    else if (i < n && (j >= m || !table || table[i + 1][j] >= table[i][j + 1])) steps.push({ i: start + i++ });
    else steps.push({ j: start + j++ });
  }
  for (let k = 0; k < end; k++) steps.push({ i: x.length - end + k, j: y.length - end + k });
  return steps;
}

/** How `nextMd` differs from `baseMd`, block by block:
 *  base, next: their blocks;
 *  changes: [{from, to, nFrom, nTo, rows}] — base blocks from..to replaced by next blocks
 *    nFrom..nTo; rows pair an old block with a new one of the same kind ({old, new}), or list one
 *    alone ({old} removed, {new} added), in order;
 *  rows: the whole note in order, {same: [i, j]} or {change: k}. */
export function diff(baseMd, nextMd) {
  const base = splitBlocks(baseMd);
  const next = splitBlocks(nextMd);
  const changes = [];
  const rows = [];
  let open = null;
  let bi = 0;
  let bj = 0;
  for (const step of align(base.map(key), next.map(key), (a, b) => a === b)) {
    if (step.i !== undefined && step.j !== undefined) {
      open = null;
      rows.push({ same: [bi++, bj++] });
      continue;
    }
    if (!open) {
      open = { from: bi, to: bi, nFrom: bj, nTo: bj };
      rows.push({ change: changes.length });
      changes.push(open);
    }
    if (step.i !== undefined) open.to = ++bi;
    else open.nTo = ++bj;
  }
  for (const c of changes) {
    const old = base.slice(c.from, c.to);
    const add = next.slice(c.nFrom, c.nTo);
    c.rows = align(old, add, (a, b) => a.kind === b.kind).map((s) => ({
      ...(s.i !== undefined && { old: c.from + s.i }),
      ...(s.j !== undefined && { new: c.nFrom + s.j }),
    }));
  }
  return { base, next, changes, rows };
}

/** The note with the kept changes (keep[k] for d.changes[k]) put in. Onto `current`, the note as
 *  it is now, when it has changed since the base: a change goes where its base blocks still stand
 *  together and unchanged (an addition, next to the block it followed or preceded); one that
 *  cannot is skipped. Returns {markdown, skipped: [change index]}. */
export function apply(d, keep, current) {
  const doc = current === undefined ? d.base : splitBlocks(current);
  const at = d.base.map(() => -1); // where each base block is in doc
  if (current === undefined) d.base.forEach((_, i) => (at[i] = i));
  else for (const s of align(d.base.map(key), doc.map(key), (a, b) => a === b)) if (s.i !== undefined && s.j !== undefined) at[s.i] = s.j;
  const out = doc.slice();
  const skipped = [];
  // From the last change back: positions in doc rise with the base's, so earlier ones stay put.
  for (let k = d.changes.length - 1; k >= 0; k--) {
    if (!keep[k]) continue;
    const c = d.changes[k];
    let pos = -1;
    if (c.to > c.from) {
      pos = at[c.from];
      for (let i = c.from; i < c.to && pos >= 0; i++) if (at[i] !== pos + i - c.from) pos = -1;
    } else if (c.from > 0 && at[c.from - 1] >= 0) pos = at[c.from - 1] + 1;
    else if (c.from < d.base.length && at[c.from] >= 0) pos = at[c.from];
    else if (c.from === 0) pos = 0;
    else if (c.from === d.base.length) pos = doc.length;
    if (pos < 0) {
      skipped.unshift(k);
      continue;
    }
    // The block after the change, still in place, is spaced from it as in the proposal.
    const follows = c.to < d.base.length && at[c.to] === pos + c.to - c.from ? d.next[c.nTo] : null;
    const put = d.next.slice(c.nFrom, c.nTo);
    out.splice(pos, c.to - c.from, ...put);
    if (follows) out[pos + put.length] = { ...out[pos + put.length], sep: follows.sep, after: follows.after };
  }
  return { markdown: joinBlocks(out), skipped };
}

/** Two versions of a block word by word: [{same}] and [{del, ins}] regions in order (either side
 *  of a change may be ""), or null when they have too little in common, or are too long, to be
 *  read that way. */
export function words(a, b) {
  const x = a.match(TOKEN) || [];
  const y = b.match(TOKEN) || [];
  if (x.length * y.length > TABLE_MAX) return null;
  const regions = [];
  let common = 0;
  for (const s of align(x, y, (p, q) => p === q)) {
    const last = regions[regions.length - 1];
    if (s.i !== undefined && s.j !== undefined) {
      if (last && "same" in last) last.same += x[s.i];
      else regions.push({ same: x[s.i] });
      if (x[s.i].trim()) common += x[s.i].length;
      continue;
    }
    const change = last && "del" in last ? last : regions[regions.push({ del: "", ins: "" }) - 1];
    if (s.i !== undefined) change.del += x[s.i];
    else change.ins += y[s.j];
  }
  // Spaces alone between two changes join them: one change reads better than word, space, word.
  for (let k = regions.length - 2; k > 0; k--) {
    const r = regions[k];
    if ("same" in r && !r.same.trim() && "del" in regions[k - 1] && "del" in regions[k + 1]) {
      const [before, after] = [regions[k - 1], regions[k + 1]];
      regions.splice(k - 1, 3, { del: before.del + r.same + after.del, ins: before.ins + r.same + after.ins });
    }
  }
  const size = (s) => s.replace(/\s+/g, "").length;
  if (2 * common < SIMILAR * (size(a) + size(b))) return null;
  return regions;
}

/** The block's Markdown with the regions' removed and added words between MARK characters. */
export function marked(regions) {
  return regions.map((r) => ("same" in r ? r.same : (r.del && MARK.delStart + r.del + MARK.delEnd) + (r.ins && MARK.insStart + r.ins + MARK.insEnd))).join("");
}
