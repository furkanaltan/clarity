# Rov.E Migrations

## Contract cancellation finalization V3 (28.09.2026)

The API-owned savepoint migration extends case/event CHECK constraints for
`FOLLOW_UP_DUE`, `FOLLOW_UP_PREPARED`, `MANUAL_REVIEW_REQUIRED` and their audit
events. It preserves V2 case IDs, revisions, confirmation hashes, ordered messages,
receipt IDs, response evidence and all original timestamps. V1 migrations still
rename `CONFIRMED` to `USER_CONFIRMED`, never to a provider confirmation.

The nullable `confirmed_provider_name` captures the provider at text confirmation.
For existing confirmed cases it is recovered only from the deterministic notice's
explicit cancellation heading; no current contract rename is substituted as
historical evidence. Missing proof blocks follow-up preparation and requires
manual review. The existing V2 payload hash is not recalculated or silently
repaired during this migration.

`app_contract_cancellation_followups` stores one user-prepared, immutable text per
owned case, linked to its original dispatch. It cascades with case/message deletion
and participates in private export and existing deletion/tombstone lifecycle.
There is no automatic sender, follow-up queue, public link, persisted PDF or new
financial column. PDF generation is private, on demand and in memory.

The existing daily report maintenance adds a bounded (100 changes/run) VKS reminder
step only after the API schema exists. An accepted send without any documented
reply becomes due after 14 days, and manual review after 28 days. These are internal
product intervals, not legal deadlines. No reminder after a response/final state;
late evidence is still accepted for manually reviewed cases. A contract's enddate
only affects a server-derived display label, never cash/fixed-cost calculations.

Release prerequisites: verified DB backup/integrity/FKs, V1/V2/V3 and privacy tests,
PDF runtime availability and maintenance timer verification. Do not downgrade code
against V3 states; keep code/DB rollback coordinated. Mail remains OFF; an exact
internal test allowlist and separate live approval are required. See
`docs/VKS_OPERATIONS.md`. No deployment or real mail test has been performed.

## Contract cancellation send/proof V2 (28.09.2026)

`ensure_cancellation_schema()` extends V1 during controlled API startup. Its
savepoint rebuilds the case/event tables to extend their CHECK constraints,
preserves IDs, notices, revisions and original timestamps, and renames V1
`CONFIRMED` to `USER_CONFIRMED` (never provider confirmation). Historical events
are marked `legacy/v1_migration`; confirmed V1 payload hashes are recorded without
changing the notice. The migration is idempotent and rolls back on failure.
`ensure_app_contracts_table()` adds nullable `cancellation_status`,
`effective_end_date`, `cancellation_confirmed_at`; no financial column changes.

`app_contract_cancellation_messages` owns outbound attempts and manually entered
inbound responses, scoped by user/case with cascade deletion. Outbound notice text
is not duplicated; the case remains its source. The case history, messages and
contract metadata are included in private export and existing account deletion /
restore-tombstone cleanup. No new external artifacts or public report links exist.
Cases are retained with the owned contract until its/account deletion, not in a
separate retention store. Inbound messages are capped at 20 per case / 6,000 chars.

The send gate is OFF unless `ROVE_VKS_EMAIL_ENABLED=1`. Transport reuses
`BREVO_API_KEY`, `ROVE_LOGIN_FROM_EMAIL` and `ROVE_LOGIN_FROM_NAME`; From must be a
registered platform sender and Reply-To is the user's verified account email.
Only explicitly confirmed notice text is sent. Attempts are committed before
network I/O, at most 3 per case / 10 per user per rolling 24 hours. No automatic
retries: timeout, 5xx, 409, malformed receipt or process exit keep resend locked.
Only a known rejection permits a new explicit audited attempt. A crash can leave
SENDING indefinitely: support must reconcile the provider receipt/correlation
before any future recovery procedure, never blindly reset or resend.

Brevo acceptance is SENT; only an authenticated, exactly correlated delivered
event from its events API records delivery. No unauthenticated webhook exists.
Without secure inbound infrastructure, the user documents the received response
and explicitly confirms its result; source is `user_confirmed_provider_message`.
No end date is inferred. A confirmation changes only contract metadata, not its
amount/active fixed costs. No AI adjudication or automatic follow-up exists.

