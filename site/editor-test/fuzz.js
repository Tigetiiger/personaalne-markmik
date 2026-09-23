// OT fuzz harness for tests/visual/editor-fuzz.spec.mjs: several editors on one page, each wired
// through a fake network with seeded random delays to a copy of the server's sequencer
// (app/collab.py). The spec drives edits; this page answers where to put the caret and whether
// everyone ended up with the same document. Served only with MARKMIK_TEST_PAGES=1.
const muyaUrl = document.querySelector('meta[name="muya"]').content;
const { mountEditor } = await import(`${muyaUrl}/editor.js`);
const { attachCollab, joinCollab } = await import(`${muyaUrl}/collab.js`);

function mulberry32(seed) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const clone = (x) => (x === undefined || x === null ? null : JSON.parse(JSON.stringify(x)));

/** The server's rules, as in app/collab.py: an op is taken only on top of the latest seq, a
 *  resent one only once; a save writes only a newer seq, never beyond the log. */
class Sequencer {
  constructor(base) {
    this.base = base;
    this.ops = [];
    this.savedSeq = 0;
    this.body = base;
    this.closed = false;
    this.errors = [];
  }

  handle(url, body) {
    if (url.endsWith("/join")) {
      return { session: 1, base: this.base, ops: this.ops, saved_seq: this.savedSeq, participants: [], cursors: [], rev: 1 };
    }
    if (this.closed) return { reset: true };
    if (url.endsWith("/sync")) {
      if (body.op != null && !this.ops.some((o) => o.client === body.client && o.op_id === body.op_id) && body.base === this.ops.length) {
        this.ops.push({ seq: this.ops.length + 1, op: body.op, client: body.client, op_id: body.op_id, author: body.client });
      }
      return { ops: this.ops.filter((o) => o.seq > body.seen), saved_seq: this.savedSeq, participants: [], cursors: [] };
    }
    if (url.endsWith("/save")) {
      const seq = Math.min(body.seq, this.ops.length);
      if (seq > this.savedSeq) {
        this.savedSeq = seq;
        this.body = body.body;
      }
      return null;
    }
    if (url.endsWith("/leave")) {
      if (body.error) {
        this.errors.push(body.error);
        this.closed = true;
      }
      return null;
    }
    throw new Error(`unknown url ${url}`);
  }
}

// maxDelay 0: no timers at all, a request is answered within the same task. A closed gate holds
// requests before the server sees them until it is opened.
function network(sequencer, rng, maxDelay, gate) {
  const pause = () => (maxDelay ? sleep(rng() * maxDelay) : null);
  const net = {
    async post(url, body) {
      const request = clone(body);
      await pause();
      if (gate.held) {
        gate.waiting++;
        await gate.held;
        gate.waiting--;
      }
      const response = clone(sequencer.handle(url, request));
      await pause();
      return response;
    },
    beacon(url, body) {
      net.post(url, body);
    },
  };
  return net;
}

const root = document.getElementById("test-root");
let sequencer = null;
let rng = null;
let delay = 0;
const editors = [];
const failures = [];

function makeGate() {
  const gate = { held: null, waiting: 0, release: null };
  gate.close = () => {
    gate.held = new Promise((resolve) => (gate.release = resolve));
  };
  gate.open = () => {
    const release = gate.release;
    gate.held = null;
    release();
  };
  return gate;
}

async function addEditor() {
  const gate = makeGate();
  const transport = network(sequencer, rng, delay, gate);
  const joined = await joinCollab("fuzz", transport);
  const host = document.createElement("div");
  host.className = "fuzz-editor";
  const box = document.createElement("section");
  box.append(host);
  root.append(box);
  const muya = mountEditor(host, { markdown: joined.base });
  const collab = attachCollab(muya, joined, {
    transport,
    onReset: () => failures.push(`editor ${editors.length}: reset`),
    onError: (err) => failures.push(`editor ${editors.length}: ${err.message}`),
  });
  editors.push({ muya, collab, gate });
  return editors.length - 1;
}

