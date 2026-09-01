# Rov.E

Dieses Repository ist die kanonische Source of Truth fuer die Rov.E Web-App,
das Flask-Backend, den Telegram-Bot und die produktiven Worker. Es enthaelt
keine produktive Datenbank und keine Secrets.

## Repository

- `frontend/`: aktive statische PWA inklusive Manifest, Service Worker und Icons
- `rove_app_api.py`: Flask API sowie Auth-, PIN-, Admin-, AI- und Push-Endpunkte
- `rove_app_state.py`: kanonische Aggregation des App- und Finanzzustands
- `bot.py`: weiterhin produktiver Telegram-Bot
- `rove_*.py`: Domainmodule und Worker
- `report_*.py`, `report_templates/`: Reportaufbereitung und Rendering
- `test_*.py`: bestehende Test-Suite
- `deploy/`: bereits versionierte Systemd- und Nginx-Bestandteile
- `docs/`: vertiefende Produkt- und Entwicklungsdokumentation

Die aktiven Python-Kernmodule bleiben vorerst im Repository-Root. Sie werden
nicht allein aus Ordnungsgruenden verschoben, weil Imports und produktive
Systemd-Pfade davon abhaengen.

## Wichtige Dokumente

- [PROJECT_MAP.md](PROJECT_MAP.md): Funktionen, Dateien und Entry Points
- [ARCHITECTURE.md](ARCHITECTURE.md): System- und Datenfluss
- [RUNBOOK.md](RUNBOOK.md): Deploy, Diagnose, Backup und Restore
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): reproduzierbarer Serveraufbau und Runtime-Inventar
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md): Variablen-, Secret- und Default-Inventar
- [CHANGELOG.md](CHANGELOG.md): kanonisch gepflegte Aenderungen
- [docs/TESTING.md](docs/TESTING.md): Tests, Teilmengen und bekannte Baseline
- [docs/SCRIPTS.md](docs/SCRIPTS.md): Runtime-, Operations- und Legacy-Inventar
- [docs/MIGRATIONS.md](docs/MIGRATIONS.md): Migrationen und Wiederholbarkeit
- [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md): Laufzeiten und Abhaengigkeiten
- [docs/PROJECT_RULES.md](docs/PROJECT_RULES.md): dauerhafte Repository- und Ownership-Regeln

## Lokale Pruefungen

Python-Syntax fuer eine geaenderte Datei:

```bash
python3 -m py_compile DATEI.py
```

Gesamte bestehende Test-Suite:

```bash
bash scripts/test.sh full
```

Gezielte Teilmengen sind `auth`, `finance`, `frontend`, `reports` und
`stability`. Details und die aktuelle Testbaseline stehen in
[docs/TESTING.md](docs/TESTING.md).

JavaScript aus dem monolithischen Frontend muss vor einem Frontend-Deploy mit
`node --check` geprueft werden. Die statischen Assets werden relativ zu
`frontend/index.html` geladen und muessen gemeinsam ausgeliefert werden.

## Laufzeiten und Abhaengigkeiten

Produktion verwendet Python 3.12. Die API laeuft in
`/root/rove-app-api-venv`, Bot und mehrere Worker verwenden `/usr/bin/python3`.
Diese Laufzeiten sind aktuell absichtlich noch nicht vereinheitlicht. Die
vorhandene `requirements-auth.txt` ist keine vollstaendige Abhaengigkeitsliste.
Das aktuelle Inventar und die empfohlene spaetere Requirements-Struktur stehen
in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Sicherheit

Folgende Dateien gehoeren niemals in Git: `.env*`, `*.db*`, Logs, Tokens,
Zertifikate und produktive Backups. Beispielkonfigurationen duerfen nur Namen
und Platzhalter enthalten.

## Telegram status

Der Telegram-Bot ist in Produktion voruebergehend `STOPPED FOR OBSERVATION`.
Die App, App-Reports und die systemd-Worker laufen unabhaengig weiter. Bot-Code
und historische Telegram-Daten werden in dieser Beobachtungsphase nicht
geloescht.

Vor Änderungen zuerst [docs/PROJECT_RULES.md](docs/PROJECT_RULES.md),
`PROJECT_MAP.md` und den passenden Domain-Owner pruefen. Nicht aus alten
`work/`-Kopien deployen.
