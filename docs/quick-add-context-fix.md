# Quick-Add navigation

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

The release artifact must be built on the verified production frontend
`frontend-release-20261021-27af4c9` (SHA256
`2eb7ae476b1e8aa5139aa858d087dce45c326fcc7bdf886c1b70d0a82e117bd8`),
with only this frontend patch and its build marker applied. This preserves the
existing production-only report-navigation adjustment. No backend release or
service restart is required.
