// The Python cells' code editor (docs/TAIENDUS-PLAN.md): a CodeMirror editor laid over each visible
// cell of the note. The Muya block underneath keeps the text (Markdown, git, co-editing): every
// change here goes into it as one text edit (setCodeText), and a co-editor's change comes back by
// its common prefix and suffix. Completion, signatures, docs and errors come from the taiendus
// container through /api/arvuta/taienda; column names after t[" from the note's own tables.
// Loaded only on a desktop note page that has cells.
import { api } from "/static/app.js";
import {
  acceptCompletion, Annotation, Transaction, EditorState, EditorView, HighlightStyle, StateEffect, StateField, autocompletion, bracketMatching,
  closeBrackets, closeBracketsKeymap, completionKeymap, defaultKeymap, history, historyKeymap, indentOnInput,
  indentUnit, indentWithTab, keymap, linter, python, showTooltip, startCompletion, syntaxHighlighting, tags,
  toggleComment,
} from "/static/vendor/codemirror.js";
import { theirChange } from "/static/arvuta/yhenda.js";

const remote = Annotation.define(); // a change that came from the Muya block, not typed here
const MIN_WORD = 2;

const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--cm-keyword)", fontWeight: "600" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--cm-string)" },
  { tag: tags.comment, color: "var(--cm-comment)", fontStyle: "italic" },
  { tag: [tags.number, tags.bool, tags.null], color: "var(--cm-number)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "var(--cm-function)" },
  { tag: [tags.definition(tags.variableName), tags.className], color: "var(--cm-definition)" },
  { tag: tags.operator, color: "var(--ink-2)" },
]);

