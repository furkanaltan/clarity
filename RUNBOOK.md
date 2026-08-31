# Rov.E Runbook

Stand: 31.08.2026. Befehle mit Root-Rechten werden ausschliesslich kontrolliert
auf dem Produktionsserver ausgefuehrt. Secrets und Datenbankinhalte duerfen
nicht in Terminalausgaben oder Git gelangen.

Der verifizierte Runtime-Aufbau und der Clean-Room-Plan stehen in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Dieses Runbook beschreibt die
operativen Gates, nicht die automatische Installation eines neuen Hosts.

## Produktionspfade

- Backend-Checkout: `/root/clarity`
- Datenbank: `/root/clarity/clarity.db`
- API-Venv: `/root/rove-app-api-venv`
- Statisches Frontend: `/var/www/getrove/app`
- Automatische DB-Backups: `/root/clarity/backups/automatic`

## Aktive Dienste

```text
rove-app-api.service
clarity-bot.service
rove-monthly-reminders.timer
rove-tracking-reminders.timer
rove-market-refresh.timer
rove-report-worker.timer
rove-report-enqueue.timer
rove-report-maintenance.timer
rove-db-backup.timer
```

## Lokale Tests

Die gesamte bestehende Suite wird ueber einen gemeinsamen Einstieg gestartet:

```bash
bash scripts/test.sh full
```

Gezielte Teilmengen koennen mit `auth`, `finance`, `frontend`, `reports` oder
`stability` ausgefuehrt werden. Der Runner setzt standardmaessig den
kanonischen Frontendpfad. Die aktuelle Testbaseline und lokale
Laufzeitkonfiguration stehen in [docs/TESTING.md](docs/TESTING.md). Fehler
werden nicht ausgeblendet und muessen gegen die dokumentierte Baseline
klassifiziert werden.

## Backend-Deploy

Backend-Aenderungen werden lokal getestet, im kanonischen Repository committed
und gepusht. Erst danach zieht der Server den freigegebenen Branch. Abhaengig
von den geaenderten Entry Points wird `rove-app-api`, `clarity-bot` oder beides
neu gestartet. Ein reiner Frontend-Deploy benoetigt keinen Service-Neustart.

Vor jedem Deploy:

```bash
git status --short
git diff --check
python3 -m py_compile GEAENDERTE_DATEI.py
```

## Frontend-Deploy

Die kanonischen Dateien unter `frontend/` werden gemeinsam nach
`/var/www/getrove/app/` kopiert. Vorher muss ein Backup der dort vorhandenen
Dateien erstellt und der lokale Hash dokumentiert werden. Nach dem Upload wird
der Serverhash verglichen. Es wird kein API-Service neu gestartet.

## Healthcheck

```bash
systemctl is-active rove-app-api
systemctl is-active clarity-bot
curl -s https://getrove.de/app-api/health
```

Erwartung: benoetigte Services sind `active`, der Healthcheck liefert
`"ok": true`.

## Logs und Diagnose

```bash
journalctl -u rove-app-api -n 100 --no-pager
journalctl -u clarity-bot -n 100 --no-pager
systemctl list-timers --all --no-pager | grep rove-
```

Logs duerfen keine Secrets, Cookies, PINs oder vollstaendigen Finanzexports
enthalten.

## Backup

`rove-db-backup.timer` erstellt taegliche SQLite-Backups mit einer vorgesehenen
Aufbewahrung von 30 Tagen. Vor einem risikobehafteten Daten- oder Schemaeingriff
wird zusaetzlich ein separates, zugriffsgeschuetztes Backup angelegt.

Ein vorhandener Dateiname allein beweist kein gueltiges Backup. Pruefungen
muessen SQLite-Integritaet, Dateigroesse und Lesbarkeit einschliessen.

## Restore-Grundablauf

1. Incident dokumentieren und passenden Backupzeitpunkt bestimmen.
2. Alle DB-schreibenden Dienste und Timer kontrolliert stoppen.
3. Aktuelle defekte DB separat sichern, nicht ueberschreiben oder loeschen.
4. Backup mit korrekten Rechten an den produktiven DB-Pfad kopieren.
5. `PRAGMA integrity_check` und `pragma_foreign_key_check` ausfuehren.
6. Finanzielle Drift-Gates ausfuehren.
7. Dienste schrittweise starten und Healthcheck pruefen.
8. App-, Bot- und Worker-Smoke-Tests durchfuehren.

Ein Restore wird nicht improvisiert und niemals auf Basis einer lokalen
Entwicklungsdatenbank durchgefuehrt.

## Rollback

- Backend: nur auf einen vorher verifizierten Commit wechseln und danach die
  betroffenen Dienste neu starten.
- Frontend: zuvor gesicherte statische Dateien atomar wiederherstellen; kein
  Service-Neustart erforderlich.
- Datenbank: nur ueber den oben beschriebenen Restore-Ablauf.

Destruktive Git-Befehle wie `git reset --hard` oder ein pauschales `git clean`
sind kein normaler Rollback-Prozess.

## Secrets

Produktive `.env`-Dateien, API-Keys, Tokens, Zertifikate, Sessiondaten und
Datenbanken bleiben ausserhalb von Git. Versionierte Konfigurationsbeispiele
enthalten ausschliesslich Variablennamen und Platzhalter.
