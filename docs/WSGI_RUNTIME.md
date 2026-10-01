# Rov.E WSGI Readiness

Stand: 01.10.2026. Review-Basis:
`6953a341a14c32f12b5a7f1685cde5410ec5d2a5` (SQLite-Fix committed und gepusht).
SQLite-/Runtime-Gate: **PASS**. WSGI-Review: **GO**.
Commit/Push: **GO mit bekanntem Test-Risiko** (sechs unveraenderte Alt-Testfehler,
unten dokumentiert). Kein uneingeschraenktes Gesamt-Test-PASS.
Production-Runtime-Deploy bleibt **NICHT FREIGEGEBEN**. Kein Commit, Push,
Deploy, Production-Zugriff oder echter Mailversand wurde fuer diesen WSGI-Block
ausgefuehrt. Der separate SQLite-Commit bleibt unveraendert.
Technische Readiness fuer den anschliessenden Runtime-Switch: **GO mit Risiko**,
vorbehaltlich frischem Operator-Precheck, Ubuntu-Unit-Pruefung und Live-Smokes.
Das ist keine Ausfuehrungsfreigabe und kein 10.000-Nutzer-Kapazitaetsnachweis.

## Architektur und Runtime-Entwurf

- `rove_app_api.py` exportiert `app = Flask(__name__)`. Der bisherige
  Production-Einstieg startet unter `__main__` den Flask Development Server.
- `rove_app_wsgi:app` verwendet dieselbe App ohne `app.run()`. Die bisherigen
  drei Startup-Ensures sowie die separat freigegebene State-Schema-Vorbereitung
  laufen in `prepare_runtime_schema()` vor dem ersten Request. Eine fehlende
  DB blockiert den Start, statt eine leere Produktionsdatenbank anzulegen.
- Gunicorn 26.2.2 ist ueber das offizielle Release-Archiv mit SHA-256 gepinnt
  (`requirements/wsgi.txt`, eingebunden durch `requirements/api.txt`). Der hier
  erreichbare Paketindex bietet diese Patch-Version noch nicht an. Die
  Installation direkt aus der gepinnten Requirements-Datei wurde isoliert
  unter Python 3.12.14 geprueft; keine bestehenden API-Pakete wurden aktualisiert.
- Entwurf: ein `gthread`-Worker mit vier Threads und 64 Verbindungen. Mehrere
  Worker sind nicht freigegeben: Auth-/Rate-Limit-Buckets liegen im
  Prozessspeicher. Kein Preload, kein Reload, kein automatisches Recycling,
  kein zusaetzlicher Gunicorn-Control-Socket.
- Bind bleibt `127.0.0.1:5057`; die vorhandene Port-Variable bleibt wirksam.
  Nginx, CORS und Browser-Schutzheader werden nicht veraendert.
- SQLite-Verbindungen werden pro Zugriff geoeffnet und geschlossen,
  Foreign Keys bleiben aktiv, Busy-Timeout bleibt 15 Sekunden. Finanzielle
  Mutationen verwenden weiterhin die bestehenden Transaktionen.
- API-Import startet keine Scheduler. Reports, Maintenance, Marktwerte und
  Reminder bleiben in ihren bestehenden separaten Services/Timern.
  Keine Streaming-/SSE-Route wurde gefunden.
- Vorhandene externe Timeouts: Brevo 10 Sekunden, AI standardmaessig
  12 Sekunden, Screenshot-Import 40 Sekunden. Gunicorn `timeout=120` ist bei
  `gthread` ein Worker-Liveness-Limit, kein einzelnes Request-Zeitlimit.
- Shutdown-Entwurf: `graceful_timeout=90`, systemd `TimeoutStopSec=105`,
  `KillMode=mixed`. `RuntimeDirectory=rove-app-api` stellt das private
  Heartbeat-Verzeichnis bereit. Restart-Policy und EnvironmentFiles bleiben
  unveraendert. Ein Neustart ist nicht automatisch unterbrechungsfrei.
- Standard-Logs gehen nach journald. Neue Access-Logs enthalten nur Zeit,
  Prozess, Methode, HTTP-Status und Dauer; keine URLs, Querystrings, Cookies,
  Auth-Header, Bodies oder Client-Adressen. Runtime-Ausnahmen werden auf den
  Exception-Typ und Stack-Frames mit Dateiname, Zeile und Funktion reduziert;
  keine Quelltextzeilen, lokalen Variablen, vollstaendigen Pfade oder
  Exception-Payloads. Der zuvor vollstaendig entfernte Traceback bleibt damit
  diagnostizierbar. Malformed HTTP-Diagnosen geben keine Headerwerte aus.
  Ein gemeinsamer Root-Handler erfasst auch Modul-Logs; Gunicorn-Logger
  propagieren nicht und erzeugen keine doppelten Access-/Error-Logs.

