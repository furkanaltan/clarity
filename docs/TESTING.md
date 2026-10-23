# Rov.E Testing

Aktueller Nachweis: siehe die Stabilisierung vom 21.10.2026 unten. Der erste
rote Lauf bleibt als historischer Befund erhalten.
Das Inventar mit 26 Dateien und 281 Tests weiter unten ist die historische
Baseline vom 31.08.2026, nicht die heutige Suite. Die Tests bleiben im
Repository-Root, weil Imports, Discovery und Frontendpfade darauf abgestimmt sind.

## Zentraler Einstieg

```bash
bash scripts/test.sh full
```

Verfuegbare Teilmengen:

```text
auth
finance
frontend
reports
stability
```

Eine abweichende Python-Laufzeit kann mit `ROVE_PYTHON` gesetzt werden.
Zusaetzliche lokale Site-Packages koennen ueber `ROVE_EXTRA_PYTHONPATH`
bereitgestellt werden. Der Runner setzt den kanonischen Frontendpfad auf
`frontend/index.html`, wenn `ROVE_FRONTEND_PATH` nicht bereits gesetzt ist.

## Stabilisierung des Full-Suite-Gates, 21.10.2026

Arbeitsbasis: `7f48c45aa6ac9aec29be2a6f1cec80219a73ddbd`, Branch
`codex/full-suite-and-sync`. Phase A aendert nur Test-Fixtures, Testannahmen,
die Development-Abhaengigkeiten und diese Dokumentation. Keine Runtime-Datei,
keine produktive Datenbank, keine ENV-Datei und kein Production-Zugriff.

Die isolierte Python-3.12.14-Umgebung wird ausschliesslich mit
`pip install -r requirements/dev.txt` aufgebaut. Das Manifest bindet nun auch
die bereits versionierten Recovery-Abhaengigkeiten ein. Keine fremden
Site-Packages einer anderen Python-Version hinzumischen. Node.js und
GnuPG >= 2.4.4 muessen im Prozess-PATH verfuegbar sein. Fuer diesen Lauf
wurde GnuPG 2.4.4 mit libgcrypt 1.10.3 lokal unter `/private/tmp` gebaut;
die Tests verwenden nur temporaere synthetische Schluessel und Datenbanken.

Exakter Gesamtlauf ohne zusaetzliche fremde Site-Packages:

```bash
env -u PYTHONPATH -u ROVE_EXTRA_PYTHONPATH \
  PATH=/private/tmp/rove-fullsuite-20261021-gpg/bin:$PATH LC_ALL=C \
  ROVE_PYTHON=/private/tmp/rove-fullsuite-20261021-venv/bin/python \
  PYTHONPYCACHEPREFIX=/private/tmp/rove-fullsuite-20261021-pycache \
  bash scripts/test.sh full
```

Das Paketinventar und die GPG-Quellpruefsummen liegen mit den vollstaendigen
Testprotokollen ausserhalb des Quellbaums unter
`/Users/furkanaltan/Documents/Codex/rove-fullsuite-stabilization-20261021/`.

Die 32 urspruenglichen Befunde wurden anhand ihrer Ausloeser korrigiert:

| Ursache | Befunde | Korrektur / erhaltene Pruefung |
|---|---:|---|
| Testumgebung | 13 | GPG fehlte und blockierte zwei Klassen; Recovery-CLI verlangt Python 3.12; AWS-SDK, Gunicorn und Web-Push fehlten. Deklarierte Abhaengigkeiten und passende Runtime bereitstellen, keine Runtime-Schutzpruefung umgehen. |
| Widerspruechliche Finanz-Fixtures / alte Drift-Erwartung | 11 | Synthetische Salden mit dem vorhandenen Domain-Mirror konsistent aufsetzen; Konten ueber `legacy_key` statt zufaellige IDs bestimmen. Der absichtliche Drift erwartet den freigegebenen fail-closed Fehler und vergleicht den gesamten DB-Dump vor/nach dem Read. Alle Schulden-, Vermoegens-, Budget- und Historien-Sollwerte bleiben erhalten. |
| Announcement-Zeitdaten und alte Klickannahme | 5 | Veroeffentlichung relativ zum Testzeitpunkt nach der Kontoanlage aufsetzen. Die echte Feature-Klickbehandlung ausfuehren statt einen veralteten Einzeiler zu erwarten. |
| Mentor-Klickannahme | 1 | Echten Handler ausfuehren und genau einen Aufruf fuer den richtigen Kandidaten und seine Route erwarten; Event muss den konkurrierenden Kartenhandler stoppen. |
| Isolierte Budget-JavaScript-Fixture | 1 | Die zwei hinzugekommenen UI-Hilfsfunktionen in der vorhandenen Unit-Fixture bereitstellen. Die Betrags- und Widerspruchspruefungen bleiben unveraendert. |
| Account-Delete-Fixture | 1 | API-DB-Pfad an die temporaere Testdatenbank binden; die bestehenden Ownership-/Loeschassertions bleiben unveraendert. |

