// Live co-editing: several editors change the same note at once. Ported from the Seljakotirobot
// site (tools/koostoo.js).
//
// Muya expresses every change as an ot-json1 operation (json-change) and can apply someone
// else's (editor.updateContents). The server (app/collab.py) only sequences operations; the
// transforming happens here, after the classic OT client: at most one operation is on its way
// to the server (inflight), changes made meanwhile wait in a queue, and every operation from the
// server is transformed against all of them before it is applied. On a tie the operation earlier
// in the server's order is "left" (its text comes first). Only plain text edits are composed:
// json1's compose of structural ops (a block replaced, then edited inside) can yield an op that
// no longer applies, so those travel one by one.
import { json1 } from "@muyajs/core";

const T = json1.type;
const FAST_MS = 250; // while something is on its way or the others are typing
const CALM_MS = 1000;
const AUTOSAVE_MS = 2000; // plus up to AUTOSAVE_JITTER_MS, so co-editors rarely save at once
const AUTOSAVE_JITTER_MS = 1000;
const HIDDEN_LEAVE_MS = 60_000;
const LOG_KEEP = 5000; // operations remembered for applyBlockEdit

// Conflicts (both toggle one checkbox; one types in a paragraph the other deleted) resolve the
// same way on every client: the left op wins a collision, a removal beats edits inside it.
// Plain type.transform throws on them.
const transform = (op, other, side) => T.transformNoConflict(op, other, side);

/** Only text edits ({es}) in the op: safe to compose. */
const textOnly = (op) => (Array.isArray(op) ? op.every(textOnly) : !op || typeof op !== "object" || Object.keys(op).every((k) => k === "es"));

/** Append op to a list of ops, merging it into the last one when both are plain text edits. */
function enqueue(list, op) {
  const last = list[list.length - 1];
  if (last && textOnly(last) && textOnly(op)) list[list.length - 1] = T.compose(last, op);
  else list.push(op);
  return list;
}

/** Changes that could not be saved, kept in this browser for the next time the note is opened
 *  (note-editor.js offers them). copy: {slug, text, baseText?, doc?, ops?, tab?, opId?}: with doc
 *  and ops (as for carry) they can be merged into whatever the note has become; tab and opId
 *  name the op that was on its way, which the server may have taken after all. One per tab, so
 *  a later tab's copy does not replace one still waiting. */
export function keepCopy(noteId, copy) {
  try {
    localStorage.setItem(`markmik:koopia:${noteId}:${copy.tab || randomId()}`, JSON.stringify({ ...copy, at: Date.now() }));
  } catch {}
}

/** doc with op applied (a kept op that the server took after all). */
export function withOp(doc, op) {
  return T.apply(structuredClone(doc), structuredClone(op));
}

/** The copies kept for a note, oldest first, looked up by slug (the join that tells the note id
 *  needs them): [{key, copy}]. */
export function findCopies(slug) {
  const found = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key.startsWith("markmik:koopia:")) continue;
      const copy = JSON.parse(localStorage.getItem(key));
      if (copy && copy.slug === slug) found.push({ key, copy });
    }
  } catch {}
  return found.sort((x, y) => x.copy.at - y.copy.at);
}

export function csrfToken() {
  return document.querySelector('meta[name="csrf"]')?.content || "";
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** The network: post(url, body) -> JSON | null, beacon(url, body). Tests pass their own. */
export const http = {
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF": csrfToken() },
      body: JSON.stringify(body),
    });
    if (r.status === 409) return r.json(); // {reset: true}
    if (!r.ok) {
      const data = await r.json().catch(() => null);
      const fallback = { 401: "Sisselogimine aegus.", 403: "Sul pole selleks õigust.", 404: "Ei leitud." }[r.status] || `Viga ${r.status}.`;
      throw new HttpError(r.status, data?.error || fallback);
    }
    return r.status === 204 ? null : r.json();
  },
  beacon(url, body) {
    const blob = new Blob([JSON.stringify({ ...body, csrf: csrfToken() })], { type: "application/json" });
    navigator.sendBeacon(url, blob);
  },
};

function randomId() {
  // crypto.randomUUID needs HTTPS; over Tailscale the app is plain HTTP.
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));

/** Join the note's shared session. Returns the base text to open the editor with, the ops after
 *  it, and this tab's client id. */
export async function joinCollab(slug, transport = http) {
  const client = randomId();
  // Kept copies' ops on their way: the server says whether each (or its whole copy) got saved.
  const kept = findCopies(slug);
  const copies = kept.map(({ copy }) => ({ tab: copy.tab || "", op_id: copy.opId || "" }));
  const joined = await transport.post("/api/collab/join", { slug, client, copies });
  return { ...joined, client, slug, kept };
}