## Root Cause: paralleler State-Lesepfad

Vor dem Read-Lifecycle-Fix auf synthetischen Daten reproduzierbar, auch mit
vorbereitetem Schema und WAL:

1. `GET /v1/state` startet einen Savepoint und baut den State auf.
2. `build_live_app_data()` ruft `ensure_app_properties_table()` auf.
3. Diese bestehende Ensure-Funktion fuehrt `UPDATE app_properties` aus, auch
   wenn kein Backfill mehr erforderlich ist.
4. Mehrere Reader versuchen dadurch, ihre Transaktion zu einem Writer zu
   machen. SQLite meldet `OperationalError: database is locked`; HTTP 500.
5. Das abschliessende Rollback verhindert persistierte Finance-Aenderungen,
   verhindert aber nicht den erforderlichen Schreiblock.

Call Chain: `current_app_state -> build_live_app_data ->
ensure_app_properties_table`. Derselbe Fehler wurde vor dem separat
freigegebenen Fix mit dem unveraenderten API-Quelltext aus Basis `e75bcf2`
reproduziert. Die folgenden Ergebnisse sind historische Fehlernachweise:

| Lauf | Bedingungen | Ergebnis |
|---|---|---|
| Gunicorn 1 | 24 gemischte Reads, 8 Clients, vorbereitetes Schema, WAL | 16 HTTP 200 / 8 Fehler |
| Gunicorn 2 | identischer Aufbau | 15 HTTP 200 / 9 Fehler |
| Gunicorn 3 | identischer Aufbau | 16 HTTP 200 / 8 Fehler |
| Basis e75bcf2 | unveraenderte API, parallele Flask-Requests, dieselbe DB | 14 HTTP 200 / 10 SQLite-Lock-Fehler |

### Bedeutung und korrekte Lifecycle-Zuordnung

Die beiden Property-Updates sind bestehende Compatibility-Backfills:
`coverage_started_at` wird nur bei NULL auf den tatsaechlichen Rollout-/
Vorbereitungszeitpunkt gesetzt, nie auf ein erfundenes Kaufdatum.
`coverage_equity_at_start` friert das bekannte Eigenkapital einmalig auf
`ROUND(market_value - remaining_debt, 2)` ein. Bestehende Werte bleiben erhalten.
Die Regeln selbst sind unveraendert; sie laufen nun beim Startup und weiterhin
auf bestehenden expliziten Schreibpfaden, nicht beim State-GET.

Weitere Mutationen desselben Call Graphs waren Debt-Status-Normalisierung,
Schema-/Index-Ensures, Mentor-Event-Upserts/Seen/Resolved und die erstmalige
Monatsabschluss-Einschreibung. `prepare_state_read_schema()` nutzt fuer die
Schema-/Compatibility-Vorbereitung ausschliesslich die bestehenden Ensures.
Startup aktiviert keine faelligen Sparraten und bucht keine ETF-Ausfuehrung.
First-Use-/Mentor-Metadaten bleiben im geschuetzten Schreibpfad; bei GET wird
derselbe DTO ohne Speicherung berechnet. Das entspricht der bisherigen
GET-Semantik, deren Metadaten am Ende zurueckgerollt wurden.

State- und Transactions-GET verwenden fuer PIN/Auth und Handler
`StateReadConnection` mit SQLite `mode=ro` und `query_only=ON`. Die Verbindung
filtert oder verschluckt keine SQL-Befehle: ein unerwarteter Schreibversuch
wird von SQLite abgewiesen. Ein Savepoint haelt nur den konsistenten
Read-Snapshot; kein Rollback-after-write ist mehr erforderlich.

Unvorbereitetes Schema wird nicht im GET repariert. Tests/Einbettungen muessen
dieselbe kanonische Startup-Vorbereitung ausfuehren wie beide Server-Einstiege.
Formeln, Salden, Fixed-Cost-Reconciliation, historische Snapshots und
Due-Processing-Regeln wurden nicht veraendert.