const theme = EditorView.theme({
  "&": { fontSize: "0.9rem", background: "var(--paper-2)", borderRadius: "var(--r)" },
  "&.cm-focused": { outline: "2px solid var(--slate)" },
  ".cm-content": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", padding: "0.5rem 0", caretColor: "var(--ink)" },
  ".cm-line": { padding: "0 0.75rem" },
  ".cm-tooltip": { background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)", borderRadius: "var(--r)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { background: "var(--paper-2)", color: "var(--ink)" },
  ".cm-completionDetail": { color: "var(--ink-2)", fontStyle: "normal", marginLeft: "0.6rem" },
  ".cm-completionInfo": { maxWidth: "28rem", whiteSpace: "pre-wrap", fontSize: "0.8rem" },
  ".cm-signature": { padding: "0.3rem 0.5rem", fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", maxWidth: "36rem" },
  ".cm-signature b": { color: "var(--slate)" },
  ".cm-diagnostic-error": { borderLeftColor: "var(--danger)" },
});

/** One editor for one cell. ctx: {editor (the note's handle), index() (the cell's top-level block
 *  index now), before() (the code of the cells before it), tables() (column names), keys}. */
export function cellEditor(parent, text, ctx) {
  let synced = text;
  const ask = (body) => api("/api/arvuta/taienda", { method: "POST", body });
  const where = (state) => {
    const pos = state.selection.main.head;
    const line = state.doc.lineAt(pos);
    return { line: line.number, col: pos - line.from };
  };

  async function complete(context) {
    const before = context.state.sliceDoc(Math.max(0, context.pos - 200), context.pos);
    const column = before.match(/\[\s*(["'])([^"'\]]*)$/);
    if (column) {
      const names = ctx.tables().filter((n) => n.toLowerCase().startsWith(column[2].toLowerCase()));
      if (!names.length) return null;
      return { from: context.pos - column[2].length, options: names.map((n) => ({ label: n, type: "text", detail: "veerg" })) };
    }
    const word = context.matchBefore(/\w*/);
    const afterDot = /\.\w*$/.test(before);
    if (!context.explicit && !afterDot && word.to - word.from < MIN_WORD) return null;
    if (/^\s*#/.test(context.state.doc.lineAt(context.pos).text)) return null; // in a comment
    const at = where(context.state);
    const out = await ask({ kind: "complete", before: ctx.before(), code: context.state.doc.toString(), ...at });
    if (context.aborted || !out.items) return null;
    const kinds = { function: "function", class: "class", module: "namespace", keyword: "keyword", instance: "variable", statement: "variable", param: "variable", property: "property" };
    return {
      from: word.from,
      validFor: /^\w*$/,
      options: out.items.map((item) => ({
        label: item.label,
        type: kinds[item.type] || "variable",
        detail: item.detail,
        info: async () => {
          const d = await ask({ kind: "doc", before: ctx.before(), code: context.state.doc.toString(), ...at, name: item.label });
          if (!d.doc) return null;
          return Object.assign(document.createElement("div"), { textContent: d.doc });
        },
      })),
    };
  }

  // The signature of the call the caret is in, as a tooltip above it.
  const setSignature = StateEffect.define();
  const signature = StateField.define({
    create: () => null,
    update(value, tr) {
      for (const e of tr.effects) if (e.is(setSignature)) return e.value;
      return value && tr.docChanged ? { ...value, pos: tr.changes.mapPos(value.pos) } : value;
    },
    provide: (f) => showTooltip.from(f, (v) => v && {
      pos: v.pos,
      above: true,
      create: () => {
        const dom = document.createElement("div");
        dom.className = "cm-signature";
        const params = v.signature.params;
        const active = params[v.signature.active];
        // The parameter being typed in bold; all of it is text, never HTML.
        const label = v.signature.label;
        const at = active ? label.indexOf(active, label.indexOf("(")) : -1;
        if (at >= 0) dom.append(label.slice(0, at), Object.assign(document.createElement("b"), { textContent: active }), label.slice(at + active.length));
        else dom.textContent = label;
        return { dom };
      },
    }),
  });
  let signatureTimer = null;
  const inCall = (state) => {
    const line = state.doc.lineAt(state.selection.main.head);
    const text = line.text.slice(0, state.selection.main.head - line.from).replace(/(["']).*?\1/g, "");
    let depth = 0;
    for (const ch of text) depth += ch === "(" ? 1 : ch === ")" ? -1 : 0;
    return depth > 0;
  };
  const signatures = EditorView.updateListener.of((u) => {
    if (!u.docChanged && !u.selectionSet) return;
    clearTimeout(signatureTimer);
    if (!inCall(u.state)) {
      if (u.state.field(signature)) u.view.dispatch({ effects: setSignature.of(null) });
      return;
    }
    signatureTimer = setTimeout(async () => {
      const state = u.view.state;
      const out = await ask({ kind: "signature", before: ctx.before(), code: state.doc.toString(), ...where(state) }).catch(() => ({}));
      if (u.view.state !== state && u.view.state.doc !== state.doc) return;
      u.view.dispatch({ effects: setSignature.of(out.signature ? { pos: state.selection.main.head, signature: out.signature } : null) });
    }, 150);
  });

  const errors = linter(async (view) => {
    const doc = view.state.doc;
    const out = await ask({ kind: "lint", before: ctx.before(), code: doc.toString(), line: 1, col: 0 }).catch(() => ({}));
    if (view.state.doc !== doc) return [];
    return (out.diagnostics || []).filter((d) => d.line >= 1 && d.line <= doc.lines).map((d) => {
      const line = doc.line(d.line);
      const from = Math.min(line.to, line.from + d.col);
      const word = line.text.slice(from - line.from).match(/^\w+/);
      return { from, to: word ? from + word[0].length : line.to, severity: d.severity, message: d.message };
    });
  }, { delay: 600 });

  const notebookKeys = keymap.of([
    { key: "Shift-Enter", run: () => (ctx.keys.run(true), true) },
    { key: "Mod-Enter", run: () => (ctx.keys.run(false), true) },
    { key: "Escape", run: (view) => (view.contentDOM.blur(), true) },
    { key: "ArrowUp", run: (view) => view.state.doc.lineAt(view.state.selection.main.head).number === 1 && ctx.keys.leave(-1) },
    { key: "ArrowDown", run: (view) => view.state.doc.lineAt(view.state.selection.main.head).number === view.state.doc.lines && ctx.keys.leave(1) },
    { key: "Mod-/", run: toggleComment },
  ]);

  // Typed here: into the Muya block at once. Refused (the block changed under us): the next
  // pull merges the other side's change, and this text goes in after it.
  const push = EditorView.updateListener.of((u) => {
    if (!u.docChanged || u.transactions.some((t) => t.annotation(remote))) return;
    const text = u.state.doc.toString();
    if (ctx.editor.setCodeText(ctx.index(), text, synced)) synced = text;
  });

  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: text,
      extensions: [
        notebookKeys,
        history(),
        indentUnit.of("    "),
        EditorState.tabSize.of(4),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        python(),
        syntaxHighlighting(highlight),
        autocompletion({ override: [complete], maxRenderedOptions: 50, activateOnTyping: true }),
        signature,
        signatures,
        errors,
        // Tab takes the chosen suggestion when the list is open, else indents.
        keymap.of([{ key: "Tab", run: acceptCompletion }, ...closeBracketsKeymap, ...completionKeymap, indentWithTab, ...historyKeymap, ...defaultKeymap]),
        EditorView.lineWrapping,
        theme,
        push,
      ],
    }),
  });

  return {
    view,
    /** The Muya block's text now. When it is not what this editor last synced, a co-editor changed
     *  it: their change (base → text) goes into this editor, around any of this editor's own
     *  change not yet in the block (base → mine; a push refused because the block had just
     *  changed), and the merged text goes back into the block. */
    pull(text) {
      if (text === synced) return;
      const base = synced;
      const mine = view.state.doc.toString();
      synced = text;
      if (mine === text) return;
      const change = theirChange(base, mine, text);
      // Not in this editor's undo history: Ctrl+Z here undoes only what was typed here.
      if (change) view.dispatch({ changes: change, annotations: [remote.of(true), Transaction.addToHistory.of(false)] });
      const merged = view.state.doc.toString();
      if (merged !== text && ctx.editor.setCodeText(ctx.index(), merged, text)) synced = merged;
    },
    focus(atEnd) {
      view.focus();
      view.dispatch({ selection: { anchor: atEnd ? view.state.doc.length : 0 } });
    },
    focusAt(pos) {
      view.focus();
      view.dispatch({ selection: { anchor: Math.min(pos, view.state.doc.length) } });
    },
    complete: () => startCompletion(view),
    destroy: () => view.destroy(),
  };
}