/** Muya internals the OT client depends on, patched once per editor:
 *  - Muya batches the user's edits and applies them to its state one animation frame later. A
 *    remote op applied in between would land on a state that lacks them (and the batch would
 *    then be sent relative to the wrong state), so the batch can be flushed on demand.
 *    If that throws, Muya's own state is broken: js.onBroken (the collab client) starts over.
 *  - Its history transforms the undo stack against remote ops with the throwing transform, and
 *    an undo puts back a caret saved before others changed the text: setCursor then threw and
 *    left the history deaf (_ignoreChange stuck). Saved carets now move with remote ops and
 *    are clamped to the text when put back.
 *  - It cannot apply a checkbox tick that came as an op (below).
 *  - It builds blocks from the very objects of an op it applies, and json1 puts them into the
 *    state too: ticking a checkbox then wrote into the op (still waiting to be sent) and into
 *    the state behind the op's back. The state gets its own copy here; see also applyOp. */
export function prepare(muya) {
  const js = muya.editor.jsonState;
  if (js.flushNow) return js.flushNow;
  js.apply = (op) => {
    js._state = T.apply(js._state, structuredClone(op));
  };
  let frame = 0;
  const flush = () => {
    cancelAnimationFrame(frame);
    js._isGoing = false;
    const ops = js._operationCache.reduce(enqueue, []);
    js._operationCache = [];
    for (const op of ops) {
      const prevDoc = js.getState();
      js.apply(op);
      muya.eventCenter.emit("json-change", { op, source: "user", prevDoc, doc: js.getState() });
    }
  };
  js._emitStateChange = () => {
    if (js._isGoing) return;
    js._isGoing = true;
    frame = requestAnimationFrame(() => {
      try {
        flush();
      } catch (err) {
        if (!js.onBroken) throw err;
        js.onBroken(err);
      }
    });
  };
  js.flushNow = flush;

  const history = muya.editor.history;
  const moveSelection = (sel, op) => {
    if (!sel || !sel.anchorPath || !sel.isSelectionInSameBlock) return null;
    const path = T.transformPosition(sel.anchorPath, op);
    return path ? { ...sel, anchorPath: path, focusPath: path } : null;
  };
  const transformStack = (stack, op) => {
    for (let i = stack.length - 1; i >= 0; i--) {
      const own = stack[i].operation;
      stack[i].operation = transform(own, op, "left");
      stack[i].selection = moveSelection(stack[i].selection, op);
      op = transform(op, own, "right");
      if (!stack[i].operation || !stack[i].operation.length) stack.splice(i, 1);
    }
  };
  history.transform = (op) => {
    transformStack(history._stack.undo, op);
    transformStack(history._stack.redo, op);
  };
  history._change = (from, to) => {
    flush(); // keystrokes of this frame first: the stack does not know them yet
    const entry = history._stack[from].pop();
    if (!entry) return;
    const selection = history.selection.getSelection();
    history._lastRecorded = 0;
    history._ignoreChange = true;
    let inverse;
    try {
      inverse = T.invertWithDoc(entry.operation, js.getState());
      applyOp(muya, entry.operation, "user");
    } catch (err) {
      console.warn("collab: an undo step no longer fits the text, dropped", err);
      return;
    } finally {
      history._ignoreChange = false;
    }
    history._stack[to].push({ operation: inverse, selection });
    const sel = entry.selection;
    const block = sel && sel.isSelectionInSameBlock && sel.anchorPath && queryBlock(muya, sel.anchorPath);
    if (block && block.isContent()) {
      const len = block.text.length;
      const start = Math.min(sel.anchor.offset, sel.focus.offset, len);
      block.setCursor(start, Math.min(Math.max(sel.anchor.offset, sel.focus.offset), len), true);
    }
    history.getLastSelection();
  };

  // A tick applied through updateContents ([…item, "meta", "checked", {r, i}]) looks the checkbox
  // up with item.find("checked"), which Muya 0.2.0 never answers: every remote tick threw.
  const Item = muya.editor.scrollPage.constructor.loadBlock("task-list-item");
  if (!Item.prototype.findsCheckbox) {
    const find = Item.prototype.find;
    Item.prototype.find = function (key) {
      return key === "checked" ? this.attachments.head : find.call(this, key);
    };
    Item.prototype.findsCheckbox = true;
  }
  return flush;
}

// Muya's queryBlock consumes the path array it is given.
const queryBlock = (muya, path) => muya.editor.scrollPage.queryBlock([...path]);

// Deep equality regardless of key order (Muya's blocks and its JSON state order keys differently).
function same(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => same(a[k], b[k]));
}

const chars = (text) => Array.from(text); // text-unicode counts code points, not UTF-16 units