Kein hoeherer Timeout, kein Retry, kein Request-Mutex, keine Sleeps und keine
Reduktion auf einen Thread wurden eingefuehrt. Der vorhandene SQLite-Journal-
Modus bleibt unveraendert. Der Writer-Lock-Regressionstest besteht sowohl mit
DELETE-Journal als auch mit WAL.

### Benachbarte Read-Endpunkte

Transactions-GET ist ebenfalls strikt schreibfrei. Die untersuchten Report-
HTML/PDF-, VKS-Case/PDF- und Settings-Reads rufen den State-/Property-Backfill-
Lifecycle nicht auf. Sie laufen ueber ihre bisherigen Gates; vorhandene
Session-/PIN-Aktivitaetsupdates und lokale Schema-Ensures dieser anderen
Endpunkte bleiben unveraendert. Es wird keine allgemeine Schreibfreiheit
saemtlicher GET-Routen behauptet.

## Validierung

Python 3.12.14, echtes Gunicorn 26.2.2 auf Loopback mit temporaerer SQLite-DB.
Alle Daten, Sessions, Reports und Transport-Secrets im Test sind synthetisch.
Login-Mailtransport ist gemockt; VKS-Mailversand ist deaktiviert.

- Erneuter finaler fokussierter Lauf: **468 Tests PASS**, 0 Fehler, 0 Skips.
  Enthalten: Auth/PIN, State, Cashflow/Dates/Financial Truth, ETF, Monatsplan,
  Buffer/Score, Reports/Retention, VKS/Webhook, Frontend-Settings/VKS,
  Feature-Announcements/Visible-Coach und Runtime.
- Enthalten: 12 SQLite-Lifecycle-Tests und 25 Runtime-Tests. Zusaetzliche
  Runtime-Tests pruefen redigierte Modul-Logs ohne doppelte Handler und
  gemeinsam genutzte PIN-Rate-Limit-Buckets in vier Request-Threads. Der
  Exception-Test verlangt nun sichtbare redigierte Stack-Frames.
- Separater Lauf: **36 Tests PASS** (24 Runtime + 12 Lifecycle).
- Zusaetzlicher finaler Report-/Runtime-Lauf mit bestehenden Report-Requirements:
  **54 Tests PASS** (29 Renderer + 25 Runtime), WeasyPrint 69.0,
  ReportLab 4.1.0, Pillow 12.2.0. WeasyPrint fehlte zunaechst im isolierten
  Python-3.12-Umfeld; Installation nur in temporaere Test-Dependencies,
  keine Aenderung bestehender Requirements oder Production-Pakete.
  Native Fontconfig meldete nicht schreibbare lokale Cache-Verzeichnisse;
  PDF-Tests und Runtime bestanden trotzdem, kein nativer Crash.
- Release-Archiv erneut heruntergeladen: SHA-256 exakt
  `fab2c19817acf7ad61d405584feef928d4231a17374ae8ffa92b82ee43deed7c`.
  Neue isolierte Installation aus `requirements/wsgi.txt`: Gunicorn 26.2.2,
  identischer Source-Hash laut Installationsmetadaten. Der Pin fixiert
  Source-Bytes, nicht bitidentische Wheels/Build-Tool-Versionen. Gunicorn ohne
  Extras benoetigt keine zusaetzlichen Runtime-Pakete.
- Drei unabhaengige Gunicorn-Master-/Workerstarts mit je 24 parallelen
  authentifizierten State-GETs und zwei isolierten synthetischen Nutzern:
  **24/24**, **24/24**, **24/24** HTTP 200; 0 Lock-Fehler, 0 HTTP 500.
  Unveraendert: Gunicorn 26.2.2, ein Worker, vier Threads. Kein HTTP-Retry.
- SQL-Trace plus SQLite-Authorizer erfassen auch den PIN/Auth-Zugriff:
  State- und Transactions-GET fuehren keine DML/DDL-Schreiboperation aus.
  Der logische Snapshot der gesamten Test-DB bleibt unveraendert.
- Zusaetzlicher direkter Vergleich mit dem State-Quelltext aus `e75bcf2`:
  gesamter State-DTO auf reichhaltigen synthetischen Daten identisch.
- Neue kanonische Basis-DB, Legacy-Property-Schema und erneut vorbereitete DB:
  PASS. Bestehende Coverage-Zeiten/Eigenkapitalwerte bleiben erhalten.
  Faellige Spar-/ETF-Verarbeitung bleibt GET-frei und im geschuetzten POST
  weiterhin genau einmal wirksam.