Keine Assertion abgeschwaecht, kein Test entfernt oder zusaetzlich
uebersprungen. Gezielte Fixture-/Klicktests: **88/88 PASS**. Die gesonderten
WSGI-/Push-Tests bestehen mit vollstaendiger Umgebung ebenfalls.
Recovery-/Kryptotests einschliesslich beider zuvor blockierter Klassen:
**182/182 PASS** in 253,552 Sekunden.
Der vollstaendige Phase-A-Gate-Lauf besteht: **2.074 Tests in 558,284 Sekunden,
0 Failures, 0 Errors, 0 Skips**. Die hoehere Zahl entsteht durch die nun
ausfuehrbaren Kryptoklassen und WSGI-Tests; kein Test wurde entfernt.
Node.js: v24.18.0. Keine echte Finanzdaten-, Authentifizierungs- oder
Reportregression ist durch diese Befunde bestaetigt. Der Runtime-Schutz bei
widerspruechlichen Bestandskonten bleibt unveraendert aktiv.

Protokoll: `full-suite-phase-a.log` im oben genannten Nachweisverzeichnis.
SHA256: `f64a16295f9f64be990f280280d9563aaa716e55c272b39bd0b2e5d34c412c44`.
Dieser Lauf klaert Phase A; fuer die anschliessende kanonische
Navigationsangleichung gilt ein eigener Full-Suite-Gate vor deren Commit.

Python 3.12.14 teilt die Production-Minor-Version 3.12, ist aber kein Nachweis
unter exakt Production-Python 3.12.3 oder dem Ubuntu-Betriebssystem.

## Phase B: kanonische Report-Navigation und Push-Vorbereitung

Arbeitsbasis ist der bestandene Phase-A-Commit `4e3635d`. Die einzige
Runtime-Aenderung ist das bereits produktive Fragment in
`frontend/index.html:openMentorCandidate()`. Keine Aenderung an Report-
Berechnungen, Renderern, Daten, Monatscheck, Auth oder Backend.

Die angepassten Navigationstests reproduzierten vor dem Fix acht falsche
Ergebnisse in ihren Unterfaellen. Nach der Angleichung bestehen **79/79**
gezielte Navigations-/Mentor-/Onboarding-/Quick-Add-/Buildtests. Die Vorschau
zeigt weiterhin die gelieferten Vermoegens-, Ausgaben- und Sparquotenwerte;
laufende Reports erzeugen keinen Ready-/Opened-Zustand und keine Dateiaktionen.

Die bestehenden Browser-Smokes `test_quick_add_browser.cjs` und
`test_frontend_valuables_browser.cjs` bestehen unveraendert: **12/12 Flows**
in Chromium und WebKit, jeweils Desktop, 390 px und Standalone-iPhone-PWA.
Synthetischer lokaler Transport, alle externen Requests blockiert; Quick-Add-
Kontext, Retry/Doppelklick/Reload sowie Sachwerte-CRUD/Kontowechsel geprueft.
Browser mussten ausserhalb der Prozess-Sandbox gestartet werden, nachdem
beide Engines dort schon vor dem Laden einer Testseite abbrachen.

Die aktuelle kanonische Quelle reproduziert mit dem historischen Build-Marker
direkt den bestaetigten d94b3c7-Hash; historische Ableitung einschliesslich
f762ae5 und 27af4c9 **3/3 bytegenau PASS**, siehe `RELEASE_TRACEABILITY_20261021.md`.
Der abschliessende Full-Suite-Lauf vor dem Phase-B-Commit besteht:
**2.077 Tests in 524,150 Sekunden, 0 Failures, 0 Errors, 0 Skips**.
Gleiche isolierte Python-3.12.14-/GPG-2.4.4-Umgebung und gleicher Runner wie
Phase A; drei zusaetzliche Navigationstests, keine entfernten Tests.
Protokoll: `full-suite-phase-b.log` im oben genannten Nachweisverzeichnis.
SHA256: `d27b86c34f94d3fb27626e39d7735d7db07eaf421684d16cae2c4b554f799db1`.
Keine neue Finanzdaten-, Authentifizierungs- oder Reportregression bestaetigt.