/** How document `a` became `b`, as a list of ops applied one after another: nested down to the
 *  changed text or value, so it transforms well against edits elsewhere in the same block. */
function diffOps(a, b, path = [], ops = []) {
  if (same(a, b)) return ops;
  if (Array.isArray(a) && Array.isArray(b)) {
    let start = 0;
    while (start < a.length && start < b.length && same(a[start], b[start])) start++;
    let end = 0;
    while (end < a.length - start && end < b.length - start && same(a[a.length - 1 - end], b[b.length - 1 - end])) end++;
    // The changed middle: blocks that stayed as they were are matched first; between them, blocks
    // of the same type are paired and diffed inside (an edit in one survives a block added next
    // to it), the rest removed or inserted.
    const kind = (v) => (v && typeof v === "object" ? v.name ?? "" : typeof v);
    let at = start; // index in the document as the ops so far left it
    const pairs = (x, y, match, gap) => {
      for (const step of align(x, y, match)) {
        if (step.i !== undefined && step.j !== undefined) {
          if (gap) {
            gap.flush();
            at++; // the same block: nothing to do
          } else diffOps(x[step.i], y[step.j], [...path, at++], ops); // same type: diffed inside
        } else if (gap) gap.add(step);
        else if (step.i !== undefined) ops.push([...path, at, { r: true }]);
        else ops.push([...path, at++, { i: y[step.j] }]);
      }
      if (gap) gap.flush();
    };
    const x = a.slice(start, a.length - end);
    const y = b.slice(start, b.length - end);
    const gap = {
      gx: [],
      gy: [],
      add(step) {
        if (step.i !== undefined) this.gx.push(x[step.i]);
        else this.gy.push(y[step.j]);
      },
      flush() {
        const [gx, gy] = [this.gx, this.gy];
        this.gx = [];
        this.gy = [];
        if (gx.length || gy.length) pairs(gx, gy, (p, q) => kind(p) === kind(q), null);
      },
    };
    pairs(x, y, same, gap);
    return ops;
  }
  const plain = (x) => x && typeof x === "object" && !Array.isArray(x);
  if (plain(a) && plain(b) && a.name === b.name) {
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (!(key in b)) ops.push([...path, key, { r: true }]);
      else if (!(key in a)) ops.push([...path, key, { i: b[key] }]);
      else if (typeof a[key] === "string" && typeof b[key] === "string" && a[key] !== b[key]) {
        const x = chars(a[key]);
        const y = chars(b[key]);
        let p = 0;
        while (p < x.length && p < y.length && x[p] === y[p]) p++;
        let q = 0;
        while (q < x.length - p && q < y.length - p && x[x.length - 1 - q] === y[y.length - 1 - q]) q++;
        const edit = [p || null, x.length - q > p ? { d: x.slice(p, x.length - q).join("") } : null, y.slice(p, y.length - q).join("") || null].filter((c) => c !== null);
        ops.push([...path, key, { es: edit }]);
      } else diffOps(a[key], b[key], [...path, key], ops);
    }
    return ops;
  }
  ops.push([...path, { r: true, i: b }]);
  return ops;
}

/** Longest common subsequence of x and y under `match`, as steps in order: {i, j} (a pair),
 *  {i} (only in x), {j} (only in y). Too large a table: no pairs. */
function align(x, y, match) {
  const table = x.length * y.length <= 250_000 ? Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0)) : null;
  if (table) {
    for (let i = x.length - 1; i >= 0; i--)
      for (let j = y.length - 1; j >= 0; j--)
        table[i][j] = match(x[i], y[j]) ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  const steps = [];
  let i = 0;
  let j = 0;
  while (i < x.length || j < y.length) {
    if (table && i < x.length && j < y.length && match(x[i], y[j]) && table[i][j] === table[i + 1][j + 1] + 1) steps.push({ i: i++, j: j++ });
    else if (i < x.length && (j >= y.length || !table || table[i + 1][j] >= table[i][j + 1])) steps.push({ i: i++ });
    else steps.push({ j: j++ });
  }
  return steps;
}

/** Apply an op to the document. Muya's updateContents patches its blocks for the op shapes
 *  Muya itself makes; a composed or transformed op (several picks and drops at once) can make
 *  it throw halfway or edit the wrong block. Its JSON state is right either way (it is applied
 *  first), so the blocks are checked against it and the top-level blocks that differ are
 *  rebuilt. Throws only when the op cannot apply to the state at all. */
