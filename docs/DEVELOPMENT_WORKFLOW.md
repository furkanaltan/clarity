# Rov.E Entwicklungs- und Release-Workflow

Status: verbindliche Arbeits- und Freigaberegeln.

Dieses Dokument definiert Entwicklungsmodi, Release-Gates und Verantwortung.
`docs/DEPLOYMENT.md` beschreibt Runtime und Zielpfade; `RUNBOOK.md` beschreibt
operative Serverablaeufe. Diese technischen Dokumente erteilen keine eigene
Produktionsfreigabe. Repository-Regeln und Ownership stehen in
`docs/PROJECT_RULES.md`.

## 1. Entwicklungsmodus

Dieser Modus gilt, solange Rov.E nicht oeffentlich mit zahlenden Kunden laeuft.

1. Ausschliesslich im kanonischen App-Checkout arbeiten. Der aktuelle
   Arbeits-/Release-Branch bleibt vorerst `feature_clarityr-report`.
2. Vor Beginn Branch, HEAD und Working Tree pruefen. Unabhaengige oder fremde
   Aenderungen bleiben unangetastet; unklarer Scope wird vor dem Editieren
   geklaert.
3. Scope und erlaubte Dateien festhalten. Bugfixes und Features erhalten
   task-only Commits; keine stillen Live-Hunks und keine Misch-Commits.
4. Relevante Regression-, Integrations- und UI-Tests ausfuehren. Vor einem
   Commit gilt zusaetzlich die Full-Suite-Regel aus `docs/PROJECT_RULES.md`.
   Syntax, Build und `git diff --check` passend zum Aenderungstyp pruefen.
5. Diff, gestagte Dateiliste und Arbeitsstatus unmittelbar vor dem Commit
   erneut pruefen. Nur freigegebene Dateien committen.
6. Codex darf nach erfolgreicher Pruefung den task-only Commit erstellen.
   Furkan fuehrt Push und Production-Deploy aus.
7. Nach dem Deploy den vorher festgelegten Smoke-Test durchfuehren und Ergebnis
   bestaetigen. Bei Fehlern Root Cause untersuchen und ueber einen neuen
   versionierten Fix oder den dokumentierten Rollback reagieren. Keine
   dauerhaften manuellen Live-Aenderungen ausserhalb des Repositories.

Keine dauerhafte Source of Truth in `/private/tmp`, alten Workspaces oder
unreferenzierten Kopien anlegen. Temporaere Build-Artefakte duerfen dort liegen,
aber nicht als Entwicklungsbasis dienen.

## 2. Live-Modus

Sobald Rov.E oeffentlich oder kommerziell mit echten Kunden laeuft, gelten
zusaetzlich die folgenden verpflichtenden Gates:

1. Aenderung auf eigenem Feature- oder Fix-Branch isolieren; Scope, erlaubte
   Dateien, Risiko und betroffene Services dokumentieren.
2. Unit-, Regression-, relevante Integrations- und Frontend-Tests ausfuehren.
   Syntax, Build, Diff und Dateiliste pruefen. Rote Tests blockieren den
   naechsten Schritt, ausser ein nachweislich unabhaengiger Baseline-Fehler ist
   reproduziert und explizit als solcher dokumentiert.
3. In einer produktionsnahen Staging-Umgebung mit derselben relevanten
   Python-/Runtime-Version und denselben Services pruefen. Keine echten
   Kundendaten fuer Tests verwenden.
4. Neue oder wesentlich veraenderte Funktionen zunaechst intern freigeben:
   Furkans Account oder dedizierte interne Testaccounts. Feature Flags sind
   dafuer das Zielbild; solange keine Flag-Infrastruktur existiert, wird kein
   Flag-Verhalten vorgetaeuscht.
5. Manuellen Smoke-Test fuer Laden, Anzeigen, Speichern, Bearbeiten, Loeschen,
   Fehlerfall, Mobile und Reload ausfuehren, soweit fuer die Funktion relevant.
6. Danach kontrollierte Beta-/Kohortenfreigabe statt sofortiger Freigabe an
   alle Nutzer. Grosse Funktionen werden schrittweise erweitert.
7. Unmittelbar nach Freigabe API-Health, Error-Logs, Latenz, relevante Worker,
   DB/API-Fehler, Frontend-Build-ID und Nutzerfehler beobachten.
8. Erst nach sauberer interner/Kohortenphase vollstaendig freigeben.

Ein fehlendes Staging, ein unbekannter Production-Stand, unerwartete Commits,
unklare Datenwirkung oder ein nicht getesteter Rollback ist ein NO-GO.