## Phase 2: Ask-Rovi-Rate-Limit und Runtime-Stabilitaet, 21.10.2026

Aufbauend auf Phase 1 wird das Ask-Rovi-Providerlimit in der bestehenden
`app_ai_usage`-Tabelle atomar reserviert. Gezielte API- und isolierte
Gunicorn-Regressionen: **70/70 PASS**. Der Full-Suite-Gate-Lauf mit dem
isolierten Python-3.12.14-/GPG-2.4.4-Setup oben besteht: **2.086 Tests in
546,762 Sekunden, 0 Failures und 0 Errors**.

Der Lasttest verwendet ausschliesslich eine temporaere WAL-Datenbank,
synthetische Nutzer und einen lokalen Provider-Stub. Bei je 48 parallelen
Anfragen pro Nutzer wurden mit einem Worker und mit zwei Workern jeweils genau
6 Requests zugelassen und 42 mit HTTP 429 begrenzt. Das Limit blieb nach einem
vollstaendigen Neustart des isolierten Testservers erhalten. Die Messwerte
stehen in `docs/WSGI_RUNTIME.md`; sie sind keine Produktions- oder
10.000-Nutzer-Kapazitaetsprognose. Keine Production-Daten oder -Services wurden
verwendet.

## Financial Intelligence Block 4B: Insight-Gedaechtnis, 22.10.2026

Arbeitsbasis: `3deabef1a92bff8a10eb6f2fddb146ba18877f38`, Branch
`codex/financial-intelligence-block4a`. Der task-only Aenderungssatz umfasst
die Coach-Memory-Implementierung in `rove_app_state.py`, `rove_app_api.py`,
`rove_coach_v4_delivery.py` und `frontend/index.html`, die neuen gezielten
Tests in `test_coach_insight_memory.py` sowie die genannten Coach-/Frontend-
Fixtures und Schema-Dokumentation. Kein Push, Deployment oder Production-Zugriff.

Gezielte Coach-Memory-/V3-/V4-/Ask-Rovi-Regressionen: **253/253 PASS**.
Der abschliessende Full-Suite-Lauf besteht: **2.144 Tests in 467,212 Sekunden,
0 Failures, 0 Errors, 0 Skips**. Runtime: isoliertes Python 3.12.14,
GnuPG 2.4.4/libgcrypt 1.10.3; voller Aufruf:

```bash
env -u PYTHONPATH -u ROVE_EXTRA_PYTHONPATH \
  PATH=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/gpg/bin:$PATH LC_ALL=C \
  ROVE_PYTHON=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/venv/bin/python \
  PYTHONPYCACHEPREFIX=/tmp/rove-pycache-4b \
  bash scripts/test.sh full
```

Die Suite enthaelt synthetische parallele Claims, Kontoisolation/-loeschung,
fehlendes Schema, persistierte Ask-Rovi-Erklaerungen und Report-/Auth-/Finanz-
Regressionen. Die fokussierten Tests bestaetigen ausserdem, dass ein weiterhin
aktiver Budgethinweis nach 30 Tagen auch ueber einen rollierenden Zeitraum
wieder erscheinen kann. Keine bestehenden Finanzformeln, Login-, Mehrkonten-,
Sachwerte- oder Report-Implementierungen wurden geaendert; Coach V4 bleibt
production-seitig hinter dem bestehenden Gate deaktiviert.

## Financial Intelligence Block 4C: bestaetigte Insight-Anzeige, 22.10.2026

Arbeitsbasis: `0d3f4641112131ba41fbebe17ed6a203ed44abc4`. Der Claim schreibt
nicht mehr voreilig einen Anzeigezeitpunkt. Eine kurzlebige, nutzer- und
Dedupeschluessel-gebundene SQLite-Reservierung wird erst nach dem sichtbaren
Frontend-Rendern per idempotentem Acknowledge atomar in die bestehende
Coach-Memory uebernommen. Die alte Anzeige-/Erklaerungshistorie bleibt bei
abgebrochenen oder abgelaufenen Versuchen erhalten. V3-Prioritaet,
Wiederanzeige-Cooldowns und Ask-Rovi-Erklaerungskontext bleiben unveraendert.