function applyOp(muya, op, source) {
  const js = muya.editor.jsonState;
  const before = js._state;
  let broke = false;
  try {
    muya.editor.updateContents(structuredClone(op), null, source); // its blocks keep the objects
  } catch (err) {
    if (js._state === before) throw err; // json1 builds a new state: this op never applied
    broke = true;
    console.warn("collab: Muya could not patch its blocks for an op, rebuilding them", err);
  }
  const page = muya.editor.scrollPage;
  // Halfway through, it may have put in a DOM node without its block, or the other way round.
  if (broke) for (const node of [...page.domNode.children]) if (node.__MUYA_BLOCK__?.parent !== page) node.remove();
  const want = js.getState();
  const blocks = [];
  for (let b = page.firstChild; b; b = b.next) blocks.push(b);
  const have = blocks.map((b) => b.getState());
  let start = 0;
  while (start < have.length && start < want.length && same(have[start], want[start])) start++;
  if (start === have.length && start === want.length) return;
  let end = 0;
  while (end < have.length - start && end < want.length - start && same(have[have.length - 1 - end], want[want.length - 1 - end])) end++;
  const next = end ? blocks[blocks.length - end] : null;
  for (let i = start; i < blocks.length - end; i++) blocks[i].remove("api");
  for (let i = start; i < want.length - end; i++) page.insertBefore(page.constructor.loadBlock(want[i].name).create(muya, want[i]), next, "api");
}

/** The caret after someone else's change: its block may have moved (json1 path) and its text changed. */
function keepCaret(muya, apply) {
  const focused = muya.domNode.contains(document.activeElement);
  const sel = focused ? muya.editor.selection.getSelection() : null;
  const before = sel && sel.isSelectionInSameBlock && sel.anchorBlock ? {
    path: sel.anchorPath,
    text: sel.anchorBlock.text,
    start: Math.min(sel.anchor.offset, sel.focus.offset),
    end: Math.max(sel.anchor.offset, sel.focus.offset),
  } : null;
  const op = apply();
  if (!before) return;
  const path = T.transformPosition(before.path, op);
  const block = path && queryBlock(muya, path);
  if (!block || !block.isContent()) {
    // Its block was removed: Muya's selection would go on pointing at the dead block (the next
    // paste or Enter inserts next to it and throws). The caret goes to the start of the
    // top-level block now in its place.
    const page = muya.editor.scrollPage;
    let top = page.firstChild;
    for (let i = 0; i < before.path[0] && top && top.next; i++) top = top.next;
    const content = top && top.firstContentInDescendant();
    if (content) content.setCursor(0, 0, true);
    return;
  }
  // Inside the text: an unchanged start stays put, from an unchanged end the distance is kept.
  const a = before.text;
  const b = block.text;
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let q = 0;
  while (q < a.length - p && q < b.length - p && a[a.length - 1 - q] === b[b.length - 1 - q]) q++;
  const move = (x) => (x <= p ? x : x >= a.length - q ? x + b.length - a.length : Math.min(b.length, p + (b.length - q - p)));
  block.setCursor(move(before.start), move(before.end), true);
}

// --- Other people's carets ------------------------------------------------------------------
const COLORS = ["#d0473a", "#2f7d3a", "#3f6c9b", "#8a4fbf", "#c77700", "#0f8b8d"];

// Colour by position in the (server-sorted) participant list: the same for everyone and distinct
// per person; by hash "admin" and "mari" got the same colour.
let order = [];

export function colorFor(name) {
  const i = order.indexOf(name);
  return COLORS[(i < 0 ? order.length : i) % COLORS.length];
}

/** Text offset inside a block -> DOM position (Muya's DOM holds the raw text, markers hidden). */
function domPoint(root, offset) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node;
  let last = null;
  while ((node = walker.nextNode())) {
    if (offset <= node.length) return [node, offset];
    offset -= node.length;
    last = node;
  }
  return last ? [last, last.length] : [root, 0];
}

// The layer sits at the top left of the editor's box, so marks are placed relative to it and
// move along when the page or a pane scrolls.
function drawCursors(muya, layer, list) {
  layer.replaceChildren();
  const origin = layer.getBoundingClientRect();
  for (const { name, cursor } of list) {
    if (!cursor) continue;
    const block = queryBlock(muya, cursor.path);
    if (!block || !block.isContent() || !block.domNode) continue;
    const len = block.text.length;
    const range = document.createRange();
    try {
      range.setStart(...domPoint(block.domNode, Math.min(cursor.start, len)));
      range.setEnd(...domPoint(block.domNode, Math.min(cursor.end, len)));
    } catch {
      continue;
    }
    const color = colorFor(name);
    for (const r of cursor.end > cursor.start ? range.getClientRects() : []) {
      const mark = document.createElement("div");
      mark.className = "mt-remote-selection";
      Object.assign(mark.style, { left: `${r.left - origin.left}px`, top: `${r.top - origin.top}px`, width: `${r.width}px`, height: `${r.height}px`, background: color });
      layer.append(mark);
    }
    range.collapse(false);
    const rects = range.getClientRects();
    const r = rects.length ? rects[rects.length - 1] : block.domNode.getBoundingClientRect();
    const caret = document.createElement("div");
    caret.className = "mt-remote-caret";
    Object.assign(caret.style, { left: `${r.left - origin.left}px`, top: `${r.top - origin.top}px`, height: `${r.height || 20}px`, background: color });
    const label = document.createElement("span");
    label.textContent = name;
    label.style.background = color;
    caret.append(label);
    layer.append(caret);
  }
}

