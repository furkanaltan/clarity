# Rov.E Changelog

Dieses Changelog wird ab dem Foundation-Stand vom 31.08.2026 kanonisch in
diesem Repository gepflegt. Es beansprucht keine vollstaendige Rekonstruktion
der vorherigen Produktgeschichte.

Fruehere technische Notizen bleiben in `ROVE_STATUS_2026-07-22.md`,
`CLARITY_HANDOFF.md` und den vorhandenen Dokumenten erhalten. Sie sind jedoch
nicht automatisch die aktuelle Architekturwahrheit.

## Unreleased

### Repository Foundation

- Aktive produktionswahre Web-App in `frontend/` als kanonische Source aufgenommen.
- Tatsaechlich benoetigte PWA-Assets gemeinsam mit dem Frontend versioniert.
- Projektkarte, Architekturuebersicht und Betriebs-Runbook angelegt.
- Ignore-Regeln fuer Secrets, Datenbanken, Caches, Ausgaben und lokale Backups erweitert.
- Frontendtests auf den kanonischen Repository-Pfad vorbereitet.

Dieser Foundation-Changeset aendert keine Produkt-, Finanz-, Auth-, Report-
oder Bot-Logik und ist noch nicht deployed.

### Backup retention and restore safety

- Exakte 30-Tage-Retention fuer automatische sowie grundsaetzlich manuelle und
  Release-Backups dokumentiert.
- Regeln fuer aktive Rollback-/Recovery-Staende, Legacy-DBs und DB-Sidecars
  (`-wal`/`-shm`) festgehalten.
- Read-only Produktionsstand dokumentiert: 32/32 automatische Backups integer,
  drei alte DB-Gruppen entfernt und 22 sensible Alt-DBs auf `600 root:root`
  gehaertet; verbleibende Legacy-/Rollback-Kopien sind bewusst retained.
- Tombstone-basierter, fail-closed Restore-Schutz und die Vorgabe ohne
  personenbezogene Inhalte in der Dokumentation festgehalten.

### Privacy provider inventory

- Aktive Drittanbieter- und Infrastrukturpfade fuer AI, Auth-Mail, Markenlogos,
  Marktdaten, Reports, Hosting und Web Push technisch dokumentiert.
- AI-Minimierung, Brandfetch-Browserrequests und die weiterhin lokale
  Nutzer-Reportdarstellung von Haendler-/Kategorieinformationen klargestellt.
- Nicht belegte Providerregionen, Vertragsstatus und Retention ausdruecklich als
  `UNVERIFIED` markiert; finAPI und Stripe nicht als aktiv dokumentiert.