## 3. Release-Gate und Rollback

Vor jedem Production-Deploy muessen dokumentiert und geprueft sein:

- freigegebener Commit und erwarteter Remote-HEAD;
- aktueller Production-Commit sowie exakte Commit-/Dateidifferenz;
- sauberer bzw. erklaerter Production-Working-Tree ohne ueberschreibbare
  lokale Aenderungen;
- bestandene Tests und passende Runtime-Version;
- Health-Baseline vor dem Deploy;
- Backup der betroffenen Live-Dateien und gegebenenfalls der Datenbank;
- exakter Rollback-Commit bzw. Restore-Befehl und betroffene Services;
- erwartete Build-ID und Hash fuer statische Frontend-Artefakte.

Kein ungezieltes `git pull` als Release-Abkuerzung. Auf dem Server wird der
freigegebene Commit explizit verifiziert; der Produktionsstand muss auf der
freigegebenen Release-Kette liegen und unerwartete Commits muessen vor dem
Deploy gestoppt werden. Nur tatsaechlich betroffene Services neu starten.

Bei fehlgeschlagenem Health- oder Smoke-Test sofort den vorher verifizierten
Code-/Frontend-Stand wiederherstellen, betroffene Services kontrolliert
starten und Health erneut pruefen. Datenbank-Rollback ist getrennt vom
Code-Rollback und darf nur ueber das kontrollierte Restore-Verfahren aus
`RUNBOOK.md` erfolgen. Eine Code-Ruecknahme setzt keine Datenmigration
automatisch zurueck.

## 4. Financial-Truth-Gates

Fuer Konten, Transaktionen, Vermoegen, Investments, Ziele, Score, Vertraege,
Open Banking und finanzielle Reports gelten zusaetzlich:

- Financial Truth bleibt in den bestehenden kanonischen Domain-/DB-Pfaden;
  UI-Aenderungen duerfen sie nicht unbeabsichtigt veraendern.
- Regressionstests muessen bekannte Vorher-/Nachher-Werte und relevante
  Fehlerfaelle abdecken.
- Keine stillen Backfills, Schaetzungen oder historischen Neuinterpretationen.
- Keine bestehenden Kundendaten ueberschreiben; neue Ableitungen bleiben von
  bestaetigten Fakten unterscheidbar.
- Jede Schemaaenderung muss additiv, idempotent, getestet und in
  `docs/MIGRATIONS.md` dokumentiert sein. Production-Migrationen erhalten ein
  eigenes Backup-, Dry-Run-, Apply- und Integrity-Gate.
- Vor breiter Freigabe sind produktionsnahe Staging- und interne Tests Pflicht.

## 5. Rollen

**Codex** ist verantwortlich fuer Scope-Klaerung, Codeaenderung, Tests,
Diff-Review, task-only Commit und Release-Vorbereitung. Codex fuehrt keinen
Production-Push oder Production-Deploy aus.

**Furkan** fuehrt Push, Production-Deploy und den finalen manuellen Smoke-Test
aus und bestaetigt dessen Ergebnis. Ein Deploy erfolgt nur fuer den explizit
freigegebenen Commit und nach bestandenem Release-Gate.

## 6. Feature Flags und Branch-Zielbild

Groessere kuenftige Features sollen schrittweise steuerbar sein: intern, Beta,
10 %, 50 % und 100 %. Ein Flag muss bei Problemen ohne Notfall-Codeaenderung
deaktivierbar sein. Das ist ein verbindliches Zielprinzip, keine Freigabe zum
Bau einer Flag-Infrastruktur in diesem Dokument.

Aktuell bleibt `feature_clarityr-report` der Arbeits-/Release-Branch. Spaeteres
Zielbild:

- `main`: Production Source of Truth;
- `develop`: integrierter und getesteter Entwicklungsstand;
- `feature/*`: neue Funktionen;
- `fix/*`: Fehlerkorrekturen;
- `release/*`: nur fuer begruendete isolierte Releases.

Diese Branches werden nicht durch dieses Dokument angelegt oder umbenannt.

## 7. Spaetere technische Arbeit

Vor dem Live-Modus sind insbesondere produktionsnahes Staging, reproduzierbare
CI-Checks, ein kontrolliertes Feature-Flag-System, Deployment-Automation mit
expliziter Freigabe, Monitoring/Alerting und regelmaessig geuebte Restore- und
Rollback-Ablaufe zu klaeren. Dieses Dokument installiert keine Infrastruktur
und aendert keinen Production-Zustand.