- PASS: minimaler Health-Payload, CORS, Passwort-Login, parallele einmalige
  Login-Code-Einloesung, sequentielle isolierte State-Reads, Report HTML/PDF,
  private VKS-PDFs, Webhook-Auth/Unknown-Events, Settings-Praeferenzen,
  Log-Redaktion und Schema-Vorbereitung.
- PASS: laufender Request beendet sich nach SIGTERM erfolgreich, Master und
  Worker enden, neuer Master startet am selben Port ohne alten Worker.
- Finanzzeilen bleiben im Runtime-Read-Test unveraendert.
- Python-Syntax und `git diff --check`: PASS.
- Alle acht WSGI-Dateien einschliesslich untracked Dateien geprueft;
  kein staged Inhalt, SQLite-Commit und Alt-Test-Dateien unveraendert.
  Bash-Syntax aller Operator-Bloecke: PASS (keine Ausfuehrung).
- Ubuntu/systemd-Lauf und echte lokale/oeffentliche Production-Healthchecks:
  **NOT RUN**. Kein Kapazitaetsnachweis fuer 10.000 gleichzeitige Nutzer.

### Unveraenderte Alt-Testfehler: explizite Ausnahme

474 Tests wurden im erweiterten Scope entdeckt. Sechs davon wurden erneut
auf einem temporaeren Git-Archiv von `e75bcf2` und im aktuellen Checkout
geprueft: dieselben drei Errors und drei Failures in genau diesen sechs Tests.
Sie wurden aus dem finalen 468er-Lauf explizit ausgeschlossen, nicht
repariert, umgedeutet oder in den Testdateien als Skip markiert:

- `test_mentor_v2.MentorV2Tests.test_frontend_prefers_server_candidate_only_in_bridge`
- `test_feature_announcements_sprint3.FeatureAnnouncementSprintThreeFrontendTests.test_coach_click_reuses_sprint_two_router`
- `test_feature_announcements_sprint3.FeatureAnnouncementSprintThreeServerTests.test_finance_due_blocks_claim_and_feature_can_appear_after_completion`
- `test_feature_announcements_sprint3.FeatureAnnouncementSprintThreeServerTests.test_major_is_claimed_once_and_persists_across_reload_and_devices`
- `test_feature_announcements_sprint3.FeatureAnnouncementSprintThreeServerTests.test_security_precedes_newer_major_and_only_one_is_claimed`
- `test_feature_announcements_sprint3.FeatureAnnouncementSprintThreeServerTests.test_seen_opened_dismissed_completed_and_coach_shown_are_ineligible`

Zwei Assertions verlangen nicht mehr vorhandene Frontend-Quelltextliterale.
Vier Announcement-Fixtures kombinieren festes August-Published-Datum mit
relativem Account-Alter; am aktuellen Testdatum sind diese Features
nach der bestehenden Eligibility-Regel nicht berechtigt. Der unselektierte
Gesamtlauf ist daher nicht gruen. Keine neue Regression dieses Diffs ist
nachgewiesen, aber die Alt-Testschuld bleibt ein ausdrueckliches Review-Risiko.

## Operator-Plan: vorbereitet, NICHT JETZT AUSFUEHREN

**Kein Production-Zugriff wurde ausgefuehrt.** Vor Ausfuehrung benoetigt es
einen separaten Commit-/Push-Auftrag und danach einen ausdruecklichen
Production-Runtime-Switch-Auftrag. Der WSGI-Commit-SHA existiert noch nicht;
der freigegebene volle SHA wird beim Deploy abgefragt, niemals erraten.

Alle folgenden Bloecke sind fuer Bash als root auf dem Server vorgesehen.
Ein Fehler stoppt nur den jeweiligen Subshell-Block, nicht die Root-Sitzung.
Kein Frontend-Deploy, keine Nginx-/Env-Aenderung, kein Bot-/Timer-Neustart.
Die vorhandene Haupt-Unit und vorhandene Drop-ins werden nicht ueberschrieben.
Rueckkehr zum Dev-Server erfordert dadurch keinen Restore unbekannter Units.

### A. PRECHECK: nur lesen, zuerst Ausgabe pruefen

