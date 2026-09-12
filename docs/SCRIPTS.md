# Rov.E Scripts

Stand: 31.08.2026. Dieses Inventar trennt aktive Runtime-Dateien von
Betriebswerkzeugen, Entwicklungshilfen, Migrationen und Legacy-Code. Wave 3
verschiebt keine dieser Dateien, weil produktive Servicepfade und historische
Betriebsbefehle zuerst separat umgestellt werden muessten.

## Active Runtime

| Datei | Zweck | Risiko bei Verschiebung |
|---|---|---|
| `bot.py` | Telegram-Bot | Hoch |
| `rove_app_api.py` | Flask API | Hoch |
| `rove_app_state.py` | App- und Finanzzustand | Hoch |
| `rove_expense_domain.py` | Ausgabenwahrheit | Hoch |
| `rove_feature_announcements.py` | Feature Announcements | Hoch |
| `rove_financial_accounts.py` | Finanzkonten | Hoch |
| `rove_investment_contributions.py` | Investmentbeitraege | Hoch |
| `rove_market_data.py` | Markt- und Crypto-Daten | Hoch |
| `rove_monthly_reminders.py` | Monatserinnerungen | Hoch |
| `rove_tracking_reminders.py` | Tracking-Erinnerungen | Hoch |
| `rove_report_worker.py` | Report-Worker | Hoch |
| `rove_account_delete_cleanup.py` | Account-Loeschbereinigung | Hoch |
| `rove_score.py` | Scoreberechnung | Hoch |
| `report_ai_text.py` | AI-Reporttext | Hoch |
| `report_engine.py` | Reportdaten und Rendering-Basis | Hoch |
| `report_html_renderer.py` | HTML-Report-Rendering | Hoch |
| `report_story_v2.py` | Report-Story | Hoch |
| `rove_pdf_light_renderer.py` | Light-PDF-Rendering | Hoch |
| `rove_pdf_report_renderer.py` | PDF-Rendering | Hoch |
| `rove_web_report_renderer.py` | Web-Report-Rendering | Hoch |

## Operations

| Datei | Zweck | Sicher verschiebbar | Risiko |
|---|---|---|---|
| `accept_report_snapshot_v2.py` | Kontrollierte Snapshot-Abnahme | Nein | Hoch |
| `audit_multi_account_rollout.py` | Read-only Rollout-Audit | Nein | Mittel |
| `backup_clarity_db.py` | Produktives DB-Backup | Nein | Hoch |
| `invite_app_user.py` | Nutzer-Einladung | Nein | Mittel |
| `manage_financial_accounts_pilot.py` | Pilotverwaltung Finanzkonten | Nein | Hoch |
| `prepare_multi_account_active_testers.py` | Rollout-Vorbereitung | Nein | Hoch |
| `publish_feature_announcements.py` | Announcements veroeffentlichen | Nein | Mittel |
| `refresh_market_positions.py` | Marktpositionen aktualisieren | Nein | Hoch |

## Development

| Datei | Zweck | Sicher verschiebbar | Risiko |
|---|---|---|---|
| `preview_report_story_v2.py` | Lokale Report-Vorschau | Nein | Mittel |
| `scripts/test.sh` | Zentraler, dokumentierter `unittest`-Einstieg | Bereits im Zielpfad | Niedrig |

## Migration And One-Off

| Datei | Zweck | Sicher verschiebbar | Risiko |
|---|---|---|---|
| `backfill_app_card_expenses.py` | Karten-Ausgaben nachtragen | Nein | Hoch |
| `migrate_etf_contribution_schema.py` | ETF-Beitragsschema | Nein | Hoch |
| `migrate_financial_account_references.py` | Kontoreferenzen | Nein | Hoch |
| `migrate_financial_accounts.py` | Finanzkonten | Nein | Hoch |
| `migrate_legacy_contracts.py` | Legacy-Vertraege | Nein | Hoch |
| `migrate_report_snapshots_v2.py` | Report-Snapshots V2 | Nein | Hoch |
| `repair_etf_contribution_assignments.py` | ETF-Zuordnungen reparieren | Nein | Hoch |
| `retire_legacy_app_state.py` | Legacy-App-State stilllegen | Nein | Hoch |

Details und Wiederholbarkeit stehen in [MIGRATIONS.md](MIGRATIONS.md).

## Legacy

| Datei | Einordnung | Sicher verschiebbar | Risiko |
|---|---|---|---|
| `dashboard.py` | Historisches Flask-Dashboard; keine aktive Repository-Referenz gefunden, externer Aufruf aber nicht ausgeschlossen | Nein | Mittel |

## Entscheidung

In Wave 3 werden keine bestehenden Python- oder Shell-Skripte verschoben oder
geloescht. Die Trennung ist dokumentarisch; nur der neue Test-Runner wird direkt
unter `scripts/` angelegt. Eine spaetere physische Zielstruktur benoetigt zuerst
eine kontrollierte Umstellung von Imports, Systemd-Units, Deploypfaden und
Runbook.
