# Icon glass: corrected asset-only scope

Branch: `feature_icon-glass`.
Scope: the seven asset types only. This supersedes the navigation stage in
`0a970be299c22966d906c43ef4d62012e98c624a` following the operator's explicit
scope correction. No deployment or push is authorized.

## Preserved baseline

`b28fd8fbbabb51a402e2e9206f85562044dcfefc` records the already present local
login copy change and its tests, byte-for-byte matching accepted deployment
candidate `3fcbf62907f0d3ba4e03c342e9c83c3b0a0890bc`. This preservation commit
precedes the icon work. Login and report implementation remain unchanged.

## Implemented family

The existing SVG sprite gains seven separate `rove-module-asset-*` symbols:

| Asset type | Tabler outline glyph | Badge |
| --- | --- | --- |
| Girokonto | `building-bank` | 44 px |
| Tagesgeld | `pig-money` | 44 px |
| Bargeld | `cash` | 44 px |
| Investments / ETF | `trending-up` | 44 px |
| Krypto | `circle-letter-b` | 44 px |
| Immobilie | `building` | 44 px |
| Sachwerte | `diamond` | 44 px |

No mapping deviations. The original glyph paths are from Tabler Icons v3.42.0;
stroke width is adapted to 1.75 in the 24x24 viewBox. The full MIT notice is in
`THIRD_PARTY_NOTICES`. No webfont, CDN or runtime dependency is added. Existing
module symbols are retained byte-for-byte. The five unused glass-navigation
symbols have been removed. The non-asset family mappings remain unchanged.

## Restored navigation and preserved behavior

The bottom navigation HTML is byte-identical to `b28fd8f...`, including the
original module symbols, the central `.plus` control and its accessible name.
The icon-stage navigation overrides and compensating screen padding have been
removed. All original CSS, including selected-state colors, size rules and
press/selection animations, is byte-identical to that baseline. Only asset-
specific glass CSS is appended.

`assetIcon()` retains its original classification logic, including renamed
cash accounts. Only the seven `ICONS.asset*` entries use `assetGlassIcon()`.
Home, detail headers, account-type pickers and account-order lists share these
local SVGs. The Krypto group header now shows the B-in-circle asset icon rather
than a Bitcoin position logo. Individual crypto and investment position marks,
their fallback letters, merchant logos, onboarding and action icons are
unchanged. Generic unknown-asset SVG fallbacks retain bounded dimensions.

The profile entry and chat title display "Ask Rovi". The existing talk route,
assistant behavior and profile icon are unchanged. Login V2, Report V2,
authentication, financial calculations, account data and backend files are
not modified.

## Material and layout

`.rv-glass` defines round badges with the supplied black gradient, upper
reflection, inner contour and shadows. The active asset badge size is 44 px,
with white outline strokes in dark and light themes. No asset receives blue
active-tab or red warning treatment. Ridges are disabled in account-order
lists, below 36 px and through the existing plain/list variants. No backdrop
blur, blend compositing or badge animation is added.

## Validation

- 345/345 targeted navigation, icon, build, auth, session, reset, frontend and
  report regression tests PASS, including 21 focused icon tests.
- The icon tests execute the real asset helpers, account-order renderer and
  account-type picker with synthetic inputs; seven mappings and renamed
  accounts are checked along with passive local SVG references and fallbacks.
- Four inline JavaScript blocks parse successfully; `git diff --check` PASS.
- Local synthetic app rendered at 390x844 and 1280x900 with external network
  connections blocked by the preview's Content Security Policy.
- Four tab changes preserve the original 68 px bar at zero safe-area and no
  glass badges. Quick entry opens and closes its existing sheet.
- All seven asset glyphs have actual paths, visible white strokes, 44x44 px
  badges and 20.23x20.23 px SVGs. No missing-glyph font is used.
- Light Mode preserves black glass, white outlines and unchanged navigation.
- At 390x844 the screenshot shows all seven asset badges between y=201.70
  and y=690.20; the tab bar begins at y=776. No horizontal overflow.
- Original module SVGs, asset classification, crypto/investment position-logo
  helpers and generic transaction-detail logo helper are byte-identical to
  the pre-icon baseline.

No test on physical iOS/Safari hardware or device frame-time measurement was
performed. Account-order rendering is covered synthetically; its authenticated
server-backed entry is not exercised against Production.

## Review artifacts

Local synthetic preview: `http://127.0.0.1:65191/#offline`.

Screenshots are outside the repository at
`/Users/furkanaltan/Documents/Codex/icon-glass-assets-review-20261014/`:

- `mobile-390-assets.jpg`
- `original-bottom-navigation.jpg`
- `desktop-1280-assets.jpg`
- `mobile-390-light.jpg`
- `mobile-390-crypto-detail.jpg`
- `browser-checks.json` records navigation and seven asset geometry checks.
- `regression-tests.log` records the targeted regression run.

No Production access, deployment or push was performed. Other icon families
must retain their original appearance. Stop after the corrected asset scope.
