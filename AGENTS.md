# Rov.E: Einstieg fuer Coding Agents

Neue Rov.E-Auftraege mit Arbeitsverzeichnis in diesem Checkout starten. Bei
einem anderswo gestarteten Chat diese Datei vor der Arbeit ausdruecklich lesen;
die blosse Nennung eines externen Repository-Pfads laedt dessen Regeln nicht.

Vor Entwicklungs-, Review- und Release-Auftraegen lesen und befolgen:

- `docs/PROJECT_RULES.md`: kanonische Quellen, Daten- und Testregeln.
- `docs/DEVELOPMENT_WORKFLOW.md`: Rollen, Branches, Freigaben und Release-Gates.
- Bei Deployment-Vorbereitung zusaetzlich `RUNBOOK.md` und `docs/DEPLOYMENT.md`.
- Bei Schemaaenderungen zusaetzlich `docs/MIGRATIONS.md`.

Diese Datei ist ein Einstieg, keine zweite Regelstruktur. Globale CTO- und
Sicherheitsanweisungen gelten weiter. Historische Workfolder-, Skill- oder
Handoff-Anweisungen ersetzen die genannten Projektdokumente nicht. Widersprueche
vor einer riskanten Aktion klaeren, nicht still zugunsten eines alten Ablaufs loesen.

Nur im kanonischen Checkout entwickeln; Branch, HEAD, Diff und fremde Aenderungen
vorher pruefen. Relevante Tests und `bash scripts/test.sh full` vor jedem Commit;
Fehler und Umgebungsgrenzen offen dokumentieren, niemals fuer Gruen abschwaechen.
Codex darf lokale task-only Commits erstellen. Furkan genehmigt und fuehrt Push
und Production-Deployment aus. Kein blindes Pull, Reset, Clean oder Force-Push.
Keine Production-, ENV- oder Datenmutation ohne konkrete Freigabe. Explizite
FROZEN-Grenzen gelten bis zu einem gezielt freigegebenen neuen Auftrag.
