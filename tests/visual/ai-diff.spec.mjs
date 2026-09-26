// The review window's diff (site/ai/diff.js), run in Node: no page needed. Blocks, changes, the
// kept ones put back, and the three-way apply onto a note that changed meanwhile.
import { expect, test } from "@playwright/test";

import { MARK, apply, diff, joinBlocks, marked, splitBlocks, words } from "../../site/ai/diff.js";

const texts = (md) => splitBlocks(md).map((b) => b.text);
const all = (d) => d.changes.map(() => true);
const only = (d, ...ks) => d.changes.map((_, k) => ks.includes(k));
const shape = (d) => d.changes.map(({ from, to, nFrom, nTo }) => [from, to, nFrom, nTo]);

test("blocks: headings, paragraphs, fences, tables, quotes, each list item with what it holds", () => {
  const md = [
    "# Pealkiri", "Lõik üks", "rida kaks.", "", "```js", "a", "", "# pole pealkiri", "```", "", "- üks", "- kaks", "  - alam", "",
    "  jätk kahe all", "1. esimene", "2) teine", "", "| a | b |", "| --- | --- |", "| 1 | 2 |", "", "> tsitaat", "> - loend", "", "$$", "x", "", "y", "$$", "",
  ].join("\n");
  expect(splitBlocks(md).map((b) => [b.kind, b.text])).toEqual([
    ["heading", "# Pealkiri"],
    ["para", "Lõik üks\nrida kaks."],
    ["code", "```js\na\n\n# pole pealkiri\n```"],
    ["item", "- üks"],
    ["item", "- kaks\n  - alam\n\n  jätk kahe all"],
    ["item", "1. esimene"],
    ["item", "2) teine"],
    ["table", "| a | b |\n| --- | --- |\n| 1 | 2 |"],
    ["quote", "> tsitaat\n> - loend"],
    ["code", "$$\nx\n\ny\n$$"],
  ]);
  // A fence closes only with its own character, at least as long; unclosed, it runs to the end.
  expect(texts("````\n```\nx\n````\n\nlõik")).toEqual(["````\n```\nx\n````", "lõik"]);
  expect(texts("~~~\nx\n\ny")).toEqual(["~~~\nx\n\ny"]);
  // A list starting at 2 does not end a paragraph (CommonMark); a bullet does.
  expect(texts("Kell on\n2. korrusel\n- punkt")).toEqual(["Kell on\n2. korrusel", "- punkt"]);
  expect(splitBlocks("")).toEqual([]);
  expect(splitBlocks("\n\n  \n")).toEqual([]);
});

const ODD = [
  "", "a", "a\n", "a\n\n\n", "\n\n# T\ntekst\n  \n- a\n- b\n\n\n- c\n", "lõik\n# pealkiri\n- a\n- b", "a\r\nb\r\n\r\nc\r\n",
  "```\nx\n", "- ```\n# pole pealkiri\n```\n- b\n", "> q\n\n    kood\n\ntekst  \n", "# T\n\nlõik\n\n- a\n- b\n\n- c\n\n```\nx\n\ny\n```\n",
];

test("blocks join back into exactly the Markdown they came from", () => {
  for (const md of ODD) expect(joinBlocks(splitBlocks(md))).toBe(md);
  expect(joinBlocks(splitBlocks("\n\n  \n"))).toBe(""); // blank lines alone are no blocks
});

test("left out, every change gives the base back exactly; kept, the proposal", () => {
  for (const md of ODD) {
    for (const next of [`${md}\n\nlisatud\n`, `alguses\n\n${md}`]) {
      const d = diff(md, next);
      expect(apply(d, d.changes.map(() => false)).markdown).toBe(md);
      expect(apply(d, all(d)).markdown).toBe(next);
    }
  }
});

test("a block after another than before gets a blank line, unless an item follows an item", () => {
  let d = diff("A\n\n# H\nlõik\n", "A\n\nlõik\n");
  expect(apply(d, all(d)).markdown).toBe("A\n\nlõik\n"); // "A\nlõik" would be one paragraph
  d = diff("lõik\n- a\n- b\n", "- a\n- b\n");
  expect(apply(d, all(d)).markdown).toBe("- a\n- b\n");
  d = diff("- a\n\nlõik\n- b\n", "- a\n- b\n");
  expect(apply(d, all(d)).markdown).toBe("- a\n- b\n");
});