```bash
(
set -euo pipefail
cd /root/clarity
git rev-parse HEAD
git status --short
UNEXPECTED="$(git status --porcelain | grep -Ev '^\?\? \.rove-(app-api|leeway|market-data)\.env$' || true)"
test -z "$UNEXPECTED"
test "$(systemctl is-active rove-app-api.service)" = active
test "$(systemctl is-active clarity-bot.service || true)" = inactive
systemctl show rove-app-api.service -p ExecStart --value | grep -F 'argv[]=/root/rove-app-api-venv/bin/python /root/clarity/rove_app_api.py ;' >/dev/null
test -z "$(systemctl show rove-app-api.service -p ExecStartPre --value)"
test -z "$(systemctl show rove-app-api.service -p ExecStartPost --value)"
test -z "$(systemctl show rove-app-api.service -p ExecStop --value)"
systemctl show rove-app-api.service -p FragmentPath -p DropInPaths -p WorkingDirectory -p User -p EnvironmentFiles -p KillSignal
echo CURRENT_ENTRY=FLASK_DEV_SERVER
test ! -e /etc/systemd/system/rove-app-api.service.d/99-rove-wsgi.conf
PID="$(systemctl show rove-app-api.service -p MainPID --value)"
python3 - "$PID" <<'PY'
import pathlib, sys
items = pathlib.Path('/proc/' + sys.argv[1] + '/environ').read_bytes().split(b'\0')
env = dict(item.decode().split('=', 1) for item in items if b'=' in item)
for key in ('ROVE_VKS_EMAIL_ENABLED', 'ROVE_VKS_LIVE_APPROVED'):
    assert env.get(key, '0').strip().lower() in ('', '0', 'false', 'no', 'off'), key + ': STOP'
assert not env.get('GUNICORN_CMD_ARGS', '').strip(), 'STOP: Gunicorn override configured'
assert env.get('ROVE_APP_API_PORT', '5057') == '5057', 'STOP: unexpected port'
assert env.get('CLARITY_DB_NAME', 'clarity.db') in ('clarity.db', '/root/clarity/clarity.db'), 'STOP: unexpected database'
print('VKS_MAIL=OFF\nLIVE_APPROVED=NO\nRUNTIME_ENV_PRECHECK=PASS')
PY
echo PRECHECK=PASS
)
```

Nur fortfahren, wenn der effektive ExecStart der bekannte Python-Dev-Server
ist, keine unbekannten Start-/Stop-Hooks bestehen und die Haupt-Unit samt
EnvironmentFiles unveraendert zur bestaetigten Production-Konfiguration passt.
Bei Abweichung STOP, nicht die Unit pauschal ersetzen.

### B-F. RELEASE/RUNTIME SWITCH: erst nach separater Freigabe

Dieser Block akzeptiert nur den bekannten Production-Stand `e75bcf2` oder
den separat freigegebenen SQLite-Fix `6953a34`. Andere HEADs brauchen einen
erneut freigegebenen Release-Verlauf. Gunicorn wird als einzige neue Runtime-
Dependency installiert; kein `pip install -r requirements/api.txt` und kein
Upgrade bestehender API-/Report-Pakete. Build-Downloads benoetigen Netzwerk.

