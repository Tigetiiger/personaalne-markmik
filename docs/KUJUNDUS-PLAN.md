# Applying the "Riba" design (2026-09-26)

Spec: `docs/KUJUNDUS.md` (the user's). Clickable mock: `docs/kujundus-riba.html`. This file records
the decisions that fill the mock's gaps and the checklist. It is built as package `wp/riba`.

## Decisions (the user, 2026-09-26)

| Gap in the mock | Decision |
|---|---|
| The note's right panel (Viited, Sisukord, Seotud, Abiline, Sündmused) is not in the mock | It is hidden by default, so the note is wide. The breadcrumb row opens it: "✦ AI" opens Abiline, and a new "Viited" button opens the other tabs. Ctrl+Shift+\\ toggles it. |
| "Ajalugu" button | Left out: the app has no version history. |
| Default theme | Follow the system (`prefers-color-scheme`). The toggle can still choose light or dark. Dark is "Riba"; light keeps the current paper tokens. |

## Lead's calls

- The spec's names (`--bg`, `--p1`, `--ac`, …) are mapped onto the variables the CSS already uses,
  so the packages' CSS files change as little as possible. Each package's own colours are
  switched to tokens.
- The font is `system-ui` everywhere. The self-hosted font files and their link are removed if
  nothing else uses them.
- "Logi välja" moves off the bar, to the Seaded page (and the palette). The gear opens Seaded.
- `g t / g m / g k / g g / g p` and `n` work outside text fields and the editor. They are listed
  in the palette.

## Checklist

- [ ] Tokens: dark "Riba" and the existing light theme; follows the system by default; the toggle
      works and remembers the choice; no flash on load (theme.js).
- [ ] Top bar per spec: brand, pages as text, the Postkast count, search ⌘K, "+ Uus", gear. Sticky,
      3 rem.
- [ ] Layout: left panel (15 rem) only on Täna and Märkmed; Kalender, Graaf, Postkast and Seaded
      run full width.
- [ ] Breadcrumb row on each page, with that page's buttons on the right (note: Jaga, Viited,
      ✦ AI; Täna: Eile/Homme; Kalender: views + "+ Sündmus").
- [ ] Right panel hidden by default, opened from the breadcrumb row.
- [ ] Täna: three stat cards on top; rows, not cards, below.
- [ ] Elements per spec: buttons, rows, chips, AI mark, focus ring, headings, note typography.
- [ ] Calendar kinds and states per spec, in both themes.
- [ ] Editor (Muya) and the AI review window readable in both themes; share pages too.
- [ ] Phone (< 900 px): the left panel hides, the page links scroll, search hides.
- [ ] Keyboard shortcuts.
- [ ] Every screenshot baseline redone, and looked at. Full suite: pytest, and Playwright
      chromium, firefox and mobile.