const contents = (muya) => [...muya.domNode.querySelectorAll("span.mu-content")].map((el) => el.__MUYA_BLOCK__).filter(Boolean);

window.fuzz = {
  async setup({ seed, base, count = 3, maxDelay = 80 }) {
    sequencer = new Sequencer(base);
    rng = mulberry32(seed);
    delay = maxDelay;
    for (let i = 0; i < count; i++) await addEditor();
    return editors.length;
  },

  /** Caret into editor i: the (r * count)-th content block (only table cells when `cells`),
   *  at "start", "end" or a fraction of its text. False when there is no such block. */
  place(i, r, where, { cells = false } = {}) {
    const { muya } = editors[i];
    const blocks = contents(muya).filter((b) => (cells ? b.blockName === "table.cell.content" : true));
    if (!blocks.length) return false;
    const block = blocks[Math.floor(r * blocks.length) % blocks.length];
    const len = block.text.length;
    const at = where === "start" ? 0 : where === "end" ? len : Math.round(where * len);
    muya.domNode.focus();
    block.setCursor(at, at, true);
    return true;
  },

  /** Click the (r * count)-th task checkbox of editor i. */
  tick(i, r) {
    const boxes = editors[i].muya.domNode.querySelectorAll(".mu-task-list-checkbox");
    if (!boxes.length) return false;
    boxes[Math.floor(r * boxes.length) % boxes.length].click();
    return true;
  },

  paste(i, text) {
    const data = new DataTransfer();
    data.setData("text/plain", text);
    editors[i].muya.domNode.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  },

  undo(i) {
    editors[i].muya.undo();
  },

  /** Let every editor send what it has and hear everything, then report. */
  async settle(timeoutMs = 15_000) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      for (const { collab } of editors) collab.getMarkdown(); // flushes Muya's pending batch
      const quiet = editors.every(({ collab }) => !collab.pending && collab.seq === sequencer.ops.length);
      if (quiet || Date.now() > deadline) break;
      await sleep(100);
    }
    return window.fuzz.report();
  },

  report() {
    return {
      markdown: editors.map(({ collab }) => collab.getMarkdown()),
      seqs: editors.map(({ collab }) => collab.seq),
      pending: editors.map(({ collab }) => collab.pending),
      ops: sequencer.ops.length,
      failures: [...failures, ...sequencer.errors],
    };
  },

  /** A new editor joining now must build the same document from base + ops. */
  async lateJoiner() {
    const i = await addEditor();
    return editors[i].collab.getMarkdown();
  },

  /** Editor 1 inserts at the start of the paragraph; editor 0 types at its end, and editor 0's
   *  held poll brings editor 1's op back before Muya has reported the keystroke (one frame). */
  async remoteOpBeforeNextFrame() {
    const until = async (check) => {
      while (!check()) await sleep(10);
    };
    const [a, b] = editors;
    const paragraph = (muya) => contents(muya).find((block) => block.text === "abc");
    a.gate.close();
    b.muya.domNode.focus();
    paragraph(b.muya).setCursor(0, 0, true);
    document.execCommand("insertText", false, "XY");
    await until(() => sequencer.ops.length === 1);
    await until(() => a.gate.waiting > 0);
    a.muya.domNode.focus();
    paragraph(a.muya).setCursor(3, 3, true);
    document.execCommand("insertText", false, "z");
    a.gate.open();
    // The answer comes through microtasks only: still this task, before any animation frame.
    for (let k = 0; k < 100 && a.collab.seq < 1; k++) await null;
    const landed = a.collab.seq === 1;
    return { landed, ...(await window.fuzz.settle()) };
  },

  async save(i) {
    await editors[i].collab.save();
    return { body: sequencer.body, savedSeq: sequencer.savedSeq, ops: sequencer.ops.length };
  },
};
document.body.dataset.editor = "ready";