/**
 * Bind a Muya editor to a joined session.
 * onState({ saved, participants, seq }) — for a status line.
 * onReset() — the session is gone (the note was written elsewhere, an op broke, the tab was
 *   hidden too long): open the note again.
 * onError(err) — a request failed; polling goes on.
 */
export function attachCollab(muya, joined, { onState = () => {}, onReset = () => {}, onError = () => {}, cursorLayer = null, readOnly = false, transport = http } = {}) {
  const { session, client } = joined;
  const url = (what) => `/api/collab/${session}/${what}`;
  const flush = prepare(muya);
  let seq = 0;
  let inflight = null; // { op, id } — sent, waiting for the server to take it
  let queue = []; // changes not sent yet, oldest first
  let savedSeq = joined.saved_seq;
  let participants = joined.participants;
  let others = joined.cursors || [];
  let applying = false;
  let timer = null;
  let running = null; // the sync request under way; one at a time
  let stopped = false;
  let othersActiveUntil = 0;
  let saveTimer = null;
  let saving = null;
  let hiddenTimer = null;
  let left = false; // left after being hidden too long; rejoins when visible again
  let lastCursor = null; // kept while the focus is on the toolbar or a dialog
  // The document as the server has it at `seq` (without inflight and queue): what own unsent
  // changes are relative to, should the session end under them (rescue).
  let serverDoc = muya.editor.jsonState.getState();

  // Every op applied to the document since attaching, in order: a block path captured by
  // selection() is carried through the ones that came after it.
  const log = [];
  let logStart = 0;
  const captures = new Map(); // JSON of a captured path -> {at, text}

  const undo = [];
  const listen = (target, type, fn, options) => {
    target.addEventListener(type, fn, options);
    undo.push(() => target.removeEventListener(type, fn, options));
  };

  const pending = () => Boolean(inflight || queue.length);
  const unsaved = () => pending() || seq > savedSeq;
  const redraw = () => cursorLayer && drawCursors(muya, cursorLayer, others);
  const report = () => {
    order = participants;
    onState({ saved: !unsaved(), participants, seq });
  };

  function ownCursor() {
    if (muya.domNode.contains(document.activeElement)) {
      const sel = muya.editor.selection.getSelection();
      lastCursor = sel && sel.isSelectionInSameBlock && sel.anchorBlock
        ? { path: sel.anchorPath, start: Math.min(sel.anchor.offset, sel.focus.offset), end: Math.max(sel.anchor.offset, sel.focus.offset) }
        : null;
    }
    return lastCursor;
  }

  const onChange = (change) => {
    if (!change.op) return;
    const { source } = change;
    const op = structuredClone(change.op); // Muya's blocks may share its objects
    log.push(op);
    if (log.length > LOG_KEEP) {
      log.shift();
      logStart++;
    }
    // Own changes (Ctrl+Z too) go to the others; remote ones were applied here, not sent back.
    // After the session ended they are still kept, for the next one (rescue).
    if (applying || source !== "user" || readOnly) return;
    if (!inflight) inflight = { op, id: randomId() };
    else enqueue(queue, op);
    report(); // unsaved, also while no session takes it
    if (stopped) return;
    redraw(); // own text moved: the others' carets move with it
    clearTimeout(timer);
    timer = null;
    schedule(30);
  };
  muya.on("json-change", onChange);
  undo.push(() => muya.off("json-change", onChange));
  muya.editor.jsonState.onBroken = (err) => fail(err, false);
  undo.push(() => (muya.editor.jsonState.onBroken = null));

  function applyRemote(op) {
    applying = true;
    try {
      keepCaret(muya, () => {
        applyOp(muya, op, "api");
        return op;
      });
    } finally {
      applying = false;
    }
  }

  // A remote op that throws leaves this editor's document in doubt, and the op may break the
  // others too: the session is closed for everyone (the server does it on this report) and all
  // rejoin from the saved text, instead of carrying on with documents that may have drifted
  // apart. When only Muya's own state broke, this editor alone starts over.
  function fail(err, everyone = true) {
    if (stopped) return;
    console.error("collab:", err);
    stopped = true;
    onError(err);
    // A view guest may not end the session for everyone (403): it only rejoins.
    const body = everyone && !readOnly ? { client, error: String((err && err.stack) || err).slice(0, 500) } : { client };
    Promise.resolve(transport.post(url("leave"), body))
      .catch(() => {})
      .then(() => onReset());
  }

  function receive(ops) {
    try {
      flush();
    } catch (err) {
      fail(err, false);
      return;
    }
    for (const o of ops) {
      if (stopped) return;
      if (o.seq !== seq + 1) continue; // seen already
      seq = o.seq;
      try {
        serverDoc = serverDoc && T.apply(serverDoc, structuredClone(o.op));
      } catch {
        serverDoc = null; // no rescue then; the op itself is dealt with below
      }
      if (inflight && o.client === client && o.op_id === inflight.id) {
        // Our op is in the server's order: the next one goes.
        inflight = queue.length ? { op: queue.shift(), id: randomId() } : null;
        continue;
      }
      let op = o.op;
      try {
        if (inflight) {
          const theirs = transform(op, inflight.op, "left");
          inflight.op = transform(inflight.op, op, "right");
          op = theirs;
        }
        queue = queue.map((mine) => {
          const theirs = op && transform(op, mine, "left");
          mine = op ? transform(mine, op, "right") : mine;
          op = theirs;
          return mine;
        }).filter(Boolean);
        // The server never took inflight (someone else's op came first), and now it does
        // nothing: drop it rather than wait for an ack that cannot come.
        if (inflight && !inflight.op) {
          inflight = queue.length ? { op: queue.shift(), id: randomId() } : null;
        }
        if (op) applyRemote(op);
      } catch (err) {
        fail(err);
        return;
      }
      othersActiveUntil = Date.now() + 5000;
    }
  }

  /** One poll. True when a change is waiting that this request did not carry. */
  async function tick() {
    if (stopped) return false;
    const body = { client, seen: seq, cursor: readOnly ? null : ownCursor() };
    const sent = inflight && inflight.id;
    if (inflight) Object.assign(body, { op: inflight.op, op_id: inflight.id, base: seq });
    try {
      const r = await transport.post(url("sync"), body);
      if (stopped) return;
      if (r.reset) {
        // The op on its way had got into the log and been saved (its answer was lost): done,
        // not to be carried into the next session a second time.
        if (r.saved && inflight) {
          try {
            serverDoc = serverDoc && T.apply(serverDoc, structuredClone(inflight.op));
          } catch {
            serverDoc = null;
          }
          inflight = queue.length ? { op: queue.shift(), id: randomId() } : null;
        }
        stopped = true;
        report();
        onReset();
        return;
      }
      receive(r.ops);
      if (stopped) return;
      savedSeq = Math.max(savedSeq, r.saved_seq); // a request begun during a save must not turn it back
      participants = r.participants;
      if (JSON.stringify(r.cursors) !== JSON.stringify(others)) othersActiveUntil = Date.now() + 5000;
      others = r.cursors;
      redraw();
      report();
      autosave();
    } catch (err) {
      if (stopped) return false;
      onError(err);
      // Too large for the server: it can never be sent, and without it this document no longer
      // matches the others. The text is kept in this browser, the changes are dropped (carried
      // into the next session they would be refused again) and the note starts over.
      if (err.status === 413) {
        keepCopy(joined.note, { slug: joined.slug, text: muya.getMarkdown(), tooLarge: true });
        inflight = null;
        queue = [];
        stopped = true;
        onReset();
        return false;
      }
    }
    return Boolean(inflight && inflight.id !== sent);
  }

  function sync() {
    if (!running) {
      running = tick()
        .then((unsent) => unsent, () => false)
        .then((unsent) => {
          running = null;
          schedule(unsent ? 0 : undefined);
        });
    }
    return running;
  }

  function schedule(ms) {
    if (stopped || timer) return;
    const busy = pending() || Date.now() < othersActiveUntil;
    timer = setTimeout(() => {
      timer = null;
      sync();
    }, ms ?? (busy ? FAST_MS : CALM_MS));
  }

  // Whoever is connected saves, guests included; the server keeps only the newest seq.
  function autosave() {
    if (stopped || readOnly || saveTimer || saving || !(seq > savedSeq) || pending()) return;
    saveTimer = setTimeout(async () => {
      await nextFrame(); // Muya reports the last keystroke one frame late
      saveTimer = null;
      flush();
      if (stopped || saving || !(seq > savedSeq)) return;
      if (pending()) return autosave(); // typing: again once the change is through
      save().catch(onError);
    }, AUTOSAVE_MS + Math.random() * AUTOSAVE_JITTER_MS);
  }

  /** Wait until all own changes are on the server, then write the shared text to the note. */
  function save() {
    if (!saving) saving = doSave().finally(() => { saving = null; });
    return saving;
  }
  async function doSave() {
    if (readOnly) return;
    flush();
    const deadline = Date.now() + 10_000;
    while (pending() && !stopped && Date.now() < deadline) {
      clearTimeout(timer);
      timer = null;
      await sync();
      // A network error returns at once; without a pause this would be ten seconds of requests.
      if (pending() && !stopped) await new Promise((resolve) => setTimeout(resolve, FAST_MS));
    }
    if (stopped) return;
    if (pending()) throw new Error("Muudatused ei jõudnud serverisse.");
    if (!(seq > savedSeq)) return report();
    // The text saved is the text up to this seq; an op arriving during the request is not in it.
    const at = seq;
    const r = await transport.post(url("save"), { body: muya.getMarkdown(), seq: at });
    if (stopped) return;
    if (r && r.reset) {
      stopped = true;
      onReset();
      return;
    }
    savedSeq = Math.max(savedSeq, at);
    report();
  }

  // Moving the caret without changing anything: send soon, so the others see it.
  listen(document, "selectionchange", () => {
    if (!stopped && muya.domNode.contains(document.activeElement) && !timer) schedule(FAST_MS);
  });
  listen(window, "resize", redraw);
  // A tab in the background for a minute saves and leaves (the session can end, and the note
  // is no longer busy for the server's writers); back in view it rejoins.
  listen(document, "visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      clearTimeout(hiddenTimer);
      hiddenTimer = setTimeout(() => {
        // Not saved (offline, say): the tab stays in the session with its changes.
        save().then(() => {
          if (stopped) return;
          stopped = left = true;
          clearTimeout(timer);
          clearTimeout(saveTimer);
          transport.beacon(url("leave"), { client });
        }, () => {});
      }, HIDDEN_LEAVE_MS);
    } else {
      clearTimeout(hiddenTimer);
      if (left) {
        left = false;
        onReset();
      }
    }
  });
  // The tab goes away: what the autosave (2–3 s later) would have written goes now, as a
  // beacon. An op still on its way travels with it; the server writes the text only if that op
  // gets the next place (a co-editor's op may have come first). More changes queued behind it
  // are in the text only: final, the session then ends, and the next editor starts from the
  // text (collab.save on the server). With no session any more, the text is kept here.
  listen(window, "pagehide", () => {
    try {
      flush();
    } catch {}
    // Unsent changes stay in this browser too: the save below may be refused (a co-editor's op
    // came first) or never arrive; the copy is dropped on the next open if it was saved.
    if (pending()) keepMine();
    if (stopped) return;
    if (!readOnly) {
      if (inflight) {
        transport.beacon(url("save"), { client, body: muya.getMarkdown(), seq: seq + 1, op: inflight.op, op_id: inflight.id, final: queue.length > 0 });
      } else if (seq > savedSeq) transport.beacon(url("save"), { body: muya.getMarkdown(), seq });
    }
    transport.beacon(url("leave"), { client });
  });

  // Ops after the base text (made by others before we joined).
  receive(joined.ops);
  report();
  schedule(0);

  /** Remove every listener and timer; no request. */
  function detach() {
    stopped = true;
    clearTimeout(timer);
    clearTimeout(saveTimer);
    clearTimeout(hiddenTimer);
    timer = saveTimer = null;
    for (const off of undo.splice(0)) off();
    if (cursorLayer) cursorLayer.replaceChildren();
  }

  function leave() {
    const was = stopped;
    detach();
    if (!was) transport.beacon(url("leave"), { client });
  }

  // --- Edits from this tab's own tools (inline AI, task ticks) ------------------------------
  // Each is its own undo step: Muya would merge it with typing from the second before.
  const history = muya.editor.history;

  /** The current block path for one captured by selection(): moved by every op since. */
  function currentPath(path) {
    const key = JSON.stringify(path);
    const cap = captures.get(key);
    if (!cap) return { path, text: null };
    if (cap.at < logStart) return { path: null, text: null }; // too long ago to tell
    let p = path;
    for (const op of log.slice(cap.at - logStart)) {
      p = T.transformPosition(p, op);
      if (!p) break;
    }
    return { path: p, text: cap.text };
  }

  /** The caret's block: {path, text, start, end}, or null outside the editor or across blocks. */
  function selection() {
    flush();
    const sel = muya.editor.selection.getSelection();
    if (!sel || !sel.anchorBlock || !sel.isSelectionInSameBlock || !muya.domNode.contains(sel.anchorBlock.domNode)) return null;
    const path = sel.anchorPath;
    const text = sel.anchorBlock.text;
    captures.set(JSON.stringify(path), { at: logStart + log.length, text });
    if (captures.size > 50) captures.delete(captures.keys().next().value);
    return { path, text, start: Math.min(sel.anchor.offset, sel.focus.offset), end: Math.max(sel.anchor.offset, sel.focus.offset) };
  }

  /** Replace the text of one block (a captured path follows the ops since). An ordinary op: the
   *  others see it, Ctrl+Z undoes it. False when the block is gone or its text has changed. */
  function applyBlockEdit(path, text) {
    if (stopped || readOnly) return false;
    flush();
    const now = currentPath(path);
    const block = now.path && queryBlock(muya, now.path);
    if (!block || !block.isContent() || (now.text !== null && block.text !== now.text)) return false;
    const sel = muya.editor.selection.getSelection();
    history.cutoff();
    block.text = text;
    if (sel && sel.anchorBlock === block) block.setCursor(text.length, text.length, true);
    else block.update();
    flush();
    history.cutoff();
    return true;
  }

  /** Tick or untick the task-list item holding `path` by editing its `checked`, not the text. */
  function toggleTask(path, checked) {
    if (stopped || readOnly) return false;
    flush();
    const now = currentPath(path).path;
    if (!now) return false;
    const state = muya.editor.jsonState.getState();
    let node = state;
    let itemPath = null;
    let item = null;
    for (let i = 0; i < now.length && node != null; i++) {
      node = node[now[i]];
      if (node && node.name === "task-list-item") {
        itemPath = now.slice(0, i + 1);
        item = node;
      }
    }
    if (!item) return false;
    if (item.meta.checked !== checked) {
      history.cutoff();
      applyOp(muya, [...itemPath, "meta", "checked", { r: true, i: checked }], "user");
      history.cutoff();
    }
    return true;
  }

  /** New Markdown for the whole note, sent as ordinary ops: only the top-level blocks that
   *  changed are replaced, so others' simultaneous edits elsewhere survive. */
  function replaceMarkdown(markdown) {
    if (stopped || readOnly) return;
    flush();
    const state = muya.editor.jsonState;
    const prev = state.getState();
    state.setContent(markdown);
    const next = state.getState();
    state.setState(prev);
    let start = 0;
    while (start < prev.length && start < next.length && same(prev[start], next[start])) start++;
    let end = 0;
    while (end < prev.length - start && end < next.length - start && same(prev[prev.length - 1 - end], next[next.length - 1 - end])) end++;
    let op = null;
    for (let i = prev.length - 1 - end; i >= start; i--) op = T.compose(op, json1.removeOp([i], prev[i]));
    for (let i = start; i < next.length - end; i++) op = T.compose(op, json1.insertOp([i], next[i]));
    if (!op) return;
    history.cutoff();
    applyOp(muya, op, "user");
    history.cutoff();
  }

  // The Markdown of a document state (Muya renders it from its own state only).
  function markdownOf(doc) {
    if (!doc) return null;
    const js = muya.editor.jsonState;
    const now = js._state;
    try {
      js.setState(doc);
      return js.getMarkdown();
    } finally {
      js.setState(now);
    }
  }

  function keepMine() {
    try {
      flush();
    } catch {}
    const rescued = rescue();
    keepCopy(joined.note, { slug: joined.slug, text: muya.getMarkdown(), baseText: markdownOf(serverDoc), ...(rescued || {}), tab: client, opId: inflight && inflight.id });
  }

  /** Own changes the server never took, with the document they apply to; null when none.
   *  For the next session of the same note (carry), after this one ended. */
  function rescue() {
    try {
      flush();
    } catch {}
    if (!pending()) return null;
    return { doc: serverDoc, ops: [inflight && inflight.op, ...queue].filter(Boolean), text: muya.getMarkdown() };
  }

  /** Put rescued changes on top of this session's text: transformed through whatever the note
   *  went through meanwhile, sent as ordinary ops. Throws when they cannot be (no base doc). */
  function carry(rescued) {
    if (!rescued.doc) throw new Error("rescued changes have no base");
    flush();
    let theirs = diffOps(rescued.doc, muya.editor.jsonState.getState());
    history.cutoff();
    for (let op of rescued.ops) {
      theirs = theirs.map((w) => {
        if (!op) return w;
        const mine = transform(op, w, "right");
        const moved = transform(w, op, "left");
        op = mine;
        return moved;
      }).filter(Boolean);
      if (op) applyOp(muya, op, "user");
    }
    history.cutoff();
  }

  return {
    save,
    leave,
    detach,
    rescue,
    carry,
    /** Keep the unsent changes in this browser (their session is gone or going). */
    keepCopy: keepMine,
    get stopped() {
      return stopped;
    },
    selection,
    applyBlockEdit,
    toggleTask,
    replaceMarkdown,
    getMarkdown() {
      flush();
      return muya.getMarkdown();
    },
    get unsaved() {
      return unsaved();
    },
    get seq() {
      return seq;
    },
    get pending() {
      return pending();
    },
  };
}