Gezielte Memory-/API-/Mentor-Regressionen: **46/46 PASS**. Angrenzende
V3-/V4-/Coaching-Regressionen: **255/255 PASS**. Der abschliessende Full-Suite-
Lauf besteht: **2.155 Tests in 549,119 Sekunden, 0 Failures, 0 Errors,
0 Skips**. Ausgefuehrt mit dem dokumentierten isolierten Python-3.12-/GPG-
Test-Setup aus Block 4B. Der Lauf enthaelt ausschliesslich lokale synthetische
Testdaten und lokale Testdienste; kein Production-Zugriff, Push oder
Deployment. Coach V4 bleibt weiterhin hinter dem bestehenden Production-Gate.

## Coach V4 explizite globale Gate-Mode, 22.10.2026

Arbeitsbasis: `29efc6ebcc6ba717e3d648292642bbd2054c136a`, task-only Branch
`codex/coach-v4-global-gate`. Der bestehende Server-Gate kennt nun die
ausdruecklichen Modi `OFF`, `ON_SCOPED` und `ON_GLOBAL`. `ON_GLOBAL` verlangt
weiterhin den Master-Schalter und wirkt nur an API-Aufrufen mit serverseitig
authentifizierter Nutzer-ID. Bei fehlendem Modus bleibt die bisherige
`ENABLED=1`-plus-numerische-Allowlist-Konfiguration `ON_SCOPED`; fehlende oder
ungueltige Freigabekonfiguration faellt auf `OFF`. Evidenzpruefung,
Priorisierung, Deduplizierung und Display-Acknowledge wurden nicht veraendert.

Gezielte Gate-/Coach-/Snapshot-/WSGI-Regressionen: **250/250 PASS**. Die
Full-Suite besteht: **2.166 Tests in 549,941 Sekunden, 0 Failures, 0 Errors,
0 Skips**. Runtime Python 3.12.14 und GnuPG 2.4.4; voller Aufruf:

```bash
env -u PYTHONPATH -u ROVE_EXTRA_PYTHONPATH \
  PATH=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/gpg/bin:$PATH LC_ALL=C \
  ROVE_PYTHON=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/venv/bin/python \
  PYTHONPYCACHEPREFIX=/tmp/rove-pycache-global-gate \
  bash scripts/test.sh full
```

Alle Daten waren lokal und synthetisch. Kein Production-Zugriff, ENV-Wechsel,
Push oder Deployment. `ON_GLOBAL` wurde in keiner Laufzeitkonfiguration
aktiviert.

## Profil-Invalidierung fuer Coach V4, 23.10.2026

Basis: `65739043d85d238e91f770b6489ec3168707410a`, Arbeitsbranch
`codex/profile-coach-invalidation`. Runtime-Scope ausschliesslich
`rove_app_api.py:update_profile`: tatsaechlich geaenderte Einkommen markieren
den Snapshot in derselben Transaktion ueber den bestehenden Helfer als stale
und pending. Identische, bereits normalisierte Werte loesen keinen Aufruf aus.
Bei kombinierten Einkommens-/Sparraten-Aenderungen bleibt es bei einem Aufruf;
die vorhandene Behandlung erst kuenftig wirksamer Sparraten bleibt erhalten.
Keine neue Berechnung, kein Schema- oder Queue-Mechanismus.

`test_profile_snapshot_invalidation.py` verwendet echte Flask-Profilrequests,
Cookie-Sessions und temporaere synthetische SQLite-Datenbanken. Die 25 neuen
Tests pruefen einzelne und kombinierte Einkommen, Null/Nullbetrag, Rundung,
identische Retries, sofortige und geplante Sparraten, Nutzerisolation,
fehlende Snapshot-Tabelle sowie bestehende Pending-/Running-Zustaende.
Eine zweite Datenbankverbindung sieht vor Commit weder das neue Profil noch
die Invalidierung. Exceptions beim Antwortaufbau und ein SQL-Abbruch im
spaeteren Sparraten-UPDATE rollen beide Aenderungen zurueck.