Before release: consistent DB backup, integrity/FK checks, Python-3.12 V1/V2 /
privacy gates, registered sender/Reply-To approval, provider data-retention review
and an explicitly authorized internal end-to-end delivery test. Local tests must
mock transport (no real emails). Deletion stops persisted queued cases; a network
request already in flight cannot be recalled. Brevo/recipient copies are external
mail records, not artifacts deleted by local account cleanup.

Rollback: keep a verified pre-V2 DB backup. Do not run V1 against dispatched V2
states or silently downgrade the schema; disable the send gate first and plan
DB/code recovery together. No migration/backfill of financial data. NOT DEPLOYED.

## Contract cancellation preparation V1 (28.09.2026)

`rove_contract_cancellation.ensure_cancellation_schema()` adds
`app_contract_cancellations` and `app_contract_cancellation_events` during
controlled API startup, before requests are accepted. The schema is additive
and idempotent; no contract or historical data is migrated. Requests fail with
503 if preparation is missing. A partial unique index allows at most one open
case per user/contract. Composite foreign keys enforce ownership and cascade
on explicit contract/account deletion. Both tables carry `user_id` and use
the existing account-delete, tombstone-reapply and private export lifecycle.

Contract/provider identity stays in `app_contracts`; only user-supplied review
information and the reviewed notice are stored in the case. The current
contract schema has no trustworthy termination dates/contact data. No debit
day, inferred profile name, or legacy display text is used as such evidence.
Dates are explicitly user supplied; `next_possible` requires a conscious choice.
Confirmation binds to the persisted revision and notice SHA-256, checks that
the contract identity is unchanged, and records CONFIRMED then READY_TO_SEND
atomically. No sender, queue, PDF, financial mutation or contract-status change.

Before rollout: production DB backup, controlled schema preparation, integrity
and FK checks, Python-3.12 API/privacy tests and internal review. Code rollback
leaves the new tables intact. Production status: NOT DEPLOYED.

## Personal buffer target (25.09.2026)

`users.buffer_target_amount` is an optional, user-owned amount, not a goal or
an allocation. `ensure_buffer_target_column()` runs in the existing API startup
schema-preparation block. The nullable REAL column is additive/idempotent;
existing users remain NULL, with no backfill and no balance changes. Positive
targets up to the existing profile-amount limit of EUR 1,000,000 are accepted;
an explicit JSON null removes the target. The existing `/v1/profile` write and
`/v1/state` response carry the preference and the derived `buffer` object.
Requests never add this column; writes fail with 503 if startup preparation
was omitted. User export/deletion already include the owning `users` row.

Before rollout: DB backup, inspect `PRAGMA table_info(users)`, apply through
controlled API startup, then integrity/FK checks. A code rollback leaves the
nullable column and targets intact. Production status: NOT DEPLOYED.

## Property coverage boundary (12.09.2026)

`app_properties.coverage_started_at` is an additive, user-scoped asset
metadata field. New properties receive `CURRENT_TIMESTAMP` when first
created. Existing properties without a value receive the one-time rollout
timestamp on schema preparation; no purchase date or historical value is
inferred. The frontend reads this server value and may cache only a mirror;
the field does not change net-worth calculations or financial history.

`app_properties.coverage_equity_at_start` is an additive snapshot of the
property equity known when coverage began. It is set only on first creation;
updates leave it unchanged. Existing rows receive their current stored equity
once during schema preparation as the rollout baseline, never as a backdated
historical value. The V2 chart uses this snapshot for post-boundary comparison;
current equity remains the source for current net worth.

## Cash request receipts (09.09.2026)

`rove_app_api.cash_request_replay()` creates `app_cash_request_receipts`
additively inside the existing `BEGIN IMMEDIATE` transaction. The primary key
is `(user_id, request_id)`. Operation, canonical payload and original response
are request receipts, not another balance source. Income and legacy
transfer/adjust writes commit or roll back together with their receipt.
No historical movements are migrated. Calls without request IDs remain
compatible but cannot be deduplicated; the current frontend supplies IDs.


