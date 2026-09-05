# Rov.E Architecture

Stand: 31.08.2026.

## Systemfluss

```text
Browser / installierte PWA
        |
        | HTTPS
        v
Nginx
  |-- /app/     -> statische Dateien unter /var/www/getrove/app
  `-- /app-api/ -> Flask API
                         |
                         v
                    SQLite DB
                         ^
                         |
        Telegram-Bot und Systemd-Worker
```

## Browser und PWA

Die App ist eine statische, mobile Web-App. CSS und JavaScript liegen
ueberwiegend in `frontend/index.html`. Das Manifest und die Icons liegen im
gleichen Verzeichnis. `frontend/sw.js` verarbeitet Push-Nachrichten, besitzt
aber bewusst keinen Fetch-Handler und kann deshalb keine alte App-Version aus
einem Offline-Cache ausliefern.

## Nginx und API

Nginx liefert `/app/` statisch aus und leitet `/app-api/` an die Flask API
weiter. Die vollstaendige produktive Nginx-Konfiguration ist noch nicht als
Infrastructure-as-Code im Repository abgebildet.

`rove_app_api.py` stellt die App-Endpunkte bereit. Authentifizierung verwendet
serverseitige Accounts, Cookie-Sessions und einen sessiongebundenen PIN-Guard.
Der Browser entscheidet nicht allein ueber den Zugriff auf Finanzdaten.

## Datenbank

API, Bot und Worker verwenden dieselbe SQLite-Datenbank unter
`/root/clarity/clarity.db`. WAL ist produktiv aktiv. Tabellen und additive
Schemaerweiterungen werden derzeit durch mehrere Runtime-Module und
Migrationsskripte verwaltet; ein einzelnes kanonisches Migrationsledger fehlt
noch.

## Bot und Worker

`bot.py` ist der getrennte Telegram-Entry-Point. Die Produktion befindet sich
aktuell in einer 7-taegigen Stop-Beobachtung; der Code bleibt fuer Rollback
erhalten. App-Reports, Monats- und Tracking-Erinnerungen, Marktwerte und
Datenbankbackups werden durch getrennte systemd-Timer verarbeitet und benoetigen
den Bot-Prozess nicht.

## Reports

Reportdaten entstehen aus der kanonischen Finanzwahrheit. Der aktuelle
Hauptpfad verwendet Story-, HTML- und Web-Renderer; ein ReportLab-Renderer
bleibt als Fallback vorhanden. Templates und produktive Renderer liegen noch
im Root beziehungsweise in `report_templates/`, um Importpfade stabil zu
halten.

## Externe Provider

- OpenAI: serverseitige AI-Antworten und optionale Reporttexte
- CoinMarketCap und weitere Marktdatenquellen: serverseitige Quotes und Metadaten
- Telegram: Bot-Kommunikation
- Web Push: Push-Nachrichten an registrierte Browser

API-Keys und Provider-Secrets werden ausschliesslich serverseitig aus
Environment-Dateien gelesen und niemals an das Frontend ausgeliefert.

## Bekannte technische Grenzen

- Frontend und Backend werden derzeit ueber unterschiedliche Deploy-Wege verteilt.
- API-Venv und System-Python besitzen unterschiedliche Dependency-Saetze.
- Nicht alle Systemd- und Nginx-Dateien sind bereits versioniert.
- Mehrere grosse Root-Module erhoehen die Seiteneffektflaeche von Aenderungen.