Der vorhandene Endpunkt kann bei einem spaeteren Validierungs-Return bereits
geschriebene Profilfelder committen. Dieses Verhalten wird hier nicht
umgestaltet: Auch diese tatsaechlich gespeicherte Einkommensaenderung wird
jetzt invalidiert. Der bestehende sichere Helfer bleibt bei fehlender Tabelle
wirkungslos und faengt SQLite-Fehler weiter ab. Historisch veraltete Snapshots
werden durch den Codewechsel allein nicht rueckwirkend eingeplant.

Gezielte Regressionen einschliesslich Snapshot, sichtbarem Gate, Memory,
Profil/Buffer, Mehrkonten und Monatscheck: **207/207 PASS**.
Vor dem Runtime-Fix schlugen 14 der ersten 22 neuen Tests reproduzierbar fehl.
Keine bestehenden Tests abgeschwaecht oder uebersprungen.
Logs liegen ausserhalb des Repositories unter
`/Users/furkanaltan/Documents/Codex/rove-profile-invalidation-20261023/`.
Der erste Gesamtlauf wurde zur Korrektur der doppelten JSON-Kodierung eines
synthetischen Quellenmarkers unterbrochen; er zaehlt nicht als Testnachweis.
Abschliessender Full-Suite-Gate: **2.196/2.196 PASS** in 569,627 Sekunden,
0 Failures, 0 Errors, 0 Skips. Runtime Python 3.12.14 und GnuPG 2.4.4.
Aufruf im kanonischen Checkout:

```bash
env -u PYTHONPATH -u ROVE_EXTRA_PYTHONPATH \
  PATH=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/gpg/bin:$PATH LC_ALL=C \
  ROVE_PYTHON=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/venv/bin/python \
  PYTHONPYCACHEPREFIX=/private/tmp/rove-profile-invalidation-pycache \
  bash scripts/test.sh full
```

Protokoll: `full-suite.log` im oben genannten Nachweisverzeichnis.
SHA256: `8d6259b40c8e5299ee70f52fa46052bad2c3d546ff7751a9d86938b79bb237cd`.
`tested-source.sha256` bestaetigt, dass Runtime und neue Regressionen seit
diesem Lauf unveraendert sind. Syntax und `git diff --check` bestehen.
Ein AST-Vergleich mit der Basis bestaetigt, dass alle API-Funktionen ausser
`update_profile` unveraendert bleiben. Der lokale Nachweis unter Python 3.12
ersetzt keinen spaeteren Ubuntu-/Production-Smoke.

Release-Allowlist: nur `rove_app_api.py`; kein Frontend, keine ENV, kein
Gate-Wechsel, keine Migration. Nach separater Freigabe nur den API-Dienst neu
starten; Timerkonfiguration und Bot bleiben unveraendert. Nach lokalem PASS
sind frischer Production-Hashvergleich, persoenlicher Push, manueller Deploy
und Betreiberabnahme eigenstaendige Gates.

## Mentorleiste: Score- und Chat-Navigation, 23.10.2026

Basis: `80b6c7e1d884e3a7f527c0d44aaf08ecf3e114f5`, Branch
`codex/mentor-score-navigation`. Runtime-Scope nur `frontend/index.html`.
Der serverseitige Aktionslabel erhielt zuvor ausschliesslich fuer Reports
`data-mentoract="candidate"`. Der Score-Link fiel deshalb zum Karten-Listener
durch und oeffnete den Chat. Gleichzeitig schrieb `setMentorCtaTarget` den
Pfeil auf das aktuelle Modulziel um. Der eingefuegte Trennpunkt blieb wegen
der blockweisen Darstellung des nachfolgenden Labels am Satzende stehen.

Die Textaktion ist nun ein eigener Button mit bestehendem Deep-Link-Pfad.
Rovi-Icon und Pfeil verwenden ausschliesslich `openMentorCoach`; der vorhandene
V4-Kontext, ACK und Opened-Pfad bleiben erhalten. `stopImmediatePropagation`
verhindert einen zweiten Aufruf durch den Karten-Listener. Auch die bisherigen
lokalen Score-/Monatsende-Reportziele bleiben ueber Textaktionen erreichbar.
Die Budgeteinrichtung bleibt am eigenen Textbutton. Ueberfluessige Trennpunkte
werden nur aus den Mentor-Templates entfernt. Keine Auswahl-, Finanz-, Score-
oder Evidenzlogik geaendert.