Stand: 31.08.2026. Migrationen werden niemals allein aufgrund ihres Namens
erneut ausgefuehrt. Der produktive Anwendungsstatus ist in diesem Repository
nicht beweisbar und wird deshalb als `UNKNOWN` dokumentiert.

| Datei | Eingefuehrt | Zweck | Idempotent | Produktion angewandt | Sicher erneut ausfuehrbar | Abhaengigkeit |
|---|---|---|---|---|---|---|
| `backfill_app_card_expenses.py` | 25.07.2026 | Karten-Ausgaben in Cash-Zustand nachtragen | Laut Script ja | UNKNOWN | Dry-run ja; Apply nur nach Scope-Pruefung | `rove_app_state.py` |
| `migrate_financial_accounts.py` | 15.08.2026 | Finanzkonten Sprint 1 | Bedingt/UNKNOWN | UNKNOWN | Dry-run ja; Apply UNKNOWN | `rove_financial_accounts.py` |
| `migrate_financial_account_references.py` | 15.08.2026 | Nullable Kontoreferenzen | Schema-seitig ja | UNKNOWN | Dry-run ja; Apply nur nach Preconditions | `migrate_financial_accounts.py`, `rove_financial_accounts.py` |
| `migrate_etf_contribution_schema.py` | 20.08.2026 | `holding_id` fuer ETF-Beitraege | Ja | UNKNOWN | Ja, aber zuerst Dry-run | `rove_investment_contributions.py` |
| `repair_etf_contribution_assignments.py` | 20.08.2026 | Legacy-ETF-Zuordnungen reparieren | Bedingt | UNKNOWN | UNKNOWN ohne Nutzer- und Schema-Pruefung | `rove_investment_contributions.py` |
| `prepare_multi_account_active_testers.py` | 20.08.2026 | Aktive Tester fuer Multi-Account vorbereiten | Nein/bedingt | UNKNOWN | Nein ohne Rollout-Pruefung | `migrate_financial_accounts.py`, `rove_financial_accounts.py` |
| `migrate_report_snapshots_v2.py` | 21.08.2026 | Additive Report-Snapshot-Tabelle | Ja | UNKNOWN | Ja, aber zuerst Dry-run | `report_engine.py` |
| `migrate_behavior_snapshot.py` | 18.09.2026 | Additive Coach-V4-Snapshot-Tabelle und Queue-Index | Ja | UNKNOWN | Ja, aber zuerst Dry-run | `rove_behavior_snapshot.py` |
| `migrate_legacy_contracts.py` | 24.08.2026 | Legacy-Fixkosten in Vertraege normalisieren | Laut Script ja | UNKNOWN | Dry-run ja; Apply nur nach Gate | `rove_app_state.py` |
| `retire_legacy_app_state.py` | 24.08.2026 | Legacy-State sichern, widerrufen und entfernen | Inventory ja; Apply bedingt | UNKNOWN | Apply UNKNOWN | `app_state_links`, State-Verzeichnis |
| `monthly_financial_snapshots` | 07.09.2026 | Immutable Finanzwerte fuer abgeschlossene Monatsreports | Runtime `CREATE TABLE IF NOT EXISTS` | UNKNOWN | Ja, additiv | `rove_app_state.py`, Monatsabschluss |
| `app_vehicle_financings` | 09.09.2026 | User-scoped Fahrzeugfinanzierungen, verknuepft mit `app_contracts` | Runtime `CREATE TABLE IF NOT EXISTS` | UNKNOWN | Ja, additiv | `rove_vehicle_financing.py`, `rove_app_api.py` |

`app_cash_movements.request_id` wird durch die bestehende additive
Schema-Vorbereitung in `rove_app_state.py` und
`rove_financial_accounts.py` nachgeruestet. Der user-scoped Unique-Index ist
idempotent; historische Bewegungen behalten einen leeren Wert und werden nicht
nachtraeglich dedupliziert.