test("a fence in a list item holds lines that look like a heading, an item or a fence", () => {
  const md = "- a\n  ```\n# koodis, mitte pealkiri\n- koodis, mitte punkt\n~~~\n  ```\n- b\n\nlõik\n";
  expect(splitBlocks(md).map((b) => [b.kind, b.text])).toEqual([
    ["item", "- a\n  ```\n# koodis, mitte pealkiri\n- koodis, mitte punkt\n~~~\n  ```"],
    ["item", "- b"],
    ["para", "lõik"],
  ]);
  expect(joinBlocks(splitBlocks(md))).toBe(md);
  const next = md.replace("lõik", "lõik muudetud").replace("- b", "- b\n- c");
  const d = diff(md, next);
  expect(shape(d)).toEqual([[2, 3, 2, 4]]);
  expect(apply(d, [false]).markdown).toBe(md);
  expect(apply(d, [true]).markdown).toBe(next);
  // Opened on the item's own line, and never closed: the rest of the note is its code.
  expect(texts("- ```\n# x\n\nlõik\n")).toEqual(["- ```\n# x\n\nlõik\n"]);
});

test("identical text, and a difference only in trailing spaces or blank lines, is no change", () => {
  const md = "# T\n\nlõik\n\n- a\n- b\n";
  expect(diff(md, md).changes).toEqual([]);
  expect(diff(md, "# T  \n\n\nlõik   \n\n- a\n- b\n\n\n").changes).toEqual([]);
  expect(diff("", "").changes).toEqual([]);
  expect(apply(diff(md, md), [], md).markdown).toBe(md);
});

test("title edited: one change, the title word by word", () => {
  const d = diff("# Kool\n\nKontrolltöö reedel.\n", "# Kool 2026\n\nKontrolltöö reedel.\n");
  expect(shape(d)).toEqual([[0, 1, 0, 1]]);
  expect(d.changes[0].rows).toEqual([{ old: 0, new: 0 }]);
  expect(words("# Kool", "# Kool 2026")).toEqual([{ same: "# Kool" }, { del: "", ins: " 2026" }]);
  expect(apply(d, all(d)).markdown).toBe("# Kool 2026\n\nKontrolltöö reedel.\n");
  expect(apply(d, [false]).markdown).toBe("# Kool\n\nKontrolltöö reedel.\n");
});

test("a second title added under the first shows as an added heading, not a changed one", () => {
  const d = diff("# Kool\n\nTekst.\n", "# Kool\n\nTekst.\n\n# Kool\n\nUus.\n");
  expect(shape(d)).toEqual([[2, 2, 2, 4]]);
  expect(d.changes[0].rows).toEqual([{ new: 2 }, { new: 3 }]);
});

test("a block moved: removed in one place, added in another; each can be kept alone", () => {
  const base = "# T\n\nA\n\nB\n\nC\n";
  const next = "# T\n\nB\n\nC\n\nA\n";
  const d = diff(base, next);
  expect(shape(d)).toEqual([[1, 2, 1, 1], [4, 4, 3, 4]]);
  expect(apply(d, all(d)).markdown).toBe(next);
  expect(apply(d, only(d, 0)).markdown).toBe("# T\n\nB\n\nC\n");
  expect(apply(d, only(d, 1)).markdown).toBe("# T\n\nA\n\nB\n\nC\n\nA\n");
});

test("a list item inserted keeps the list tight; an ordered list renumbered is not a change", () => {
  let d = diff("- a\n- b\n\nlõik\n", "- a\n- x\n- b\n\nlõik\n");
  expect(shape(d)).toEqual([[1, 1, 1, 2]]);
  expect(apply(d, all(d)).markdown).toBe("- a\n- x\n- b\n\nlõik\n");
  d = diff("1. a\n2. b\n3. c\n", "1. a\n2. uus\n3. b\n4. c\n");
  expect(shape(d)).toEqual([[1, 1, 1, 2]]);
  // The editor numbers the items itself when it reads this back.
  expect(apply(d, all(d)).markdown).toBe("1. a\n2. uus\n2. b\n3. c\n");
  // An item removed from the middle: its neighbours stay one list.
  d = diff("- a\n- b\n- c\n", "- a\n- c\n");
  expect(apply(d, all(d)).markdown).toBe("- a\n- c\n");
  // The first block removed: what follows starts the note.
  d = diff("lõik\n\n- a\n- b\n", "- a\n- b\n");
  expect(apply(d, all(d)).markdown).toBe("- a\n- b\n");
});

test("a table cell changed: the table is one block, changed word by word", () => {
  const base = "| a | b |\n| --- | --- |\n| 1 | 2 |\n";
  const next = "| a | b |\n| --- | --- |\n| 1 | 3 |\n";
  const d = diff(base, next);
  expect(shape(d)).toEqual([[0, 1, 0, 1]]);
  const w = words(d.base[0].text, d.next[0].text);
  expect(w.filter((r) => "del" in r)).toEqual([{ del: "2", ins: "3" }]);
  expect(marked(w)).toBe(`| a | b |\n| --- | --- |\n| 1 | ${MARK.delStart}2${MARK.delEnd}${MARK.insStart}3${MARK.insEnd} |`);
});