```bash
(
set -euo pipefail
trap 'echo RUNTIME_SWITCH=STOP' ERR
cd /root/clarity
PY=/root/rove-app-api-venv/bin/python
read -r -p 'Freigegebenen vollen WSGI-Commit-SHA eingeben: ' TARGET
test "${#TARGET}" = 40
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]
BASE="$(git rev-parse HEAD)"
test "$BASE" = e75bcf2ff2b9d0af4ea71b7dbcb09d78f395af99 || test "$BASE" = 6953a341a14c32f12b5a7f1685cde5410ec5d2a5
UNEXPECTED="$(git status --porcelain | grep -Ev '^\?\? \.rove-(app-api|leeway|market-data)\.env$' || true)"
test -z "$UNEXPECTED"
git fetch origin feature_clarityr-report
test "$(git rev-parse origin/feature_clarityr-report)" = "$TARGET"
test "$(git rev-parse "$TARGET^")" = 6953a341a14c32f12b5a7f1685cde5410ec5d2a5
diff <(git diff --name-only 6953a341a14c32f12b5a7f1685cde5410ec5d2a5 "$TARGET" | sort) <(printf '%s\n' deploy/systemd/rove-app-api.service requirements/api.txt deploy/gunicorn.conf.py docs/WSGI_RUNTIME.md requirements/wsgi.txt rove_app_wsgi.py rove_wsgi_logging.py test_wsgi_runtime.py | sort)
test "$(systemctl is-active rove-app-api.service)" = active
test "$(systemctl is-active clarity-bot.service || true)" = inactive
test "$(systemctl show rove-app-api.service -p WorkingDirectory --value)" = /root/clarity
test -z "$(systemctl show rove-app-api.service -p ExecStartPre --value)"
test -z "$(systemctl show rove-app-api.service -p ExecStartPost --value)"
test -z "$(systemctl show rove-app-api.service -p ExecStop --value)"
systemctl show rove-app-api.service -p ExecStart --value | grep -F 'argv[]=/root/rove-app-api-venv/bin/python /root/clarity/rove_app_api.py ;' >/dev/null
PID="$(systemctl show rove-app-api.service -p MainPID --value)"
python3 - "$PID" <<'PY'
import pathlib, sys
env = dict(item.decode().split('=', 1) for item in pathlib.Path('/proc/' + sys.argv[1] + '/environ').read_bytes().split(b'\0') if b'=' in item)
for key in ('ROVE_VKS_EMAIL_ENABLED', 'ROVE_VKS_LIVE_APPROVED'):
    assert env.get(key, '0').strip().lower() in ('', '0', 'false', 'no', 'off'), key + ': STOP'
assert not env.get('GUNICORN_CMD_ARGS', '').strip(), 'STOP: Gunicorn override configured'
assert env.get('ROVE_APP_API_PORT', '5057') == '5057', 'STOP: unexpected port'
assert env.get('CLARITY_DB_NAME', 'clarity.db') in ('clarity.db', '/root/clarity/clarity.db'), 'STOP: unexpected database'
print('VKS_MAIL=OFF\nLIVE_APPROVED=NO\nRUNTIME_ENV_PRECHECK=PASS')
PY
DROPIN=/etc/systemd/system/rove-app-api.service.d/99-rove-wsgi.conf
test ! -e "$DROPIN"
umask 077
BACKUP="/root/clarity/backups/wsgi-runtime-before-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$BACKUP"
printf '%s\n' "$BASE" > "$BACKUP/server-head"
systemctl show rove-app-api.service -p FragmentPath -p DropInPaths -p ExecStart -p EnvironmentFiles -p KillMode -p TimeoutStopUSec > "$BACKUP/service-before.txt"
printf 'ROLLBACK_DIRECTORY=%s\n' "$BACKUP"
"$PY" - "$BACKUP/clarity.db" <<'PY'
import sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect('file:/root/clarity/clarity.db?mode=ro', uri=True)) as source:
    with closing(sqlite3.connect(sys.argv[1])) as destination:
        source.backup(destination)
        assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print('DB_BACKUP_INTEGRITY=PASS')
PY
git switch --detach "$TARGET"
git diff --check "$BASE..$TARGET"
"$PY" -c 'import sys; assert sys.version_info[:2] == (3,12); print(sys.version)'
"$PY" -m pip install --no-deps -r requirements/wsgi.txt
"$PY" -c 'import gunicorn; assert gunicorn.__version__ == "26.2.2"; print("GUNICORN=26.2.2")'
"$PY" -m py_compile rove_app_wsgi.py rove_wsgi_logging.py deploy/gunicorn.conf.py
systemd-analyze verify deploy/systemd/rove-app-api.service
printf '%s\n' '[Service]' 'ExecStart=' 'ExecStart=/root/rove-app-api-venv/bin/python -m gunicorn --config /root/clarity/deploy/gunicorn.conf.py rove_app_wsgi:app' 'RuntimeDirectory=rove-app-api' 'RuntimeDirectoryMode=0700' 'KillMode=mixed' 'TimeoutStopSec=105' 'StandardOutput=journal' 'StandardError=journal' > "$BACKUP/99-rove-wsgi.conf"
install -d -o root -g root -m 755 /etc/systemd/system/rove-app-api.service.d
install -o root -g root -m 644 "$BACKUP/99-rove-wsgi.conf" "$DROPIN"
sha256sum "$DROPIN" > "$BACKUP/wsgi-dropin.sha256"
systemctl daemon-reload
systemctl show rove-app-api.service -p ExecStart --value | grep -F 'argv[]=/root/rove-app-api-venv/bin/python -m gunicorn --config /root/clarity/deploy/gunicorn.conf.py rove_app_wsgi:app ;' >/dev/null
START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n' "$START" > "$BACKUP/deploy-start"
systemctl restart rove-app-api.service
test "$(systemctl is-active rove-app-api.service)" = active
test "$(systemctl is-active clarity-bot.service || true)" = inactive
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 http://127.0.0.1:5057/health
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 https://getrove.de/app-api/health
test "$(git rev-parse HEAD)" = "$TARGET"
printf '\nSERVER_HEAD=%s\nRUNTIME_SWITCH_HEALTH=PASS\nROLLBACK_DIRECTORY=%s\n' "$TARGET" "$BACKUP"
)
```

