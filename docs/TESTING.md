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
