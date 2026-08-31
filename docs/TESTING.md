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
| `test_auth_sprint9.py` | 7 | Auth | Nein | Ja | Ja | Nein | Hoch |
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
| `test_stability_sprint1.py` | 8 | Stability | Nein | Ja | Ja | Nein | Hoch |
| `test_stability_sprint5.py` | 2 | Stability | Nein | Nein | Ja | Nein | Mittel |
| `test_stability_sprint6.py` | 5 | Stability/Legacy | Nein | Ja | Ja | Nein | Hoch |
| `test_stability_sprint7.py` | 6 | Stability | Nein | Nein | Ja | Nein | Mittel |
| `test_stability_sprint8.py` | 5 | Stability/Auth | Nein | Nein | Ja | Nein | Mittel |
| `test_state_link_security_9_1.py` | 6 | Legacy/Auth/Frontend | Ja | Ja | Ja | Nein | Hoch |

Gesamt: 281 Tests.

## Bekannte Baseline-Fehler

Der unveraenderte Vergleichsstand hat sechs bekannte Fehler. Der zentrale
Runner blendet sie nicht aus und wandelt sie nicht in Erfolg um.

1. `test_auth_sprint9.PasswordAuthTests.test_change_password_requires_current_password_and_reissues_session`
   erwartet `401`, erhaelt wegen fehlender PIN-Einrichtung beziehungsweise
   fehlendem Unlock im Fixture aber `423`.
2. `test_report_render_v2.ReportRenderV2Tests.test_july_truth_pass_uses_merchant_budget_and_zero_guards`
   erwartet den zusammenhaengenden Text `24 Tage getrackt`; das aktuelle,
   separat geaenderte Template rendert `24 Tagen aktiv getrackt`.
3. `test_stability_sprint1.StabilitySprint1Tests.test_parallel_transactions_materialize_scheduled_savings_once`
   verwendet einen alten Bearer-Pfad und erreicht wegen `401` die eigentliche
   Idempotenzpruefung nicht.
4. `test_stability_sprint6.StabilitySprint6ContractTests.test_legacy_origin_contract_uses_the_normal_app_edit_and_delete_path`
   verwendet ebenfalls einen alten Bearer-Pfad und erhaelt `401`.
5. `test_state_link_security_9_1.StateLinkSecurityTests.test_cookie_session_returns_only_its_own_uncached_state`
   erzeugt keinen passenden `app_session_pins`-Zustand und erhaelt deshalb
   `423 setup_required`.
6. `test_state_link_security_9_1.StateLinkSecurityTests.test_logout_blocks_state_and_old_bearer`
   scheitert an derselben fehlenden PIN-Voraussetzung.

Baseline: 275 von 281 Tests erfolgreich. Diese Fehler sind dokumentierte
Altlasten, keine stillschweigende Freigabe fuer neue Regressionen.

| Test | Status | Spaetere Korrektur |
|---|---|---|
| `test_change_password_requires_current_password_and_reissues_session` | Baseline, Auth/PIN-Fixture-Drift | Aktuelle PIN-Einrichtung und Unlock im Fixture abbilden |
| `test_july_truth_pass_uses_merchant_budget_and_zero_guards` | Baseline, separat gestagete Reporttemplate-Abweichung | Assertion und freigegebene Report-Copy in einem separaten Report-Changeset abgleichen |
| `test_parallel_transactions_materialize_scheduled_savings_once` | Baseline, alter Bearer-Pfad | Cookie-Session und PIN-Unlock im Fixture verwenden |
| `test_legacy_origin_contract_uses_the_normal_app_edit_and_delete_path` | Baseline, alter Bearer-Pfad | Aktuellen Cookie-/PIN-Auth-Pfad im Fixture verwenden |
| `test_cookie_session_returns_only_its_own_uncached_state` | Baseline, fehlende PIN-Voraussetzung | Passenden `app_session_pins`-Zustand im Fixture erzeugen |
| `test_logout_blocks_state_and_old_bearer` | Baseline, fehlende PIN-Voraussetzung | Passenden PIN-Zustand vor der Logout-Pruefung erzeugen |

## Verschieberisiko

Aktuell ist kein Test als sicher verschiebbar eingestuft. Vor einer spaeteren
Verschiebung muessen Root-Imports, Fixture-Pfade und Discovery explizit
entkoppelt werden. Das ist ein eigenes Changeset und nicht Teil von Wave 3.
