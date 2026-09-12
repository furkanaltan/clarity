# Rov.E Testing

Stand: 31.08.2026. Die kanonische Test-Suite besteht aus 26 Dateien im
Repository-Root. Die Dateien bleiben dort, weil Imports, `unittest`-Discovery
und einzelne Frontendpfade auf diese Lage abgestimmt sind.

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
