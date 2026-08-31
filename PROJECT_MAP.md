# Rov.E Project Map

Stand: 31.08.2026. Dieses Dokument beschreibt den aktuellen Aufbau und keine
bereits abgeschlossene Zielarchitektur.

## Produktive Entry Points

| Bereich | Entry Point | Produktionspfad |
|---|---|---|
| Web-App | `frontend/index.html` | `/var/www/getrove/app/index.html` |
| Service Worker | `frontend/sw.js` | `/var/www/getrove/app/sw.js` |
| Flask API | `rove_app_api.py` | `/root/clarity/rove_app_api.py` |
| Telegram-Bot | `bot.py` | `/root/clarity/bot.py` |
| Report Worker | `rove_report_worker.py` | `/root/clarity/rove_report_worker.py` |
| Monats-Erinnerungen | `rove_monthly_reminders.py` | `/root/clarity/rove_monthly_reminders.py` |
| Tracking-Erinnerungen | `rove_tracking_reminders.py` | `/root/clarity/rove_tracking_reminders.py` |
| Marktaktualisierung | `refresh_market_positions.py` | `/root/clarity/refresh_market_positions.py` |
| DB-Backup | `backup_clarity_db.py` | `/root/clarity/backup_clarity_db.py` |

## Fachliche Zuordnung

| Funktion | Hauptdateien |
|---|---|
| Auth, Sessions, PIN, Admin | `rove_app_api.py` |
| App-State und Finanzaggregation | `rove_app_state.py` |
| Ausgabenwahrheit | `rove_expense_domain.py` |
| Finanzkonten | `rove_financial_accounts.py` |
| Investmentbeitraege | `rove_investment_contributions.py` |
| Score | `rove_score.py` |
| Crypto- und Marktwerte | `rove_market_data.py`, `refresh_market_positions.py` |
| Feature Announcements | `rove_feature_announcements.py` |
| Telegram-Interaktion | `bot.py` |
| Reports | `report_engine.py`, `report_story_v2.py`, `report_html_renderer.py`, `rove_web_report_renderer.py` |
| Reporttemplates | `report_templates/` |
| Account-Loeschung | `rove_account_delete_cleanup.py` |

## Kritische Daten

- Produktive SQLite-Datenbank: `/root/clarity/clarity.db`
- Automatische Backups: `/root/clarity/backups/automatic`
- Secrets: `/root/clarity/.env` und separate serverseitige Environment-Dateien
- Web-Reports und PDFs: durch Worker verwaltete Server-Ausgaben, nicht Git-Source

## Tests und Migrationen

- Tests liegen aktuell als `test_*.py` im Repository-Root.
- Migrationen und Reparaturskripte liegen aktuell ebenfalls im Root.
- Beide Gruppen werden erst in einer spaeteren, separat geprueften Phase
  verschoben, damit Imports und bekannte Betriebsbefehle stabil bleiben.
- Testinventar und Runner: [docs/TESTING.md](docs/TESTING.md) und
  `scripts/test.sh`.
- Script- und Migrationsinventar: [docs/SCRIPTS.md](docs/SCRIPTS.md) und
  [docs/MIGRATIONS.md](docs/MIGRATIONS.md).
- Laufzeit- und Paketabhaengigkeiten: [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).
- Deployment-, Runtime- und Recovery-Foundation: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Nicht-kanonische Kopien

- Root-`index.html` und `rove-app/index.html` sind keine aktive Frontendquelle.
- `index.STAND-*` und `index.ROLLBACK-*` sind historische Recovery-Artefakte.
- Lokale Datenbanken, Reports, Caches und Backups sind keine Source-Dateien.
