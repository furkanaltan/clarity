# Rov.E Testing

Aktueller Nachweis: siehe den vollstaendigen Lauf vom 21.10.2026 unten.
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

## Full-Suite-Nachweis 21.10.2026

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
