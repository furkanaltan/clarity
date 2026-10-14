# Icon glass: foundation and navigation acceptance

Branch: `feature_icon-glass`.
Scope: first stage only. Asset, expense, profile and status families require
the operator's explicit design approval before implementation.

## Preserved baseline

`b28fd8fbbabb51a402e2e9206f85562044dcfefc` records the already present local
login copy change and its tests, byte-for-byte matching accepted deployment
candidate `3fcbf62907f0d3ba4e03c342e9c83c3b0a0890bc`. This preservation commit
precedes the icon work. Login and report implementation remain unchanged.

## Implemented family

The existing SVG sprite gains five separate `rove-module-tab-*` symbols:

| Existing control | Tabler outline glyph | Badge |
| --- | --- | --- |
| `data-tab="home"` | `home` | 52 px |
| `data-tab="ziele"` | `flag` | 52 px |
| `id="addBtn"` | `plus` | 60 px |
| `data-tab="tx"` | `arrows-exchange` | 52 px |
| `data-tab="vertraege"` | `file-check` | 52 px |

No mapping deviations. The original glyph paths are from Tabler Icons v3.42.0;
stroke width is adapted to 1.75 in the 24x24 viewBox. The full MIT notice is in
`THIRD_PARTY_NOTICES`. No webfont, CDN or runtime dependency is added. Existing
module symbols are retained byte-for-byte; the other family mappings are not
changed.

## Material and layout

`.rv-glass` defines round badges with the supplied black gradient, upper
reflection, inner contour and shadows. The supported size classes are 30, 44,
52, 60 and 96 px. Only `.tab.on` applies the blue gradient. The explicit
`data-state="warning"` variant is prepared but not applied to any current UI.
Ridges are disabled at 30 px, with `.rv-glass--plain`, and inside
`.rv-glass-list`. Five navigation badges use static gradient layers without
backdrop blur, blend compositing, animation or scaling on selection/press.

The central control retains its ID and accessible name and gains a visible
"Erfassen" label. Existing navigation routes and the quick-entry handler are
unchanged. All four inline script blocks are byte-identical to the baseline,
including the iOS tab-bar anchoring code. The only shared layout compensation
adds the existing bottom safe-area value to screen bottom padding so the taller
badges do not cover the last content row.

## Validation

- 208/208 navigation, icon, build, auth, session, reset and report regression
  tests PASS, including two new SVG/navigation checks.
- 133/133 remaining targeted frontend regression tests PASS.
- Four inline JavaScript blocks parse successfully; `git diff --check` PASS.
- Local synthetic app rendered at 390x844 and 1280x900 with external network
  connections blocked by the preview's Content Security Policy.
- Five repeated tab changes preserve exact badge and bar rectangles. Badge
  widths remain `[52, 52, 60, 52, 52]`; bar height is 104 px at zero safe-area.
- Exactly one blue badge corresponds to the active tab; all glyph strokes are
  white. The quick-entry control still opens and closes its existing sheet.
- Light Mode preserves the same badge sizes, colors and white glyphs.
- At the bottom of the synthetic home list, the last asset row ends at y=705.20
  while the tab bar begins at y=740 on the 390x844 viewport.

No test on physical iOS/Safari hardware or device frame-time measurement was
performed. No long list uses the new badges in this first stage.

## Review artifacts

Local synthetic preview: `http://127.0.0.1:65191/#offline`.

Screenshots are outside the repository at
`/Users/furkanaltan/Documents/Codex/icon-glass-review-20261014/`:

- `mobile-390-home.png`
- `desktop-1280-home.png`
- `mobile-390-light.png`
- `mobile-390-bottom.png`
- `mobile-navigation-checks.json` records the five geometry/state checks.

No Production access, deployment or push was performed. Stop after this stage
and await design approval before changing any other icon family.
