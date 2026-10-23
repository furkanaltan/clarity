# Cockpit static dependencies

These assets are served from the same origin as `cockpit.html`. The cockpit
does not contact a script CDN or Google Fonts at runtime. Publish the entire
`cockpit/` directory together so the page, scripts and fonts remain consistent.

## Chart.js

- Version: 4.4.1, unchanged from the previous CDN dependency.
- Source: https://registry.npmjs.org/chart.js/-/chart.js-4.4.1.tgz
- Package integrity (verified before extraction):
  `sha512-C74QN1bxwV1v2PEujhmKjOZ7iUM4w6BWs23Md/6aOZZSlwMzeCIDGuZay++rBgChYru7/+QFeoQW0fQoP534Dg==`
- `chart.umd.js`, its source map and `LICENSE.md` are unmodified package files.
- License: MIT, included in `chartjs-4.4.1/LICENSE.md`.

## Fonts

The existing Inter (400, 500, 600) and JetBrains Mono (400, 500) faces and their
Unicode subsets were downloaded on 2026-09-30 from the existing Google Fonts
stylesheet:

https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap

The WOFF2 files use the stylesheet's Inter v20 and JetBrains Mono v24 URLs.
`fonts/fonts.css` preserves the supplied faces, weights and Unicode ranges,
with source URLs replaced by local relative paths. Fonts remain under the
SIL Open Font License 1.1; the licenses are included in `fonts/Inter-OFL.txt`
and `fonts/JetBrainsMono-OFL.txt`.

## Integrity

`SHA256SUMS` records the exact static files. From this directory, verify with:

```sh
shasum -a 256 -c SHA256SUMS
```
