// Inline AI in the note editor (docs/PLAN-OVERVIEW.md §9). Select text → ✦ (next to the
// selection, or in the note's metadata row) or Ctrl+J → a short instruction → streamed preview →
// Asenda / Lisa alla / Loobu. With no selection, and from the "@" menu's "AI" item, new text is
// written at the caret. Changes go through the editor handle (docs/CONTRACTS.md §6) as ordinary
// ops, so co-editors see them and Ctrl+Z undoes them; a stale target is refused, never merged.
// Loaded by site/ai/index.js on "editor:ready"; tools/build-editor.mjs copies it to site/muya.
import { toast } from "/static/app.js";
import { streamAi } from "/static/ai/sse.js";

const CHIPS = ["Lühemaks", "Pikemaks", "Paranda keel", "Tabeliks", "Tõlgi inglise keelde"];
const STALE = "Tekst muutus vahepeal, proovi uuesti";
const installed = new WeakMap(); // muya -> inline AI state

// Deep equality regardless of key order.
function same(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => same(a[k], b[k]));
}

// The editor handle does not expose Muya; every Muya element points back at its block
// (__MUYA_BLOCK__) and every block at its Muya. The first one in document order is the page.
function findMuya(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) if (node.__MUYA_BLOCK__) return node.__MUYA_BLOCK__.muya;
  return null;
}

function button(text, cls, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  if (cls) b.className = cls;
  b.addEventListener("mousedown", (e) => e.preventDefault()); // clicks never steal the editor's selection
  b.addEventListener("click", onClick);
  return b;
}

/** Inline AI for the editor in `root` (the element openNote was given). Again for the same editor
 *  (another note opened in it): only the handle and note change. Returns { open() }. */
export function attachInlineAi(root, { editor, note }) {
  const muya = findMuya(root);
  if (!muya) return null;
  let state = installed.get(muya);
  if (!state) {
    state = install(muya);
    installed.set(muya, state);
  }
  Object.assign(state, { editor, slug: note.slug });
  return { open: () => state.open() };
}

