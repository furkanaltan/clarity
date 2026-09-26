# Rov.E Runbook

Stand: 31.08.2026. Befehle mit Root-Rechten werden ausschliesslich kontrolliert
auf dem Produktionsserver ausgefuehrt. Secrets und Datenbankinhalte duerfen
nicht in Terminalausgaben oder Git gelangen.

Der verifizierte Runtime-Aufbau und der Clean-Room-Plan stehen in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Dieses Runbook beschreibt die
operativen Gates, nicht die automatische Installation eines neuen Hosts.

Release-Modi, Freigabekriterien und Rollen sind verbindlich in
[`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md) festgelegt.
Dieses Runbook bleibt die operative Checkliste; ein Branch-Pull allein ist
keine Freigabe. Vor dem Update muessen der explizit freigegebene Commit, die
Commit-Differenz und der Rollback-Stand geprueft sein.

## Produktionspfade

- Backend-Checkout: `/root/clarity`
- Datenbank: `/root/clarity/clarity.db`
- API-Venv: `/root/rove-app-api-venv`
- Statisches Frontend: `/var/www/getrove/app`
- Automatische DB-Backups: `/root/clarity/backups/automatic`

## Aktive Dienste

```text
rove-app-api.service
clarity-bot.service (STOPPED FOR OBSERVATION bis 08.09.2026)
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
und von Furkan gepusht. Der Server wird danach kontrolliert auf den explizit
freigegebenen Commit gebracht; kein pauschaler Branch-Head-Pull ersetzt die
Commit- und Scope-Pruefung. Abhaengig von den geaenderten Entry Points wird
`rove-app-api`, `clarity-bot` oder beides neu gestartet. Ein reiner
Frontend-Deploy benoetigt keinen Service-Neustart.

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

## Telegram-Beobachtung

`clarity-bot.service` bleibt bis zum 08.09.2026 gestoppt. Die Beobachtung
prueft read-only, dass API, App-Reports und die getrennten Worker unabhaengig
laufen. Der Bot darf in dieser Phase weder gestartet noch geloescht werden.

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
Aufbewahrung von exakt 30 Tagen. Manuelle und Release-Backups werden grundsaetzlich
ebenfalls 30 Tage ab Erstellung (`mtime`) aufbewahrt, ausser sie sind ausdruecklich
als aktiver Rollback- oder Recovery-Stand dokumentiert.

Ein vorhandener Dateiname allein beweist kein gueltiges Backup. Pruefungen
muessen SQLite-Integritaet, Dateigroesse und Lesbarkeit einschliessen.

### Retention-Regeln und verifizierter Produktionsstand

- Automatische Backups liegen unter `/root/clarity/backups/automatic` und werden
  nach exakt 30 Tagen rotiert.
- Manuelle und Release-Backups erhalten ohne dokumentierten Ausnahmegrund kein
  unbegrenztes Aufbewahrungsrecht. Das Retention-Ende ist `mtime + 30 Tage`.
- Ein aktiver Rollback- oder Recovery-Stand muss mit Zweck und benoetigter Frist
  dokumentiert sein. Nach Wegfall des Zwecks gilt wieder die regulaere Retention.
- Legacy-DBs bleiben nur erhalten, solange ein dokumentierter Zweck besteht oder
  keine gleichwertige Ersatzkopie fuer Recovery vorhanden ist.
- Sensible DB-Dateien werden restriktiv als `600 root:root` gehalten; sensible
  DB-Verzeichnisse sollen, sofern ohne Betriebsrisiko moeglich, `700 root:root`
  sein.
- `*.db-wal` und `*.db-shm` gehoeren immer zur jeweiligen Haupt-DB-Gruppe und
  werden nicht separat klassifiziert oder freigegeben.
- Account-Delete-Tombstones liegen ausserhalb der SQLite-Backups. Ein Restore
  muss das Tombstone-Ledger vor dem API-Start erneut anwenden und bei fehlendem
  oder ungueltigem Ledger fail-closed abbrechen.
- Diese Dokumentation enthaelt keine personenbezogenen oder finanziellen Inhalte.

Der am 15.09.2026 read-only verifizierte Produktionsstand ist:

- automatischer Backup-Timer aktiv;
- 30-Tage-Rotation wirksam, 32 von 32 automatischen Backups integer;
- drei alte DB-Gruppen inklusive zugehoeriger Sidecars geloescht;
- 22 sensible Alt-DBs auf `600 root:root` gehaertet;
- verbleibende Legacy- und Rollback-Kopien bewusst retained, weil ihr Zweck oder
  ihre Recovery-Relevanz dokumentiert weiter bewertet wird.

Weitere Dateiaktionen erfordern weiterhin eine dateiweise Pruefung von Zweck,
statischen Referenzen, aktiver Nutzung, Integritaet und gleichwertiger Recovery-
Abdeckung. Die obige Verifikation hat keine weiteren Dateien oder Services geaendert.

## Restore-Grundablauf

1. Incident dokumentieren und passenden Backupzeitpunkt bestimmen.
2. Alle DB-schreibenden Dienste und Timer kontrolliert stoppen.
3. Aktuelle defekte DB separat sichern, nicht ueberschreiben oder loeschen.
4. Backup mit korrekten Rechten an den produktiven DB-Pfad kopieren.
5. `reapply_account_delete_tombstones.py` mit dem externen Loeschledger ausfuehren.
6. `PRAGMA integrity_check` und `pragma_foreign_key_check` ausfuehren.
7. Finanzielle Drift-Gates ausfuehren.
8. Dienste schrittweise starten und Healthcheck pruefen.
9. App-, Bot- und Worker-Smoke-Tests durchfuehren.

Der Loeschledger liegt absichtlich ausserhalb der automatischen SQLite-Backups.
Ein wiederhergestellter Account wird vor dem erneuten API-Start daraus erneut
user-scoped entfernt; das ist unabhaengig vom spaeteren Datei-Cleanup.

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
