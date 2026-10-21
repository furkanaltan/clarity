# Quick-Add navigation

## Bestaetigter Release-Status, 21.10.2026

`frontend-d94b3c7` wurde durch Furkan nach konkreter Dateiwechsel-Freigabe
atomisch bereitgestellt. Seine uebermittelte Terminalausgabe bestaetigt den
oeffentlichen und lokalen SHA256
`e77e5789ace28a9724584b458c64159ac82af6efeec174dc13d5c424a3861262`,
Build-Marker, beide API-Healthchecks und unveraenderte weitere Assets.
Kein Dienstneustart. Kein Push in diesem Ablauf.

Deployment: **PASS laut Operator-Beleg**, kein neuer Live-Zugriff durch Codex.
Produktive iPhone-/Mac-Betreiberabnahme: **PENDING**, noch keine ausdrueckliche
Bestaetigung im vorliegenden Verlauf. Daher kein behauptetes FROZEN nach Abnahme.
Die lokalen Browser-Smokes unten ersetzen diese Abnahme nicht.
Artefaktzuordnung und die inzwischen kanonisch angeglichene Navigation stehen in
`RELEASE_TRACEABILITY_20261021.md`.

## Ursache und lokale Validierung

The normal save and safe-retry success paths explicitly called
`closeSheetAndNavigate("tx")`. This caused a confirmed Dashboard entry to switch
to Cashflow. The browser regression reproduced `tab-tx` instead of `tab-home`.

Both success paths now use `closeQuickEntrySheet()`. It closes the sheet without
queuing `history.back()` and replaces its history entry with the currently active
tab. It does not call `go()`, so the screen, scroll position and Cashflow filter
remain intact. Other explicit navigation actions keep their existing helper.

Booking, request IDs, confirmation, canonical refresh, budgets, balances and the
existing success toast are unchanged. Failed saves keep the form and input.

Validation: 79/79 targeted tests, six browser smoke flows and four inline
JavaScript syntax checks passed.

- First-user, navigation, sheet-close/swipe, notification frontend and build tests.
- Real frontend browser flows using synthetic local API responses only.
- Dashboard, Goals, Cashflow and Contracts as starting contexts.
- Double submit, rejection, uncertain result, retry, reload, Back and Forward.
- Dashboard values and existing budget/account views update after confirmation.
- Chromium and WebKit, desktop and 390 px including standalone iPhone-PWA layout.

The original isolated mini-release was built on the verified production frontend
`frontend-release-20261021-27af4c9` (SHA256
`2eb7ae476b1e8aa5139aa858d087dce45c326fcc7bdf886c1b70d0a82e117bd8`),
with only this frontend patch and its build marker applied. The separately
approved source alignment now preserves that report-navigation adjustment in
the canonical frontend. With the historical `frontend-d94b3c7` marker, the
canonical source directly reproduces the confirmed `e77e5789...` artifact;
future builds use the normal builder and their own new Git commit ID. No
unversioned Production base is needed. No new deployment is performed here.