function install(muya) {
  const state = { editor: null, slug: null, open };
  const jsonState = muya.editor.jsonState;
  // Markdown of some top-level blocks: Muya's own serializer, fed a slice of its state.
  const markdownOf = (blocks) => jsonState.getMarkdown.call({ getState: () => blocks });
  let box = null; // the open prompt

  // --- What the AI works on, captured when the prompt opens ----------------------------------
  function capture() {
    const sel = muya.editor.selection.getSelection();
    if (!sel || !sel.anchorBlock || !muya.domNode.contains(sel.anchorBlock.domNode)) return null;
    const one = state.editor.selection(); // also remembers the path, so applyBlockEdit can follow it
    const blocks = jsonState.getState();
    if (one) {
      const top = one.path[0];
      return { ...one, mode: one.start < one.end ? "block" : "caret", first: top, captured: [blocks[top]], selected: one.text.slice(one.start, one.end) };
    }
    // Across blocks: the top-level blocks it touches are rewritten whole.
    const first = Math.min(sel.anchorPath[0], sel.focusPath[0]);
    const captured = blocks.slice(first, Math.max(sel.anchorPath[0], sel.focusPath[0]) + 1);
    return { mode: "range", first, captured, selected: markdownOf(captured) };
  }

  /** Where the captured blocks are now (co-editors may have added blocks above), or -1. */
  function locate(blocks, captured, hint) {
    const fits = (i) => captured.every((b, k) => same(blocks[i + k], b));
    if (hint + captured.length <= blocks.length && fits(hint)) return hint;
    for (let i = 0; i + captured.length <= blocks.length; i++) if (fits(i)) return i;
    return -1;
  }

  /** Put `result` in. how: "replace" | "below" | "insert". False when the target changed meanwhile. */
  function apply(t, result, how) {
    const editor = state.editor;
    const text = result.trim();
    const oneLine = !text.includes("\n");
    const topParagraph = t.path && t.path.length === 2 && t.captured[0]?.name === "paragraph";
    if (how !== "below" && t.mode !== "range" && (oneLine || (t.mode === "block" && !topParagraph))) {
      const end = t.mode === "block" ? t.end : t.start;
      return editor.applyBlockEdit(t.path, t.text.slice(0, t.start) + text + t.text.slice(end));
    }
    // Block-level text (a table, a list, paragraphs) or several blocks: the affected top-level
    // blocks are swapped through replaceMarkdown, which leaves every other block untouched.
    editor.getMarkdown(); // flushes pending typing into the state
    const blocks = jsonState.getState();
    const at = locate(blocks, t.captured, t.first);
    if (at < 0) return false;
    let middle;
    if (how === "below" || (t.mode === "caret" && !topParagraph)) middle = [markdownOf(t.captured), text];
    else if (t.mode === "range") middle = [text];
    else {
      // Inside a top-level paragraph: it splits around the new blocks. Its current text must be
      // the captured one (locate checked that).
      const end = t.mode === "block" ? t.end : t.start;
      middle = [t.text.slice(0, t.start), text, t.text.slice(end)];
    }
    const parts = [markdownOf(blocks.slice(0, at)), ...middle, markdownOf(blocks.slice(at + t.captured.length))];
    editor.replaceMarkdown(`${parts.map((s) => s.replace(/\s+$/, "")).filter((s) => s.trim()).join("\n\n")}\n`);
    return true;
  }

  // --- The prompt box ---------------------------------------------------------------------------
  function anchorRect() {
    const sel = document.getSelection();
    const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
    const rect = range && muya.domNode.contains(range.endContainer) ? range.getBoundingClientRect() : null;
    if (rect && (rect.width || rect.height)) return rect;
    const block = muya.editor.selection.getSelection()?.anchorBlock;
    return (block?.domNode || muya.domNode).getBoundingClientRect();
  }

  function open() {
    if (!state.editor) return;
    if (box) {
      box.input.focus();
      return;
    }
    const target = capture();
    if (!target) {
      toast("Vali märkmes tekst või vii kursor sinna, kuhu kirjutada.", "error");
      return;
    }
    box = makeBox(target, anchorRect());
  }

  function close() {
    if (!box) return;
    box.abort?.abort();
    box.el.remove();
    box = null;
    muya.domNode.focus({ preventScroll: true }); // Ctrl+Z right after works on the editor
  }

  function makeBox(target, rect) {
    const rewriting = target.mode !== "caret";
    const el = document.createElement("div");
    el.className = "ai-inline";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "AI");
    const form = document.createElement("form");
    const input = Object.assign(document.createElement("input"), {
      type: "text",
      placeholder: rewriting ? "Mida valitud tekstiga teha?" : "Mida siia kirjutada?",
    });
    input.setAttribute("aria-label", "Juhis AI-le");
    const go = Object.assign(document.createElement("button"), { type: "submit", textContent: "Saada", className: "primary" });
    form.append(input, go);
    const chips = document.createElement("div");
    chips.className = "ai-chips";
    if (rewriting) chips.append(...CHIPS.map((c) => button(c, "ai-chip", () => run(c))));
    const preview = document.createElement("div");
    preview.className = "ai-preview";
    preview.hidden = true;
    const actions = document.createElement("div");
    actions.className = "ai-actions";
    const cancel = button("Loobu", "ghost", close);
    actions.append(cancel);
    el.append(form, chips, preview, actions);
    document.body.append(el);
    const left = Math.max(8, Math.min(rect.left, document.documentElement.clientWidth - el.offsetWidth - 8));
    el.style.left = `${left + window.scrollX}px`;
    el.style.top = `${rect.bottom + window.scrollY + 8}px`;
    const b = { el, input, abort: null };

    let result = "";
    async function run(instruction) {
      if (!instruction.trim() || b.abort) return;
      b.abort = new AbortController();
      form.hidden = true;
      chips.hidden = true;
      preview.hidden = false;
      preview.classList.remove("error");
      preview.classList.add("ai-streaming");
      preview.textContent = "";
      result = "";
      let failed = null;
      try {
        await streamAi("/api/ai/inline", { slug: state.slug, instruction, text: target.selected }, (ev) => {
          if (ev.type === "delta") {
            result += ev.text;
            preview.textContent = result;
          } else if (ev.type === "error") failed = ev.message;
        }, b.abort.signal);
      } catch (err) {
        if (err.name === "AbortError") return;
        failed = err.message;
      }
      preview.classList.remove("ai-streaming");
      if (failed || !result.trim()) {
        preview.classList.add("error");
        preview.textContent = failed || "AI ei andnud vastust.";
        return;
      }
      const done = (how) => () => {
        const ok = apply(target, result, how);
        close();
        if (!ok) toast(STALE, "error");
      };
      const choices = rewriting
        ? [button("Asenda", "primary", done("replace")), button("Lisa alla", "", done("below"))]
        : [button("Lisa", "primary", done("insert"))];
      actions.prepend(...choices);
      choices[0].focus();
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      run(input.value);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      }
    });
    input.focus();
    return b;
  }

  // --- Ways in --------------------------------------------------------------------------------
  // On the document, capturing: Muya cuts a selection that spans blocks on any other key press
  // (its clipboard keydown), so it must never see this one.
  document.addEventListener("keydown", (e) => {
    if (!muya.domNode.contains(e.target)) return;
    if (e.key === "Control" && muya.editor.selection.getSelection()?.isSelectionInSameBlock === false) {
      // Muya's cut test lists Alt, Meta and Shift as keys that do not type, but not Control: the
      // Ctrl of Ctrl+J would delete a selection spanning blocks before the J arrives.
      e.stopImmediatePropagation();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "j") {
      e.preventDefault();
      e.stopImmediatePropagation();
      open();
    }
  }, true);

  // ✦ next to a selection in the text.
  const spark = button("✦", "ai-spark", () => {
    spark.hidden = true;
    open();
  });
  spark.title = "AI valitud tekstiga (Ctrl+J)";
  spark.setAttribute("aria-label", "AI valitud tekstiga");
  spark.hidden = true;
  document.body.append(spark);
  document.addEventListener("selectionchange", () => {
    const sel = document.getSelection();
    const inside = sel && !sel.isCollapsed && muya.domNode.contains(sel.anchorNode) && muya.domNode.contains(sel.focusNode);
    if (!inside || box || !muya.domNode.isConnected) {
      spark.hidden = true;
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    spark.style.left = `${rect.right + window.scrollX + 4}px`;
    spark.style.top = `${rect.bottom + window.scrollY + 4}px`;
    spark.hidden = false;
  });

  // "@" menu item "ai" (added by the build): the "@ai" line empties and the prompt opens there.
  const quickInsert = muya._uiPlugins.quickInsert;
  const selectItem = quickInsert.selectItem.bind(quickInsert);
  quickInsert.selectItem = (item) => {
    if (item.label !== "ai") return selectItem(item);
    const block = quickInsert.block;
    quickInsert.hide();
    block.text = "";
    block.setCursor(0, 0, true);
    open();
  };

  return state;
}