Sieben neue Regressionen reproduzierten vor dem Fix sieben Fehlschlaege
einschliesslich der Pfeil-Unterfaelle. Bestehende Erwartungen an einen zum
Modul navigierenden Pfeil wurden durch die ausdruecklich freigegebene Trennung
ersetzt: Text oeffnet das Modul, Pfeil den Chat. Report-Monatszuordnung,
Monatscheck, Budgeteinrichtung, Quick-Add und V4-Kontext bleiben geprueft.
Gezielte Python-/Node-Regressionen: **155/155 PASS**.

`test_ask_rovi_browser.cjs` ergaenzt echte Klick-, Touch- und Tastaturtests fuer
Score-Button, Icon, Pfeil und Kartenhintergrund. Geprueft werden genau ein
Zielaufruf, nicht ueberlappende Klickflaechen, sauberer Text und der bestehende
Reportdetail-/Archiv-Rueckweg. Desktop, 390 px und Standalone-iPhone-PWA laufen
gegen eine ausschliesslich lokale synthetische API. Die vorhandenen Chat-
Retry-/Wallet- und fuenf V4-Muster-Smokes bleiben erhalten. Das ersetzt keine
physische iPhone- oder Production-Abnahme.

Protokolle, Screenshots und Quellpruefsummen liegen ausserhalb des Quellbaums
unter `/Users/furkanaltan/Documents/Codex/rove-mentor-navigation-20261023/`.
Chromium und WebKit: **je 16/16 Browser-Flows PASS**, einschliesslich der drei
neuen Navigationsprofile. Chromium: Google Chrome 156.0.8078.47 mit Playwright
1.62.1; WebKit ueber die vorhandene macOS-kompatible Playwright-1.49.1-Runtime.
Die beiden Mentor-Screenshots fuer Desktop und 390-px-PWA wurden visuell
geprueft. Bestehende Tests bleiben erhalten; zwei alte Pfeil-Zielerwartungen
werden entsprechend dem neuen Auftrag als getrennte Text-/Chat-Aktionen
getestet. Die neue Browserpruefung beruecksichtigt den bereits vorhandenen
Rueckweg vom Reportdetail ueber das Archiv, statt diesen als Fehler zu werten.

Probe-Build mit `scripts/build_frontend_release.py --allow-dirty` besteht.
Dieser Preview traegt die Basis-ID und darf nicht deployed werden. Der finale
Build wird nach dem Commit ohne `--allow-dirty` erstellt. Die bekannte
Production-Frontend-Basis `frontend-6573904` reproduziert sich aus der
Quellbasis exakt zu
`d6d3f2c5083a3daee89602f6a7ba25572646c8e7db59aaab9424300dffc41872`.
Erster Full-Suite-Lauf: 2.203 Tests in 550,710 Sekunden, ein Failure im
unveraenderten WSGI-Zwei-Worker-Test
`test_ai_rate_limit_is_shared_across_workers_and_survives_restart`. Dessen
Starthelfer wartet auf den ersten erfolgreichen Healthcheck. Alle 48 kurzen
PID-Proben trafen diesmal denselben Worker; der Test brach vor seiner
Zwei-Worker-Limitpruefung ab. API, WSGI-Konfiguration und dieser Test sind
gegenueber der Basis unveraendert. Derselbe Test besteht isoliert ohne
Code-/Testaenderung in 5,869 Sekunden, einschliesslich beider Worker und
persistiertem Limit nach Neustart (`wsgi-recheck.log`). Der rote Erstlauf
bleibt als `full-suite.log` erhalten und zaehlt nicht als bestandenes Gate.
Der unveraenderte vollstaendige Wiederholungslauf besteht:
**2.203/2.203 PASS** in 491,180 Sekunden, 0 Failures, 0 Errors, 0 Skips.
`full-suite-recheck.log` ist der abschliessende Gate-Nachweis; SHA256:
`6f034cefc1f155bcc9a32c06dc646cd439e6259c0c4bfc4ef778600ab5cc83e6`.
Runtime Python 3.12.14 und GnuPG 2.4.4. Aufruf im kanonischen Checkout:

```bash
env -u PYTHONPATH -u ROVE_EXTRA_PYTHONPATH \
  PATH=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/gpg/bin:$PATH LC_ALL=C \
  ROVE_PYTHON=/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/venv/bin/python \
  PYTHONPYCACHEPREFIX=/private/tmp/rove-mentor-navigation-pycache \
  bash scripts/test.sh full
```

