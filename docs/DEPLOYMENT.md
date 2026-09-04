# Rov.E Deployment and Recovery Foundation

The complete variable-by-variable environment inventory is maintained in
[`docs/ENVIRONMENT.md`](ENVIRONMENT.md). The deployable placeholder template is
`deploy/env.example`; it intentionally contains no production secrets.

Stand: 31.08.2026. Dieses Dokument wurde aus einer read-only Inventur der
Produktion und dem kanonischen Repository erstellt. Es ist kein freigegebener
Deploy-Lauf. Befehle und Templates werden erst nach einem separaten Gate auf
einem neuen Host oder in Produktion angewandt.

## Zielsystem

- Betriebssystem: Ubuntu 24.04, Linux x86_64
- System-Python: 3.12.3
- Backend-Checkout: `/root/clarity`
- API-Venv: `/root/rove-app-api-venv`
- Datenbank: `/root/clarity/clarity.db`
- Frontend-Root: `/var/www/getrove`
- App-Ziel: `/var/www/getrove/app`
- Report-Ausgabe: `/var/www/reports`
- Automatische Backups: `/root/clarity/backups/automatic`

Die exakte Python-Version innerhalb des API-Venv und dessen Paketversionen
sind `UNKNOWN`: Der read-only SSH-Account kann den Pfad nicht inspizieren.

## Trennung von Code, Konfiguration, Secrets und Daten

| Klasse | Quelle | Ziel | Git |
|---|---|---|---|
| Backend-Code | Repository-Root | `/root/clarity` | Ja |
| Frontend-Code | `frontend/` | `/var/www/getrove/app/` | Ja |
| Unit-Templates | `deploy/systemd/` | `/etc/systemd/system/` | Ja, sanitisiert |
| Nginx-Template | `deploy/nginx/getrove.conf.example` | `/etc/nginx/sites-available/` | Ja, sanitisiert |
| Environment | `deploy/env.example` als Namensmodell | `/root/clarity/.env*` | Nur Beispiel |
| SQLite-Daten | validiertes Backup | `/root/clarity/clarity.db` | Nein |
| Reports/Backups | Server-Laufzeit | Server-Ausgabepfade | Nein |

## Produktionsruntime

`active` beschreibt den read-only Stand der Inventur, nicht eine gewünschte
Installationsaktion.

| Unit | Zweck | Python/ExecStart | Env-Quelle | Benutzer | Restart/Status |
|---|---|---|---|---|---|
| `rove-app-api.service` | Flask API | API-Venv, `rove_app_api.py` | `.env`, `.rove-app-api.env`, optionale Marktdateien | root | always, active |
| `clarity-bot.service` | Telegram-Bot | System-Python, `bot.py` | optionale `.rove-leeway.env`; `bot.py` lädt zusätzlich `.env` | root (Default) | always, active |
| `rove-db-backup.service` | SQLite-Backup, 30 Tage | System-Python, `backup_clarity_db.py` | keine | root (Default) | oneshot |
| `rove-market-refresh.service` | Marktwerte aktualisieren | API-Venv, `refresh_market_positions.py` | Markt-/Leeway-Dateien | root | oneshot |
| `rove-monthly-reminders.service` | Monatscheck-Push | API-Venv, `rove_monthly_reminders.py` | `.rove-app-api.env` | root (Default) | oneshot |
| `rove-tracking-reminders.service` | Tracking-Push | API-Venv, `rove_tracking_reminders.py` | `.env`, `.rove-app-api.env` | root | oneshot |
| `rove-report-enqueue.service` | Reportjobs anlegen | System-Python, `rove_report_worker.py enqueue` | `.env` | root | oneshot |
| `rove-report-worker.service` | Reportjobs verarbeiten | System-Python, `rove_report_worker.py process` | `.env` | root | oneshot |
| `rove-report-maintenance.service` | Reports aufräumen/archivieren | System-Python, `rove_report_worker.py maintain` | `.env` | root | oneshot |

## Timer

| Timer | Zeitplan | Persistent |
|---|---|---|
| `rove-db-backup.timer` | täglich 03:20, Systemzeitzone | Ja |
| `rove-market-refresh.timer` | täglich 22:30 Europe/Berlin | Ja |
| `rove-monthly-reminders.timer` | täglich 09:05 Europe/Berlin | Ja |
| `rove-report-enqueue.timer` | Monatstag 1 um 07:55 und Tag 2 um 08:05 Europe/Berlin | Ja |
| `rove-report-maintenance.timer` | täglich 03:10 Europe/Berlin | Ja |
| `rove-report-worker.timer` | 2 Minuten nach Boot, danach 1 Minute nach Ende | Nein |
| `rove-tracking-reminders.timer` | 3 Minuten nach Boot, danach 10 Minuten nach Ende | Ja |

Alle neun Services und sieben Timer sind unter `deploy/systemd/` als
sanitizierte Templates abgebildet. Produktion nutzt bei API und Bot teilweise
Drop-ins; die Repository-Templates bilden deren effektive EnvironmentFile-
Struktur zusammengeführt ab.

