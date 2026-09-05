# Rov.E Migrations

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
| `migrate_legacy_contracts.py` | 24.08.2026 | Legacy-Fixkosten in Vertraege normalisieren | Laut Script ja | UNKNOWN | Dry-run ja; Apply nur nach Gate | `rove_app_state.py` |
| `retire_legacy_app_state.py` | 24.08.2026 | Legacy-State sichern, widerrufen und entfernen | Inventory ja; Apply bedingt | UNKNOWN | Apply UNKNOWN | `app_state_links`, State-Verzeichnis |

`app_cash_movements.request_id` wird durch die bestehende additive
Schema-Vorbereitung in `rove_app_state.py` und
`rove_financial_accounts.py` nachgeruestet. Der user-scoped Unique-Index ist
idempotent; historische Bewegungen behalten einen leeren Wert und werden nicht
nachtraeglich dedupliziert.

## Ausfuehrungsregeln

1. Produktionsstatus und betroffene Nutzer read-only pruefen.
2. Datenbankbackup mit restriktiven Rechten erstellen und validieren.
3. Wenn vorhanden, zuerst Dry-run beziehungsweise Inventory ausfuehren.
4. Preconditions, erwartete Zeilenzahl und Idempotenz dokumentieren.
5. Migration nur gegen den explizit gewaehlten DB-Pfad ausfuehren.
6. Danach Integritaet, Foreign Keys und fachliche Drift-Gates pruefen.
7. Backup- und Migrationspfad im Deployment-Protokoll festhalten.

`UNKNOWN` ist eine Sperre fuer blindes Wiederholen, kein Hinweis auf einen
Fehler im Script.
