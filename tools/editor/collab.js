// Live co-editing: several editors change the same note at once. Ported from the Seljakotirobot
// site (tools/koostoo.js).
//
// Muya expresses every change as an ot-json1 operation (json-change) and can apply someone
// else's (editor.updateContents). The server (app/collab.py) only sequences operations; the
// transforming happens here, after the classic OT client: at most one operation is on its way
// to the server (inflight), changes made meanwhile collect in a buffer, and every operation from
// the server is transformed against both before it is applied. On a tie the operation earlier in
// the server's order is "left" (its text comes first).
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
      const fallback = r.status === 401 || r.status === 403 ? "Sisselogimine aegus." : `Viga ${r.status}.`;
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
  const joined = await transport.post("/api/collab/join", { slug, client });
  return { ...joined, client };
}

/** Muya internals the OT client depends on, patched once per editor:
 *  - Muya batches the user's edits and applies them to its state one animation frame later. A
 *    remote op applied in between would land on a state that lacks them (and the batch would
 *    then be sent relative to the wrong state), so the batch can be flushed on demand.
 *  - Its history transforms the undo stack against remote ops with the throwing transform.
 *  - It cannot apply a checkbox tick that came as an op (below). */
export function prepare(muya) {
  const js = muya.editor.jsonState;
  if (js.flushNow) return js.flushNow;
  let frame = 0;
  const flush = () => {
    cancelAnimationFrame(frame);
    js._isGoing = false;
    if (!js._operationCache.length) return;
    const op = js._operationCache.reduce(T.compose);
    js._operationCache = [];
    const prevDoc = js.getState();
    js.apply(op);
    muya.eventCenter.emit("json-change", { op, source: "user", prevDoc, doc: js.getState() });
  };
  js._emitStateChange = () => {
    if (js._isGoing) return;
    js._isGoing = true;
    frame = requestAnimationFrame(flush);
  };
  js.flushNow = flush;

  const history = muya.editor.history;
  const transformStack = (stack, op) => {
    for (let i = stack.length - 1; i >= 0; i--) {
      const own = stack[i].operation;
      stack[i].operation = transform(own, op, "left");
      op = transform(op, own, "right");
      if (!stack[i].operation || !stack[i].operation.length) stack.splice(i, 1);
    }
  };
  history.transform = (op) => {
    transformStack(history._stack.undo, op);
    transformStack(history._stack.redo, op);
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
  if (!block || !block.isContent()) return;
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
  let buffer = null; // changes not sent yet
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

  const unsaved = () => Boolean(inflight || buffer) || seq > savedSeq;
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

  const onChange = ({ op, source }) => {
    if (!op) return;
    log.push(op);
    if (log.length > LOG_KEEP) {
      log.shift();
      logStart++;
    }
    // Own changes (Ctrl+Z too) go to the others; remote ones were applied here, not sent back.
    if (applying || source !== "user" || readOnly || stopped) return;
    if (!inflight) inflight = { op, id: randomId() };
    else buffer = buffer ? T.compose(buffer, op) : op;
    report();
    redraw(); // own text moved: the others' carets move with it
    clearTimeout(timer);
    timer = null;
    schedule(30);
  };
  muya.on("json-change", onChange);
  undo.push(() => muya.off("json-change", onChange));

  function applyRemote(op) {
    applying = true;
    try {
      keepCaret(muya, () => {
        muya.editor.updateContents(op, null, "api");
        return op;
      });
    } finally {
      applying = false;
    }
  }

  // A remote op that throws leaves this editor's document in doubt. The session is closed for
  // everyone (the server does it on this report) and all editors rejoin from the saved text,
  // instead of carrying on with documents that may have drifted apart.
  function fail(err) {
    console.error("collab: remote op failed", err);
    stopped = true;
    onError(err);
    Promise.resolve(transport.post(url("leave"), { client, error: String(err && err.stack || err).slice(0, 500) }))
      .catch(() => {})
      .then(() => onReset());
  }

  function receive(ops) {
    flush();
    for (const o of ops) {
      if (stopped) return;
      if (o.seq !== seq + 1) continue; // seen already
      seq = o.seq;
      if (inflight && o.client === client && o.op_id === inflight.id) {
        // Our op is in the server's order: the next one goes.
        inflight = buffer ? { op: buffer, id: randomId() } : null;
        buffer = null;
        continue;
      }
      let op = o.op;
      try {
        if (inflight) {
          const theirs = transform(op, inflight.op, "left");
          inflight.op = transform(inflight.op, op, "right");
          op = theirs;
        }
        if (buffer) {
          const theirs = transform(op, buffer, "left");
          buffer = transform(buffer, op, "right");
          op = theirs;
        }
        // The server never took inflight (someone else's op came first), and now it does
        // nothing: drop it rather than wait for an ack that cannot come.
        if (inflight && !inflight.op) {
          inflight = buffer ? { op: buffer, id: randomId() } : null;
          buffer = null;
        }
        if (op) applyRemote(op);
      } catch (err) {
        fail(err);
        return;
      }
      othersActiveUntil = Date.now() + 5000;
    }
  }

  async function tick() {
    if (stopped) return;
    const body = { client, seen: seq, cursor: readOnly ? null : ownCursor() };
    if (inflight) Object.assign(body, { op: inflight.op, op_id: inflight.id, base: seq });
    try {
      const r = await transport.post(url("sync"), body);
      if (stopped) return;
      if (r.reset) {
        stopped = true;
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
      if (stopped) return;
      onError(err);
      // Too large for the server: it can never be sent, and without it this document no longer
      // matches the others. Start over from the server's text.
      if (err.status === 413) {
        stopped = true;
        onReset();
        return;
      }
    }
  }

  function sync() {
    if (!running) running = tick().finally(() => {
      running = null;
      schedule();
    });
    return running;
  }

  function schedule(ms) {
    if (stopped || timer) return;
    const busy = inflight || buffer || Date.now() < othersActiveUntil;
    timer = setTimeout(() => {
      timer = null;
      sync();
    }, ms ?? (busy ? FAST_MS : CALM_MS));
  }

  // Whoever is connected saves, guests included; the server keeps only the newest seq.
  function autosave() {
    if (stopped || readOnly || saveTimer || saving || !(seq > savedSeq) || inflight || buffer) return;
    saveTimer = setTimeout(async () => {
      await nextFrame(); // Muya reports the last keystroke one frame late
      saveTimer = null;
      flush();
      if (stopped || saving || !(seq > savedSeq)) return;
      if (inflight || buffer) return autosave(); // typing: again once the change is through
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
    while ((inflight || buffer) && !stopped && Date.now() < deadline) {
      clearTimeout(timer);
      timer = null;
      await sync();
      // A network error returns at once; without a pause this would be ten seconds of requests.
      if ((inflight || buffer) && !stopped) await new Promise((resolve) => setTimeout(resolve, FAST_MS));
    }
    if (stopped) return;
    if (inflight || buffer) throw new Error("Muudatused ei jõudnud serverisse.");
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
        save().catch(() => {}).finally(() => {
          if (stopped) return;
          stopped = left = true;
          clearTimeout(timer);
          clearTimeout(saveTimer);
          transport.beacon(url("leave"), { client });
        });
      }, HIDDEN_LEAVE_MS);
    } else {
      clearTimeout(hiddenTimer);
      if (left) {
        left = false;
        onReset();
      }
    }
  });
  listen(window, "pagehide", () => {
    if (!stopped) transport.beacon(url("leave"), { client });
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
      muya.editor.updateContents(json1.replaceOp([...itemPath, "meta", "checked"], item.meta.checked, checked), null, "user");
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
    const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
    let start = 0;
    while (start < prev.length && start < next.length && same(prev[start], next[start])) start++;
    let end = 0;
    while (end < prev.length - start && end < next.length - start && same(prev[prev.length - 1 - end], next[next.length - 1 - end])) end++;
    let op = null;
    for (let i = prev.length - 1 - end; i >= start; i--) op = T.compose(op, json1.removeOp([i], prev[i]));
    for (let i = start; i < next.length - end; i++) op = T.compose(op, json1.insertOp([i], next[i]));
    if (!op) return;
    history.cutoff();
    muya.editor.updateContents(op, null, "user");
    history.cutoff();
  }

  return {
    save,
    leave,
    detach,
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
      return Boolean(inflight || buffer);
    },
  };
}