## Nginx und öffentliche Pfade

- Öffentliche App: `https://getrove.de/app/`
- Statisches Root: `/var/www/getrove`
- API-Prefix: `/app-api/`
- Upstream: `http://127.0.0.1:5057/`
- Reports: `/reports/` aus `/var/www/reports/`, privat gecacht für 300 Sekunden
- Legacy-App-State: `/app-state/` liefert `410`
- TLS: Certbot-Pfade; Zertifikate und Schlüssel werden nicht aus Git erzeugt

Die Produktion besitzt keine besondere Nginx-Cache-Regel für `/app/`. Der
Service Worker hat keinen Fetch-Handler. Nach Frontend-Releases sind deshalb
Dateihashes und PWA-Neuladung zu prüfen; ein Service-Neustart ist nicht nötig.

Das Template bildet nur Rov.E-App, API und Reports ab. Das separate Cockpit
mit Basic Auth ist absichtlich nicht enthalten.

## Python Dependencies

Die Struktur unter `requirements/` trennt API, Bot, Reports und Entwicklung.
Gepinnte Versionen stammen aus dem produktiven System-Python. API-Pakete sind
ungepinnt, solange das aktive API-Venv nicht read-only inventarisiert werden
kann. Ein lokales Mac-`pip freeze` ist keine Produktionswahrheit.

| Bereich | Verifizierte Produktionsversionen |
|---|---|
| Core | `python-dotenv 1.2.2` |
| Bot | `APScheduler 3.11.2`, `openai 2.38.0`, `pyTelegramBotAPI 4.33.0` |
| Reports | `Jinja2 3.1.2`, `Pillow 12.2.0`, `reportlab 4.1.0`, `WeasyPrint 69.0` |
| API | Paketnamen durch Imports belegt; Versionen `UNKNOWN` |

## Native Dependencies

Read-only verifiziert:

- SQLite CLI 3.45.1
- WeasyPrint CLI 69.0
- Cairo 1.18.0
- Pango/PangoCairo 1.52.1
- GDK Pixbuf 2.42.10
- libffi 3.4.6
- DejaVu-Fonts vorhanden; 177 Fontconfig-Einträge
- Node.js ist auf Produktion nicht installiert und nur für lokale Frontendtests nötig

Weitere beobachtete Pakete stehen in `docs/DEPENDENCIES.md`. Native Pakete
werden nicht allein aus einer Python-Requirements-Datei installiert.

## Environment-Modell

`deploy/env.example` enthält ausschließlich Namen und sichere Platzhalter.
Besonders schützenswert sind Auth-, Telegram-, E-Mail-, AI-, Marktdaten-,
VAPID- und interne Push-Secrets. Defaults für Limits, Pfade und Copy sind dort
kenntlich gemacht. Reale Werte dürfen weder in Git noch in Diagnoseausgaben.

Produktiv beobachtete Environment-Dateien:

- `/root/clarity/.env`
- `/root/clarity/.rove-app-api.env`
- `/root/clarity/.rove-market-data.env`
- `/root/clarity/.rove-leeway.env`

Die Inhalte wurden nicht gelesen.

## Clean-Room-Plan

Die folgenden Schritte sind ein Plan für einen neuen Host, kein aktuell
auszuführender Produktionsbefehl:

1. Ubuntu-Host, DNS, TLS und restriktiven administrativen Zugang bereitstellen.
2. Backend-, Frontend-, Report- und Backup-Verzeichnisse mit passenden Rechten anlegen.
3. Kanonischen Branch nach `/root/clarity` auschecken.
4. System-Python 3.12 und `/root/rove-app-api-venv` bereitstellen.
5. Requirements pro Runtime installieren; native Reportpakete und Fonts separat bereitstellen.
6. `deploy/env.example` in getrennte, nicht versionierte Environment-Dateien überführen.
7. Validiertes SQLite-Backup wiederherstellen; niemals die lokale Entwicklungs-DB verwenden.
8. Produktionsschema und bereits angewandte Migrationen gegen `docs/MIGRATIONS.md` inventarisieren.
9. Nur fehlende additive Migrationen nach eigenem Gate ausführen.
10. Sanitizierte systemd-Templates gegen den Zielhost prüfen und installieren.
11. Nginx-Site und TLS bereitstellen; Konfiguration vor Aktivierung validieren.
12. `frontend/` vollständig nach `/var/www/getrove/app/` übertragen.
13. Erst API, dann abhängige Worker/Timer und zuletzt den Bot kontrolliert aktivieren.
14. Healthcheck, Integrität, Foreign Keys, Finance-Drift und isolierte Smoke-Tests ausführen.
15. Backup-Timer und einen dokumentierten Restore-Test verifizieren.

## Datenbank und Migrationen

- Produktive DB: `/root/clarity/clarity.db`
- Backup-Ziel: `/root/clarity/backups/automatic`
- Produktiv wurde WAL beobachtet; Restore muss DB, `-wal` und `-shm` kontrolliert behandeln.
- Neun Migrationen sind in `docs/MIGRATIONS.md` inventarisiert.
- Produktionsstatus und sichere globale Reihenfolge bleiben `UNKNOWN`.