Alle vier Frontend-/Testquellen blieben gemaess `tested-source.sha256` seit
den Browser- und Full-Suite-Laeufen unveraendert. Inline-JavaScript-Syntax
und `git diff --check` bestehen. Die vorhandene instabile Worker-Startprobe
bleibt als Restrisiko der Testsuite dokumentiert; sie wurde nicht repariert,
abgeschwaecht oder uebersprungen.
Keine Backend-, ENV- oder Datenbankaenderung im Produkt;
kein Push, Deployment oder Production-Zugriff.

## Historischer erster Full-Suite-Lauf 21.10.2026

Quellstand: `d94b3c7a846ee98a6f4130370b448743b1ce05cd`; waehrend des Laufs nur
Dokumentationsaenderungen auf `codex/workflow-release-traceability`.
Alle Produkt- und Testdateien blieben unveraendert. Keine Assertions entfernt,
keine Tests uebersprungen und kein Fehler als Erfolg umgewertet.

Ausgefuehrt im kanonischen Checkout:

```bash
ROVE_PYTHON=/private/tmp/rove-assets-test-venv/bin/python \
ROVE_EXTRA_PYTHONPATH=/usr/local/lib/python3.14/site-packages \
bash scripts/test.sh full
```

Python 3.14.6; damit ausdruecklich kein Testnachweis unter Production-Python 3.12.3.
Ergebnis: **FAIL**, 1.982 Tests in 292,662 Sekunden, 9 Failures und 23 Errors.
Zwei Fehler entstehen bereits in `setUpClass`; 1.982 bedeutet daher nicht,
dass jeder entdeckte Test ausgefuehrt werden konnte. Browser-Smokes in `.cjs`
werden durch diesen Python-Runner nicht automatisch gestartet.

| Gruppe | Befunde | Beobachtung / offene Einordnung |
|---|---:|---|
| Consumer Debt / alte Konten-Stabilitaet | 11 Errors | `cash_reconciliation_required`; widerspruechliche Testbestandswerte treffen den neuen Schutz. Kein Beleg fuer einen neuen Datenverlust, auch kein pauschaler PASS. |
| Feature Announcements Sprint 3 | 3 Errors, 2 Failures | Erwartete Claims fehlen / alter Coach-Routing-String. Produktfehler vs. ueberholte Erwartung noch nicht abschliessend geklaert. |
| Mentor V2 | 1 Failure | Frontend-String-Erwartung weicht ab; keine Testanpassung. |
| Budget-Frontend-Fixture | 1 Error | Ausgefuehrtes Node-Fixture scheitert; keine Produktkorrektur in diesem Auftrag. |
| Account-Delete-Fixture | 1 Error | Ungepatchter DB-Zugriff endet mit `unable to open database file`. Keine DB angelegt oder repariert. |
| Recovery | 6 Errors, 5 Failures | GPG fehlt, `boto3`/`botocore` fehlen; CLI-Tests sehen teils `runtime_invalid`. Umgebungsabhaengige Befunde, kein Recovery-PASS. |
| WSGI | 1 Error | `gunicorn` fehlt im ausgewaehlten Interpreter; Modul nicht geladen. |
| Push-Transport | 1 Failure | Erwarteter Send-Aufruf fehlt; fehlende optionale Transportabhaengigkeit als Ursache noch nicht abschliessend verifiziert. |

Der vollstaendige unveraenderte lokale Lauf liegt unter
`/Users/furkanaltan/Documents/Codex/rove-workflow-repair-20261021/full-suite-before-commit.log`.
SHA256: `4e5ca3ad2d3f22c16354b857be06f5eb38cc5d5788d8b7d34b260f0653c3ad0e`.
Das Log bleibt ausserhalb des Quellbaums. Keine Production-Verbindung.

Die Befunde stammen aus dem unveraenderten Produkt-/Teststand. Dieser Auftrag
erstellt ausschliesslich Dokumentations-Commits mit offen ausgewiesenem roten
Gesamtlauf. Das ist kein bestandener Produkt-/Release-Gate. Weitere Releases
bleiben bis zur Einordnung/Behebung und passendem Gesamtlauf NO-GO.
Ein heutiger Lauf ersetzt keinen fehlenden Vorabnachweis aelterer Releases.

## Testinventar

`Produktnahe Temp-DB` bedeutet eine temporaere oder In-Memory-SQLite-DB mit
produktionsnaher Struktur. Kein Test greift auf die produktive `clarity.db` zu.

