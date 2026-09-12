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

### Wealth Chart V2

`rove_app_state.build_live_app_data()` supplies `chartV2` alongside the legacy
series contract. Its points carry exact euro values, IDs, timestamps, source
and coverage scope. Intraday values are reconstructed from the current total
and remaining committed expense, income/fixed and market-valuation events;
they are not recorded observations or browser snapshots. Deleting an event
reconstructs subsequent points without that effect. Transfers and expense
cash mirrors are excluded. Unlogged balance corrections are reflected in the
current anchor, not invented as timed events. The day follows the server's
existing calendar/time convention.

Long ranges reuse the existing reconstruction and immutable monthly snapshots
without kEUR rounding. Property-excluded reconstructions, full current totals
and snapshots with unproven comparable coverage are separate line segments.
The displayed delta sums only comparable adjacent intervals; coverage gaps
contribute no claimed performance. This does not backfill property equity.
`coverage_started_at` bounds today's comparable reconstruction when applicable.

Frontend `normalizeChartSeriesV2` and `buildRangeSeriesV2` are pure. Local
netHistory remains a legacy/profile comparison path and cannot override a
received V2 contract. Refresh callers share a queued read, so a mutation during
a request waits for a subsequent read. The existing renderer consumes the
result, using a deterministic padded domain with a 5% minimum span (at least
EUR 1,000) and separate paths at coverage gaps. No new persistence table.

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