Es gibt kein kanonisches Migrationsledger. Deshalb darf ein Clean-Room-Aufbau
nicht alle Skripte blind in Dateinamensreihenfolge ausführen. Zuerst wird ein
Backup wiederhergestellt, dann werden Schema und Migrationseffekte read-only
inventarisiert. Jede fehlende Migration braucht ein separates Gate.

Restore-Grundsatz: alle DB-schreibenden Dienste anhalten, defekten Stand
separat sichern, validiertes Backup wiederherstellen, `integrity_check`,
`foreign_key_check` und fachliche Drift-Gates ausführen und Dienste gestaffelt
starten. Die operative Checkliste steht in `RUNBOOK.md`.

## Frontend-Deploy-Modell

SOURCE:

- `frontend/index.html`
- `frontend/sw.js`
- `frontend/manifest.webmanifest`
- `frontend/app-icon.png`
- `frontend/logo.png`

TARGET: `/var/www/getrove/app/`

Vor einer Veröffentlichung werden JavaScript, Full Suite, Diff und Hashes
geprüft. Das aktuelle `frontend/index.html` hat den kanonischen SHA-256
`222d034182001586ca1079d661c5d2919208a57fccbd9f483bd17dfd6df02d32`.
Die fünf Dateien werden als Satz gesichert und veröffentlicht. Danach werden
Serverhashes und PWA-Verhalten geprüft. Kein Backend-Service wird neu gestartet.

Die Nginx-Konfiguration ist derzeit nicht kanonisch im Repository versioniert.
Für die Live-Installation müssen deshalb ausserhalb dieses Repositories
Revalidierungs-Header für die Dokument- und Update-Dateien gesetzt werden:

```nginx
location = /app/ {
    add_header Cache-Control "no-cache, must-revalidate" always;
}
location = /app/index.html {
    add_header Cache-Control "no-cache, must-revalidate" always;
}
location = /app/sw.js {
    add_header Cache-Control "no-cache, must-revalidate" always;
}
location = /app/manifest.webmanifest {
    add_header Cache-Control "no-cache, must-revalidate" always;
}
```

Diese Live-Anpassung ist kein Repository-Deploy und darf nur nach separater
Nginx-Pruefung angewendet werden. Der Service Worker bleibt ohne `fetch`-Handler.

## Backend-Deploy-Modell

- Local Source: kanonisches Repository
- Server Target: `/root/clarity`
- Branch: `feature_clarityr-report`
- API Service: `rove-app-api.service`
- API Venv: `/root/rove-app-api-venv`
- Bot Service: `clarity-bot.service`
- Datenbank: `/root/clarity/clarity.db`, niemals über Git

Code wird per Git verteilt. Konfiguration und Secrets bleiben in geschützten
Environment-Dateien. Daten und generierte Reports bleiben außerhalb des
Repositorys. Welche Services neu gestartet werden, ergibt sich ausschließlich
aus den geänderten Entry Points; ein Frontend-Only-Release braucht keinen
Service-Neustart.

## Healthcheck und Rollback

Nach einer späteren, freigegebenen Installation werden mindestens geprüft:

- API- und Bot-Service aktiv
- API-Health liefert `ok: true`
- alle Timer geladen und erwartungsgemäß wartend
- SQLite-Integrität und Foreign Keys sauber
- Finance- und Investment-Drift 0
- Frontend- und Asset-Hashes stimmen

Rollback trennt Code, Frontend und Daten: Backend auf einen verifizierten
Commit zurückführen, statische Dateien aus dem Release-Backup wiederherstellen
und die DB nur über den kontrollierten Restore-Prozess anfassen.

## Rebuild Gap Analysis

| Bereich | Status | Offene Lücke |
|---|---|---|
| Kanonische Quellen und Zielpfade | READY | Keine bekannte Lücke |
| Frontend-Dateisatz | READY | Automatisches Release-Artefakt fehlt |
| systemd-Templates | READY | Installation auf Clean Host ungeprüft |
| Nginx-App/API-Template | READY | Certbot-/TLS-Bootstrap ungeprüft |
| Environment-Namen | READY | Reale Werte liegen absichtlich nur auf Produktion |
| System-Python Requirements | READY | Installation in frischem Venv ungeprüft |
| API Requirements | PARTIAL | Aktive Venv-Versionen `UNKNOWN` |
| Native PDF-Abhängigkeiten | PARTIAL | Vollständiger Neuinstallationslauf ungeprüft |
| DB-Backup/Restore | PARTIAL | Kein automatisierter Restore-Test |
| Migrationen | UNKNOWN | Kein Ledger und Produktionsstatus unbekannt |
| Vollständiger Clean-Room-Rebuild | PARTIAL | Noch nie auf leerem Host validiert |

## Freigabegrenzen

Diese Foundation verändert keine Produkt-, Finanz-, Auth-, Bot-, Report- oder
Frontendlogik. Vor jeder Installation sind ein eigener Review, Secret-Scan,
Testlauf und eine explizite Produktionsfreigabe erforderlich.