Bei Fehler nach Drop-in-Installation nicht blind erneut deployen. Gesicherten
`ROLLBACK_DIRECTORY` verwenden und den ausdruecklich markierten Rollback
unten ausfuehren, falls die Runtime nicht gesund ist. Bereits heruntergeladene
Pakete/Code werden dabei nicht automatisch zurueckgesetzt.

### G-J. Runtime-/Journal-/App-Verifikation

```bash
(
set -euo pipefail
cd /root/clarity
read -r -p 'ROLLBACK_DIRECTORY aus dem Deploy eingeben: ' BACKUP
[[ "$BACKUP" == /root/clarity/backups/wsgi-runtime-before-* ]]
test -f "$BACKUP/deploy-start"
MASTER="$(systemctl show rove-app-api.service -p MainPID --value)"
test "$MASTER" -gt 1
test "$(pgrep -P "$MASTER" | wc -l)" -eq 1
ps -o pid,ppid,comm -p "$MASTER" --ppid "$MASTER"
ss -ltnp 'sport = :5057'
test -d /run/rove-app-api
stat -c 'RUNTIME_DIR_MODE=%a OWNER=%U' /run/rove-app-api
python3 - "$MASTER" <<'PY'
import pathlib, sys
env = dict(item.decode().split('=', 1) for item in pathlib.Path('/proc/' + sys.argv[1] + '/environ').read_bytes().split(b'\0') if b'=' in item)
for key in ('ROVE_VKS_EMAIL_ENABLED', 'ROVE_VKS_LIVE_APPROVED'):
    assert env.get(key, '0').strip().lower() in ('', '0', 'false', 'no', 'off'), key + ': STOP'
assert not env.get('GUNICORN_CMD_ARGS', '').strip()
print('VKS_MAIL=OFF\nLIVE_APPROVED=NO')
PY
journalctl -u rove-app-api.service --since "$(cat "$BACKUP/deploy-start")" --no-pager -n 80
)
```

Erwartet: genau ein Master plus ein Worker, vier Threads laut gepruefter
Konfiguration, Bind ausschliesslich `127.0.0.1:5057`, Runtime-Verzeichnis root
0700. Kein Boot-Loop, kein Dev-Server-Banner, kein Lock-/500-Fehler. Health
bleibt genau `{"ok":true,"service":"rove-app-api"}`. Env-Werte bleiben geheim.

Echte App-Smokes mit eigenem/dediziertem Testkonto: bestehende Session,
State/Homescreen, Transactions/Cashflow, Goals, Settings, VKS-Fall/private PDF
und bestehender Report/PDF. Kein echter Mailversand, keine Finanz-Testbuchung,
keine Loeschung. Auth-/Mutations-Negativtests bleiben in der isolierten Suite;
nicht Production-Login-Codes oder Kundendaten fuer den Smoke veraendern.

### K. Kontrollierter Restart-Smoke

```bash
(
set -euo pipefail
OLD_MASTER="$(systemctl show rove-app-api.service -p MainPID --value)"
OLD_WORKER="$(pgrep -P "$OLD_MASTER")"
test -n "$OLD_WORKER"
systemctl restart rove-app-api.service
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 http://127.0.0.1:5057/health
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 https://getrove.de/app-api/health
NEW_MASTER="$(systemctl show rove-app-api.service -p MainPID --value)"
test "$NEW_MASTER" != "$OLD_MASTER"
test ! -d "/proc/$OLD_MASTER"
test ! -d "/proc/$OLD_WORKER"
test "$(pgrep -P "$NEW_MASTER" | wc -l)" -eq 1
test "$(systemctl is-active clarity-bot.service || true)" = inactive
echo RESTART_SMOKE=PASS
)
```

Danach G-J inklusive Mail-Gates wiederholen. Ein Restart setzt bestehende
prozesslokale Rate-Limit-Buckets zurueck und ist nicht unterbrechungsfrei;
keine gespeicherten Login-/Finanzdaten werden dadurch geloescht.

