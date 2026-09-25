# Rov.E Project Rules

Diese Regeln halten Repository und Laufzeit nachvollziehbar.

## Source of truth

- Dieses Repository ist die kanonische Source of Truth.
- `frontend/index.html` ist die kanonische aktive Frontendquelle.
- Produktion wird nur aus bewusst geprueften kanonischen Quellen aktualisiert.
- Alte Work-Kopien sind Referenzmaterial, keine Entwicklungsquelle.

## Root directory

- Im Root liegen nur absichtliche Projektdateien und etablierte Entry Points.
- Experimente, Previews und Einmal-Ausgaben gehoeren in Scratch-/Archivbereiche.
- Unklare Dateien werden klassifiziert, nicht vorschnell geloescht.

## Backups, retention and generated files

- Backups gehoeren ausserhalb des aktiven Quellbaums.
- Logs, PDFs, Previews, Caches, WAL/SHM-Dateien und temporaere Ausgaben werden
  nicht versioniert.
- Historische Quellen werden nur mit dokumentiertem Grund archiviert.
- Automatische DB-Backups haben eine exakte Retention von 30 Tagen.
- Manuelle und Release-Backups gelten ebenfalls fuer 30 Tage ab `mtime`, ausser
  sie sind ausdruecklich als aktiver Rollback-/Recovery-Stand dokumentiert.
- Legacy-DBs werden nur mit dokumentiertem Zweck oder bis zum Nachweis einer
  gleichwertigen Ersatzkopie behalten; unbegrenzte Aufbewahrung ohne Zweck ist
  nicht zulaessig.
- Sensible DB-Dateien muessen `600 root:root` sein. WAL/SHM-Sidecars werden mit
  ihrer Haupt-DB als eine Backup-Gruppe behandelt.
- Restore-Prozesse muessen Account-Delete-Tombstones erneut anwenden und bei
  fehlendem oder ungueltigem Ledger fail-closed abbrechen.
- Versionierte Dokumentation darf keine personenbezogenen oder finanziellen
  Inhalte enthalten.

## Ownership

- Jede fachliche Regel hat einen klaren Domain-Owner und einen definierten
  Entry Point.
- Transportadapter, insbesondere der Telegram-Bot, besitzen keine neue
  Finanzwahrheit ohne ausdrueckliche Freigabe.
- Duplizierte Business-Logik wird nicht nebenbei eingefuehrt.

## Database

- Es gibt eine produktive Datenbank; alle Reads/Writes sind `user_id`-scoped.
- Schemaaenderungen sind additiv, idempotent und in `docs/MIGRATIONS.md`
  dokumentiert.
- Keine ad-hoc Produktionsmigration aus einem Entwicklungsverzeichnis.

## Tests

- Vor einem Commit: `bash scripts/test.sh full`.
- Wenn die Umgebung die Suite verhindert, werden fehlende Pakete und die
  genaue Einschraenkung dokumentiert.
- Bekannte Baseline-Fehler werden nicht durch abgeschwaechte Assertions versteckt.

## Documentation and deployment

- Verbindliche Entwicklungsmodi, Release-Gates und Rollen stehen in
  `docs/DEVELOPMENT_WORKFLOW.md`.
- `docs/DEPLOYMENT.md` ist Runtime-/Pfadinventar; `RUNBOOK.md` ist die
  operative Server-Checkliste. Keines der beiden Dokumente ersetzt das
  Workflow- und Freigabe-Gate.
- Ownership- oder Laufzeitaenderungen werden in Project Map und Architektur
  nachgefuehrt.
- Kein Deploy aus zufaelligen Work-Kopien.
- Vor riskanten Aenderungen muessen Backup, Rollback und betroffene Services
  bekannt sein.

## Legacy status

Legacy-Komponenten tragen einen expliziten Status: `ACTIVE`, `DEPRECATED`,
`OBSERVATION`, `RETIRE_LATER` oder `RETIRED`. Der Telegram-Bot ist in
Produktion aktuell `STOPPED FOR OBSERVATION`; das ist keine endgueltige
  Code-Loeschung.

## New files

Vor einer neuen Root- oder Moduldatei klaeren:

1. Gibt es bereits einen passenden Owner?
2. Welcher Ordner besitzt die Datei?
3. Ist sie Runtime, Test, Migration, Script, Dokumentation oder Generated?
4. Ist ihr Zweck fuer den naechsten Entwickler eindeutig?

## Health check

Nach groesseren Features und vor Launch-Hardening wird der read-only Check
`bash scripts/repo_health.sh` ausgefuehrt. Er meldet verdaechtige Zustaende und
loescht niemals automatisch Dateien.