`app_auth_login_limits` wird additiv und idempotent durch `ensure_auth_tables()`
in `rove_app_api.py` angelegt. Die Tabelle speichert ausschliesslich gehashte
Login-Subjekte sowie temporaere Fehlerzaehler und Backoff-Zeitpunkte; keine
E-Mail-Adressen oder Passwoerter. Ein separater Produktions-Migrationslauf ist
nicht erforderlich.

`monthly_financial_snapshots` wird beim bestaetigten Monatsabschluss additiv und
idempotent angelegt. Pro `(user_id, report_month)` wird genau ein Snapshot in
derselben Transaktion wie `app_month_closures` geschrieben. Es gibt bewusst
keinen Backfill aus aktuellen Profilwerten: Fuer alte Monate ohne Snapshot
bleiben nicht beweisbare Finanzwerte im Report nicht verfuegbar.

## Ausfuehrungsregeln

Consumer Debt V1: `rove_consumer_debt.ensure_consumer_debt_schema()` legt
`app_consumer_debts` beim ersten Write additiv/idempotent an. Keine Migration
aus `fixed_costs_details.kredite.restschuld`. Monatsraten bleiben in Vertraegen;
die neue Tabelle speichert nur positive/Null-Restschulden, aktiv/inaktiv und Typ.
Hypothek, Fahrzeugfinanzierung und Dispo sind keine erlaubten Typen. Bereits
negative Cash-Konten werden ausschliesslich im Cash-Aggregat beruecksichtigt.
Create-Requests tragen eine optionale user-scoped `request_id` mit einem
Payload-Fingerprint. Der eindeutige Index verhindert doppelte Anlagen bei
Responseverlust; ein abweichender Retry wird als Konflikt abgewiesen. Die
additive `app_consumer_debt_events`-Tabelle speichert Create-, Balance-,
Deactivate- und Delete-Zeitpunkte. Bestandszeilen erhalten hoechstens einen
`legacy_baseline` ab `created_at`; es gibt keinen Backfill des heutigen Saldos
auf fruehere Zeitraeume.

Vehicle Financing V1: `rove_vehicle_financing.ensure_vehicle_financing_schema()`
legt `app_vehicle_financings` additiv/idempotent an. Die Tabelle speichert nur
user-scoped Fahrzeugmetadaten und verweist auf genau einen bestehenden
`app_contracts`-Datensatz; die Monatsrate bleibt ausschliesslich in
`app_contracts.amount`. Finanzierung und Leasing werden nicht als Asset,
Eigenkapital oder Consumer Debt behandelt. Bestehende Auto-/Leasingvertraege
werden nicht automatisch migriert; neue Datensaetze werden atomar mit ihrem
Rate-Vertrag angelegt und bei Loeschung gemeinsam entfernt.

`monthly_financial_snapshots.total_consumer_debt` wird durch die vorhandene
Snapshot-Schemavorbereitung nullable hinzugefuegt. Neue Snapshots (Version 2)
frieren die Summe in der bestehenden Abschluss-Transaktion ein. Alte Zeilen
bleiben NULL und unveraendert; neu erzeugte historische Reports zeigen dann
kein behauptetes schuldenbereinigtes Nettovermoegen. Kein Backfill, keine
Aenderung vorhandener `report_snapshots_v2`. Allocation zeigt positive Assets,
nicht Anteile am nach Schulden moeglicherweise negativen Nettovermoegen.
User-Export enthaelt die Positionen; bestehende user-scoped Account-Loeschung
erfasst die Tabelle automatisch. Produktionsstatus: UNKNOWN, nicht deployed.

1. Produktionsstatus und betroffene Nutzer read-only pruefen.
2. Datenbankbackup mit restriktiven Rechten erstellen und validieren.
3. Wenn vorhanden, zuerst Dry-run beziehungsweise Inventory ausfuehren.
4. Preconditions, erwartete Zeilenzahl und Idempotenz dokumentieren.
5. Migration nur gegen den explizit gewaehlten DB-Pfad ausfuehren.
6. Danach Integritaet, Foreign Keys und fachliche Drift-Gates pruefen.
7. Backup- und Migrationspfad im Deployment-Protokoll festhalten.

`UNKNOWN` ist eine Sperre fuer blindes Wiederholen, kein Hinweis auf einen
Fehler im Script.