### L. ROLLBACK: NUR BEI FEHLGESCHLAGENEM RUNTIME-SWITCH

**Kein normaler Deploy-Schritt.** Dieser Block entfernt nur unser exakt
geprueftes WSGI-Drop-in und aktiviert den unveraenderten bisherigen Dev-Server.
Kein DB-Restore, kein Git-Rollback, keine Env-Aenderung, kein Bot-Neustart.

```bash
(
set -euo pipefail
read -r -p 'Gesicherten ROLLBACK_DIRECTORY eingeben: ' BACKUP
[[ "$BACKUP" == /root/clarity/backups/wsgi-runtime-before-* ]]
test -f "$BACKUP/wsgi-dropin.sha256"
sha256sum -c "$BACKUP/wsgi-dropin.sha256"
rm -- /etc/systemd/system/rove-app-api.service.d/99-rove-wsgi.conf
systemctl daemon-reload
systemctl show rove-app-api.service -p ExecStart --value | grep -F 'argv[]=/root/rove-app-api-venv/bin/python /root/clarity/rove_app_api.py ;' >/dev/null
systemctl restart rove-app-api.service
test "$(systemctl is-active rove-app-api.service)" = active
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 http://127.0.0.1:5057/health
curl -fsS --retry 8 --retry-delay 2 --retry-connrefused --max-time 10 https://getrove.de/app-api/health
test "$(systemctl is-active clarity-bot.service || true)" = inactive
echo RUNTIME_ROLLBACK=PASS
)
```

Rollback stellt nur den Laufzeit-Einstieg zurueck. Der neue Code mit dem
SQLite-Fix und die ungenutzte Gunicorn-Installation duerfen verbleiben.
Danach Mail-Gates erneut read-only bestaetigen. Backup-Retention bleibt 30 Tage.

## Vorhandener WSGI-Entwurf

- `rove_app_wsgi.py`: WSGI-Einstieg.
- `rove_wsgi_logging.py`: Redaktionsfilter fuer Runtime-Logs.
- `deploy/gunicorn.conf.py`: Runtime-Konfiguration.
- `deploy/systemd/rove-app-api.service`: unfreigegebenes WSGI-Unit-Template.
- `requirements/api.txt`: WSGI-Requirements eingebunden.
- `requirements/wsgi.txt`: verifizierter Release-Pin.
- `test_wsgi_runtime.py`: erneut validiert und drei State-Parallel-Laeufe ergaenzt.
- `docs/WSGI_RUNTIME.md`: Ergebnisse und weiterhin gesperrter Operator-Plan.

## Minimaler Read-Lifecycle-Fix

- `rove_app_api.py`: schreibgeschuetzte State-/Transactions-Verbindungen,
  Schema-Vorbereitung vor Requests, keine Ensures im GET.
- `rove_app_state.py`: bestehende Read-Helfer von Schema-/Metadaten-Writes
  getrennt, kanonische Vorbereitung aus vorhandenen Ensures.
- `rove_score.py`: nur Normalisierungs-Guard bei vorbereitetem Read.
- `rove_investment_contributions.py`: Schema-Ensure im Read deaktivierbar;
  Buchungs-/Bewertungsregeln unveraendert.
- `rove_feature_announcements.py`: Schema-Ensure im Read deaktivierbar;
  Eligibility-/Anzeige-Semantik unveraendert.
- `test_sqlite_read_lifecycle.py`: SQL-Trace, Writer-Lock, DTO-/Datenvergleich,
  Legacy-/Startup-/First-Use-Regressionsschutz und Nebenpfad-Checks.
- `test_stability_sprint1.py`, `test_state_link_security_9_1.py`,
  `test_buffer_v1.py`: je eine kanonische Schema-Vorbereitung im Test-Fixture.

WSGI-Konfiguration, Unit-Template, Dependency-Pin, Einstieg und Log-Filter
wurden in diesem Read-Fix nicht weiter ausgebaut. SQLite bleibt ein Single-
Writer-System: konkurrierende/lang laufende echte Writes sind weiterhin ein
Kapazitaetsrisiko. Dieser Read-Fix ist kein allgemeiner 10.000-Nutzer-Nachweis.

## Quellen

- [Gunicorn 26.2.2 Release](https://github.com/benoitc/gunicorn/releases/tag/26.2.2)
- [Gunicorn Settings](https://gunicorn.org/reference/settings/)
- [Gunicorn Worker Design](https://gunicorn.org/design/)