test("a code fence with blank lines is one block", () => {
  const d = diff("```py\nx = 1\n\ny = 2\n```\n\nlõik\n", "```py\nx = 1\n\ny = 3\n```\n\nlõik\n");
  expect(shape(d)).toEqual([[0, 1, 0, 1]]);
  expect(d.changes[0].rows).toEqual([{ old: 0, new: 0 }]);
});

test("an empty note: everything is one added change", () => {
  const d = diff("", "# Uus\n\nTekst.\n");
  expect(shape(d)).toEqual([[0, 0, 0, 2]]);
  expect(apply(d, all(d)).markdown).toBe("# Uus\n\nTekst.\n");
  expect(apply(d, [false]).markdown).toBe("");
  expect(apply(d, all(d), "Keegi kirjutas vahepeal.\n").markdown).toBe("# Uus\n\nTekst.\n\nKeegi kirjutas vahepeal.\n");
});

test("adjacent changed blocks are one change; blocks of a kind pair up inside it", () => {
  const d = diff("# T\n\nA.\n\nB.\n\nC.\n", "# T\n\nA uus.\n\n## Vahe\n\nB uus.\n\nC.\n");
  expect(shape(d)).toEqual([[1, 3, 1, 4]]);
  expect(d.changes[0].rows).toEqual([{ old: 1, new: 1 }, { new: 2 }, { old: 2, new: 3 }]);
});

test("word diff: spaces between two changes join them; too different is no word diff", () => {
  expect(words("Kontrolltöö on reedel.", "Kontrolltöö on neljapäeval.")).toEqual([
    { same: "Kontrolltöö on " }, { del: "reedel", ins: "neljapäeval" }, { same: "." },
  ]);
  expect(words("üks kaks kolm neli viis", "üks KAKS KOLM neli viis")).toEqual([
    { same: "üks " }, { del: "kaks kolm", ins: "KAKS KOLM" }, { same: " neli viis" },
  ]);
  expect(words("Täiesti teine lause siin.", "Midagi muud hoopis.")).toBeNull();
  // Either side put back together is exactly the text it came from.
  const w = words("Tere **maailm**, kuidas läheb?", "Tere **ilm**, kuidas sul läheb?");
  expect(w.map((r) => r.same ?? r.del).join("")).toBe("Tere **maailm**, kuidas läheb?");
  expect(w.map((r) => r.same ?? r.ins).join("")).toBe("Tere **ilm**, kuidas sul läheb?");
});

test("the note changed during review: an untouched change goes in, one whose blocks changed is skipped", () => {
  const base = "# T\n\nEsimene lõik.\n\nKeskmine.\n\nTeine lõik.\n";
  const next = "# T\n\nEsimene lõik, muudetud.\n\nKeskmine.\n\nTeine lõik, muudetud.\n";
  const d = diff(base, next);
  expect(d.changes).toHaveLength(2);
  // Someone edited the first paragraph and added a block at the top meanwhile.
  const current = "# T\n\nÜleval uus.\n\nEsimene lõik, teise käega.\n\nKeskmine.\n\nTeine lõik.\n";
  expect(apply(d, all(d), current)).toEqual({
    markdown: "# T\n\nÜleval uus.\n\nEsimene lõik, teise käega.\n\nKeskmine.\n\nTeine lõik, muudetud.\n",
    skipped: [0],
  });
  // Left out, it is not reported.
  expect(apply(d, only(d, 1), current).skipped).toEqual([]);
  // A block put in the middle of a change's blocks meanwhile: they no longer stand together.
  const two = diff("A\n\nB\n\nC\n", "A\n\nX\n\nY\n\nC\n");
  expect(apply(two, all(two), "A\n\nB\n\nC\n").skipped).toEqual([]);
  expect(apply(diff("A\n\nB\n\nD\n\nC\n", "A\n\nC\n"), [true], "A\n\nB\n\nuus\n\nD\n\nC\n").skipped).toEqual([0]);
});

test("additions onto a changed note go next to the block they followed, else before the one they preceded", () => {
  const d = diff("A\n\nB\n", "A\n\nuus\n\nB\n");
  expect(apply(d, [true], "A\n\nvahel\n\nB\n").markdown).toBe("A\n\nuus\n\nvahel\n\nB\n");
  expect(apply(d, [true], "A muudetud\n\nB\n").markdown).toBe("A muudetud\n\nuus\n\nB\n");
  expect(apply(d, [true], "A muudetud\n\nB muudetud\n")).toEqual({ markdown: "A muudetud\n\nB muudetud\n", skipped: [0] });
  const end = diff("A\n", "A\n\nlõpp\n");
  expect(apply(end, [true], "A muudetud\n").markdown).toBe("A muudetud\n\nlõpp\n");
});
