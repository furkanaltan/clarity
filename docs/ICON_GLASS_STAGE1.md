# Rov.E Icon Glass: app-wide feature icon family

Branch: `feature_icon-glass`.
This records the user's expanded request after the earlier asset-only scope
correction. Local changes only; no push or Production deployment is authorized.

## Scope

The black-glass material is used for feature-icon badges across goals,
contracts, profile/settings, mentoring offers, expense categories, activity,
status and related feature surfaces. The top-right profile initial uses the
same black-glass treatment. The bottom tab bar, including its original icons,
sizes, selected state, quick-entry control and animations, remains unchanged.
Existing labels, routes and accessible names remain intact.

Seven asset glyphs remain the shared local symbols used by home, details,
account type selection and asset ordering:

| Asset type | Outline glyph |
| --- | --- |
| Girokonto | `building-bank` |
| Tagesgeld | `pig-money` |
| Bargeld | `cash` |
| Investments / ETF | `trending-up` |
| Krypto | `circle-letter-b` |
| Immobilie | `building` |
| Sachwerte | `diamond` |

Tabler paths are local SVG symbols; no webfont, CDN or runtime dependency is
added. Existing financial classification and position-logo behavior are
unchanged. The profile/chat label remains `Ask Rovi` without route changes.

## Preserved distinctions

Rovi's animated face in the home mentor card and chat header remains in its
existing design; it is character artwork rather than a feature icon. Merchant
and coin logos, charts, financial data glyphs and small inline action controls
such as arrows remain distinct. Real warning/error statuses retain their red
indicator. No financial logic, authentication, report behavior, account data
or backend code is changed.

## Material

Badges use the round black-glass gradient, subtle reflection, inner contour and
shadow with white outline glyphs in dark and light themes. List variants
suppress texture where needed for legibility. No backdrop blur, blend
compositing or new badge animation is introduced.

## Validation

- 108/108 icon, navigation, support, settings, build, canonical asset restore
  and merchant-analysis regression tests pass, including 24 focused icon tests.
- The bottom-navigation markup is byte-for-byte identical to its pre-expansion
  version; tests also guard that it stays free of glass badges. The profile
  initial is checked for black-glass styling in both themes.
- The local synthetic app was rendered and reviewed at 390 px across home and
  contracts; the original tab bar, profile initial and feature badges are visible.
- All seven home asset badges resolve to local SVG symbols with visible white
  strokes; no missing asset references or horizontal overflow were observed.
- The animated Rovi face and merchant marks retain their original treatment.
- Screenshots were shown in the local preview during review, but standalone
  screenshot files were not exported. `git diff --check` passes.

No Production access, deployment or push was performed.