| Datei | Tests | Bereich | Pfadannahme | Altes Auth-Modell | Produktnahe Temp-DB | Sicher verschiebbar | Risiko |
|---|---:|---|---|---|---|---|---|
| `test_ai_chat_phase1.py` | 12 | AI | Nein | Nein | Ja | Nein | Mittel |
| `test_auth_pin_sprint9_phase2.py` | 12 | PIN | Nein | Nein | Ja | Nein | Mittel |
| `test_auth_sprint9.py` | 7 | Auth | Nein | Nein | Ja | Nein | Hoch |
| `test_coach_savings_copy.py` | 2 | Frontend | Ja | Nein | Nein | Nein | Mittel |
| `test_crypto_v1.py` | 31 | Crypto | Ja | Teilweise | Ja | Nein | Mittel |
| `test_etf_contribution_assignment.py` | 13 | Investments | Nein | Nein | Ja | Nein | Mittel |
| `test_feature_announcements_sprint1.py` | 12 | Announcements | Nein | Nein | Ja | Nein | Mittel |
| `test_feature_announcements_sprint2.py` | 19 | Announcements/Frontend | Ja | Nein | Nein | Nein | Mittel |
| `test_feature_announcements_sprint3.py` | 14 | Announcements | Ja | Nein | Ja | Nein | Mittel |
| `test_final_fix_before_sprint3.py` | 8 | Announcements/Frontend | Ja | Nein | Ja | Nein | Mittel |
| `test_financial_accounts_sprint1.py` | 9 | Finance | Nein | Nein | Ja | Nein | Mittel |
| `test_financial_accounts_sprint2.py` | 19 | Finance | Nein | Teilweise | Ja | Nein | Mittel |
| `test_financial_accounts_sprint3.py` | 18 | Finance | Nein | Teilweise | Ja | Nein | Mittel |
| `test_frontend_cookie_auth_9_1b.py` | 7 | Frontend/Auth | Ja | Nein | Nein | Nein | Mittel |
| `test_frontend_pin_sprint9_phase2.py` | 11 | Frontend/PIN | Ja | Nein | Nein | Nein | Mittel |
| `test_monthly_checkin_v1.py` | 13 | Finance | Nein | Nein | Ja | Nein | Mittel |
| `test_quick_capture_close.py` | 2 | Frontend | Ja | Nein | Nein | Nein | Mittel |
| `test_report_render_v2.py` | 12 | Reports | Ja | Nein | Nein | Nein | Hoch |
| `test_report_snapshot_v2.py` | 6 | Reports | Nein | Nein | Ja | Nein | Mittel |
| `test_report_story_v2.py` | 22 | Reports | Nein | Nein | Nein | Nein | Mittel |
| `test_stability_sprint1.py` | 8 | Stability | Nein | Nein | Ja | Nein | Hoch |
| `test_stability_sprint5.py` | 2 | Stability | Nein | Nein | Ja | Nein | Mittel |
| `test_stability_sprint6.py` | 5 | Stability/Legacy | Nein | Nein | Ja | Nein | Hoch |
| `test_stability_sprint7.py` | 6 | Stability | Nein | Nein | Ja | Nein | Mittel |
| `test_stability_sprint8.py` | 5 | Stability/Auth | Nein | Nein | Ja | Nein | Mittel |
| `test_state_link_security_9_1.py` | 6 | Legacy/Auth/Frontend | Ja | Nein | Ja | Nein | Hoch |

Gesamt: 281 Tests.

## Test-Baseline

Baseline: 281 von 281 Tests erfolgreich. Der zentrale Runner blendet keine
Fehler aus und wandelt keine Fehler in Erfolg um.

Die sechs zuvor dokumentierten Baseline-Fehler wurden ausschliesslich in den
Tests korrigiert:

- Auth-, Parallelzugriffs-, Legacy-CRUD- und State-Link-Tests verwenden jetzt
  echte Cookie-Sessions mit gueltigem serverseitigem PIN-Zustand.
- Der Reporttest prueft die aktuelle, separat gestagete kanonische
  Reportdarstellung, ohne das Reporttemplate zu veraendern.
- Produkt-, Finance-, Auth/PIN- und Reportlogik blieben unveraendert.

## Verschieberisiko

Aktuell ist kein Test als sicher verschiebbar eingestuft. Vor einer spaeteren
Verschiebung muessen Root-Imports, Fixture-Pfade und Discovery explizit
entkoppelt werden. Das ist ein eigenes Changeset und nicht Teil von Wave 3.
