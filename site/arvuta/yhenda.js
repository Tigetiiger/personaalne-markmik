// Merging a co-editor's change into a cell's code editor (site/arvuta/koodiredaktor.js): pure
// functions, no imports, so the tests load this file on its own.

/** The one changed span between two texts: {from, to} in a, and what b has there. */
export function span(a, b) {
  let from = 0;
  while (from < a.length && from < b.length && a[from] === b[from]) from++;
  let end = 0;
  while (end < a.length - from && end < b.length - from && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  return { from, to: a.length - end, insert: b.slice(from, b.length - end) };
}

/** Their change (base → theirs) as a change to `mine` ({from, to, insert} in mine), or null when it
 *  touches the same place as mine's own change (base → mine): then mine wins here, theirs stays in
 *  the block until mine is pushed over it. */
export function theirChange(base, mine, theirs) {
  const t = span(base, theirs);
  if (mine === base) return t;
  const own = span(base, mine);
  if (t.to <= own.from) return t;
  if (t.from >= own.to) {
    const shift = own.insert.length - (own.to - own.from);
    return { from: t.from + shift, to: t.to + shift, insert: t.insert };
  }
  return null;
}
