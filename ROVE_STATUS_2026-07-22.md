# Rov.E Arbeitsstand – laufend aktualisiert

## AKTUELLER HANDOFF-SNAPSHOT FUER CLAUDE/CODEX (14.08.2026)

> Diesen Abschnitt zuerst lesen. Aeltere Abschnitte darunter sind die chronologische
> Entwicklung und koennen inzwischen ueberholt sein. Vor jeder Aenderung die konkrete
> Stelle im aktuellen Code pruefen; nicht aus alten Statussaetzen ableiten.

### Stability Fix 5 – Mentor / Legacy Bot / Expense Truth (23.08.2026)

- Der gemeinsame Expense-Domain-Pfad bucht App- und Legacy-Bot-Ausgaben atomar: Expense,
  Kontowirkung, Cash-Movement und Cash-Mirror erfolgen innerhalb einer `BEGIN IMMEDIATE`-
  Transaktion. Telegram-Ausgaben erhalten einen stabilen, nutzergebundenen Idempotenzschluessel.
- Bei aktivem Multi-Account-Flag belastet eine Bot-Ausgabe die konfigurierte `expense`-Rolle;
  bei Flag OFF bleibt der bestehende Legacy-Pfad aktiv. Der Bot bleibt ein Legacy-Kanal und
  bekommt keine neuen Produktfunktionen.
- Zielprognosen verwenden und benennen nur die explizite zielbezogene Monatsrate. Allgemeine
  Sparrate, ETF-Sparrate, Cash und Investments werden nicht als Zielrate dargestellt.
- Der KI-Kontext nutzt aktuelle Cash-, Budget-, Ziel-, Score- und Investment-Fakten. Nur echte
  Nutzerbudgets gelten als Budget; beobachtete Ausgaben sind keine Sollgrenzen. KI-Freitext mit
  nicht freigegebenen Finanzzahlen wird neutral verworfen.
- Im lokalen Profilmodus werden beobachtete Ausgaben nicht mehr als echte Budgets persistiert.
  Bestehende Nutzerbudgets bleiben unveraendert. Lokal: 113 Tests, Python-Compile, JavaScript-
  Syntax und `git diff --check` gruen. Der Production-Gate-Scan war ohne Drift, Referenzfehler
  oder SQLite-Fehler; Baseline-Diffs enthielten nur Stability Fix 5.

### Stability Fix 6 – Contract Unification / Legacy-Abschluss (23.08.2026)

- `app_contracts` ist die operative Vertragsquelle. Bekannte Legacy-Fixkosten aus
  `users.fixed_costs_details` werden mit einer stabilen `telegram_legacy:<section>:<key>`-
  Referenz additiv normalisiert. `source` beschreibt nur die Herkunft und sperrt keine
  Bearbeitung oder Löschung mehr.
- `migrate_legacy_contracts.py` bietet einen transaktionalen Dry-Run/Apply-Ablauf mit Backup.
  Ein erneuter Lauf erkennt die gleiche Legacy-Referenz und erzeugt keinen zweiten Vertrag.
  Exakte, native App-Duplikate werden nicht kopiert; unsichere Namenskonflikte bleiben bewusst
  unverändert und werden gezählt.
- Nach der Normalisierung werden nur die migrierten Legacy-Schlüssel aus dem JSON entfernt;
  Restschuld, Null-/ungültige oder unbekannte Altwerte bleiben erhalten. Der App-Vertragsmirror
  berechnet `users.fixed_costs` anschliessend aus genau einer operativen Vertragsmenge.
- Keine historischen Cash-Movements, Expenses, Investments, Score-Historie oder finalisierten
  Reports werden durch die Migration verändert. App- und Legacy-origin Verträge verwenden
  danach denselben Betrag-Edit- und Delete-API-Pfad.

### Quick Fix – Manuelle Vertragskategorie Miete (25.08.2026)

- Die manuelle Vertragserfassung verwendet jetzt die bestehende Kategorie-Erkennung statt des
  voreingestellten `Abos`-Werts. `Miete`, `Wohnungsmiete`, `Warmmiete` und `Kaltmiete` werden
  wie Strom unter `Wohnen` eingeordnet; Netflix und Spotify bleiben `Abos`.
- Bestehende Produktionsverträge werden nicht automatisch umgeschrieben. Der bestehende
  Vertrag-Editor erlaubt weiterhin Betrag ändern und löschen, aber keine Kategorieänderung.

### Quick Fix – Vermögens-Tap und Ziel-Sheet schließen (25.08.2026)

- Die gemeinsame Vermögenskarte skaliert beim Öffnen einer einzelnen Konto- oder Depotzeile nicht
  mehr. Das verhindert das sichtbare Verkleinern und Zurückspringen des gesamten Kartenblocks.
- Das Ziel-Sheet hat einen direkten Schließen-Button. Die bestehende Hintergrund-Schließgeste bleibt
  unverändert; ein versehentlich geöffnetes Ziel lässt sich damit jederzeit eindeutig verlassen.
- Das Ziel-Sheet ist auf die sichtbare iPhone-Höhe begrenzt und scrollt bei langen Inhalten intern.
  Sein Schließen-Button bleibt dadurch unterhalb der Statusleiste erreichbar.

### Quick Fix – iOS PIN AutoFill (26.08.2026)

- Das reine App-PIN-Feld ist kein Passwortfeld mehr: Es verwendet einen neutralen Namen,
  `type="text"`, numerische Eingabe und deaktivierte AutoFill-, Korrektur- und Schreibprüfungs-Hinweise.
  Die sichtbare Maskierung erfolgt ausschließlich per `-webkit-text-security`, sodass eine PIN wie
  `0007` weiterhin unverändert als String an die bestehende Server-Validierung geht.
- Der normale gesperrte PIN-Screen enthält weiterhin nur die PIN-Eingabe. E-Mail- und Passwortfelder
  erscheinen ausschließlich im getrennten Re-Auth-/Recovery-Ablauf.
- Lokal geprüft: 11 PIN-Frontend-Tests, JavaScript-Syntax und `git diff --check` sauber. Noch nicht deployed.

### Rov.E AI – Phase 1 und Pilot-Quick-Fix (26.08.2026)

- Der neue, geschützte `POST /v1/ai/chat`-Pfad ist lokal vorbereitet. Er leitet den Nutzer nur aus
  der HttpOnly-Cookie-Session ab und bleibt hinter dem bestehenden serverseitigen PIN-Gate.
- Die Schicht ist strikt read-only: Schreibabsichten bleiben bei den vorhandenen deterministischen
  Rov.E-Funktionen und werden nicht an das Modell weitergegeben. Es gibt keine Tools oder Funktionen
  fuer OpenAI.
- Finanzkontext wird je Anfrage frisch und nur fuer den erkannten Kontexttyp aufgebaut. Allgemeine
  Bildungsfragen wie TER erhalten keinerlei persönliche Finanzdaten; Auth-, Session- und Profildaten
  wie E-Mail oder Tokens sind ausgeschlossen.
- Gespräche sind pro Nutzer und servergenerierter Conversation-ID auf 12 Nachrichten begrenzt und
  werden nach 24 Stunden idempotent bereinigt. Der Verlauf ist nur Sprachkontext, nie Finanzwahrheit.
- Die Web-App nutzt AI erst beim bestehenden deterministischen Fallback, kennzeichnet generierte
  Antworten als `Rov.E AI` und setzt deren Inhalt ausschliesslich als Text ein. Die Mentor-Karte
  behält ihren bisherigen Öffnungsweg und hat zusätzlich einen dezenten Chevron-Hinweis.
- Phase 1 ist nach Backup, Datenschutz-Veröffentlichung, Schema-Initialisierung und Server-Testlauf
  produktiv. Der API-Pfad bleibt Cookie- und PIN-geschützt; die ausgelieferte Web-App enthält keinen
  OpenAI-Key.
- Der Pilot-Quick-Fix trennt allgemeine Finanzfragen wie ETF, Börse und Broker ausdrücklich von
  persönlichen Portfoliofragen. Nur die zweite Gruppe erhält Positionsdaten. Manuelle, nicht bereits
  als Holding dargestellte Aktienevents werden dabei als manuelle Positionen ergänzt; so bleibt eine
  Position wie XPeng sichtbar, ohne sie mit einer Holding doppelt zu zählen.
- Der Browser-Router lässt allgemeine ETF-/Aktien-/Broker-Fragen jetzt bewusst bis zum AI-Fallback
  durch. Der frühere Asset-Keyword-Block beantwortete schon das einzelne Wort `ETF` als persönlichen
  Depot-Snapshot. Deterministische Portfolioauskünfte bleiben ausschließlich bei klaren persönlichen
  Markern wie `mein`, `habe ich` oder `zeig mir`; `Was ist mein ETF?` fragt stattdessen kurz nach.
- Eindeutig fachfremde Themen werden lokal und ohne Provider-Aufruf freundlich abgefangen. Modelltext
  bleibt Plaintext; Markdown-Sonderzeichen werden zusätzlich vor der Ausgabe entfernt.
- Lokal geprüft: 12 AI-Sicherheits-, Routing- und Kontexttests erfolgreich; Python-Compile und
  `git diff --check` für die betroffenen Dateien sauber. Der Quick-Fix ist noch nicht deployed.

### Feature Announcements – Sprint 1 (Production Gate gruen, Deploy freigegeben)

- Eine additive serverseitige Grundlage trennt globale Feature-Definitionen von
  nutzergebundenem Interaktionsstatus. State-Zeilen entstehen erst bei einer tatsächlichen
  Interaktion, nicht beim Veröffentlichen eines Features.
- Die Eligibility berücksichtigt aktive Zeitfenster, einen 90-Tage-Zeitraum für prominente
  Neuigkeiten, das Kontoalter, optionale bestehende Feature-Flags sowie eine feste interne
  Deep-Link-Positivliste. Externe URLs werden nie ausgeliefert.
- Die vorbereiteten Interaktionspfade für `seen`, `opened`, `dismissed` und `completed` sind
  Cookie-, PIN- und nutzergebunden sowie idempotent. AI-, Crypto- und Report-Nutzung können
  ausschließlich als Boolean-Signal als erledigt erkannt werden.
- Der State liefert den neuen Block bereits passiv mit. Glocke, localStorage-Activity-Feed,
  Coach und Push bleiben unverändert; es gibt keine produktiv sichtbaren Test-Ankündigungen.
- Der Production-Snapshot-Gate lief gegen acht aktive Nutzer vollständig grün: Schema additiv
  und wiederholbar, Eligibility und Smart-Dismiss korrekt, Multi-Device-State ohne Duplikate
  sowie Integrity, Foreign Keys, Cash- und Investment-Drift vor und nach der Simulation sauber.

### Verbindliche Quellen und Deployment

- Einzige bearbeitete App-Quelle:
  `/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/work/rove-app/index.html`
- Python-Arbeitsstand:
  `/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/work/Calrity_Main/`
- Git-Main-Repo auf dem Mac:
  `/Users/furkanaltan/Documents/Project Clarity/Calrity_Main`
- Git-Branch: `feature_clarityr-report`; Server-Checkout: `/root/clarity`.
- Live-App: `https://getrove.de/app/`; API via nginx unter `/app-api/`, intern Port 5057,
  Service `rove-app-api`. Der alte Telegram-Dienst `clarity-bot` laeuft noch als
  Migrations-/Uebergangssystem, ist aber nicht mehr das Zielprodukt.
- Python geht vom Workfolder per Kopie ins Main-Repo, danach `py_compile`, Commit/Push,
  serverseitig `git pull` und nur der betroffene Service-Neustart. Die App-`index.html`
  geht separat per `scp` nach `/var/www/getrove/app/index.html`; sie liegt nicht im
  produktiven Python-Git-Deployweg.
- Seit der Serverhaertung erfolgt SSH als `roveadmin` ueber den Alias `ssh rove`, danach
  bei Bedarf `sudo -i`. Niemals innerhalb einer bereits offenen Server-Sitzung erneut
  `ssh rove` starten. Die App fuer Updates niemals vom Home-Bildschirm loeschen.

### Produkt- und Datenarchitektur heute

- Rov.E ist App-first. App, API, Report-Worker und der noch laufende Bot verwenden dieselbe
  produktive SQLite-Datenbank `clarity.db`; keine lokale Schatten-Datenbank fuer verbindliche
  Finanzobjekte bauen.
- Bestehende Telegram-Nutzer kommen ueber `/app`; neue App-only-Beta-Nutzer werden im
  Adminbereich eingeladen und melden sich per E-Mail-Einmalcode an. App-only-Identitaeten,
  Onboardingdaten, Sitzungen und Finanzdaten sind zentral gespeichert und geraeteuebergreifend.
  Oeffentliche Selbstregistrierung, Stripe und Paywall sind noch nicht live.
- Dauerhaft gespeichert werden Buchungen, Kategorien, Budgets, Kontostaende, Umbuchungen,
  Bargeldbewegungen, Einnahmen, Fixkosten/Vertraege, Ziele/Zweckbindungen, Immobilien,
  Investments, Sparplaene, Score/Rov.E Points, Monatsplaene und Reports. Freie Mentor-Chats
  werden bewusst nicht als dauerhafte Chat-Historie archiviert.
- Cash besitzt aktuell genau die drei festen Kontotypen `giro`, `tagesgeld`, `bargeld`.
  Ausgaben und Einnahmen veraendern den passenden Kontostand dauerhaft; Umbuchungen veraendern
  nur die Verteilung. Die Summen `current_cash` und `current_investments` bleiben verbindliche
  Aggregate fuer App, Score und Reports.
- Nutzer koennen die vorhandenen Vermoegensarten unter `Konten verwalten` zentral sortieren.
  Die Reihenfolge liegt in `app_asset_order`, gilt geraeteuebergreifend fuer App-Ansichten,
  aber nicht fuer fachlich sortierte Reports.
- Echte dynamische Mehrfachkonten sind noch nicht gebaut. Mehrere Giro-/Tagesgeldkonten,
  Depots oder Immobilien benoetigen eine additive Migration mit stabilen Konto-/Asset-IDs;
  keine lokale Plus-Kachel oder Namens-Deduplizierung als Scheinloesung einfuehren.

### Fertiger App-Core

- Gefuehrtes App-only-Onboarding mit Einkommen/Zahltag, Vermoegen, Sparraten, Fixkosten,
  Zielen und optionalem ETF-Plan; leere Zustaende und erste Schritte sind vorhanden.
- Cashflow: natuerliche Schnellerfassung, Kategorien, Bearbeiten/Loeschen, Monatsnavigation,
  Suche nach Haendler/Text/Kategorie/Betrag, Budgets, Monatsplan und Konto-/Bargeldlogik.
- Screenshot-Import nutzt den bestehenden OpenAI-Schluessel serverseitig, erzeugt nur
  Vorschlaege, dedupliziert gegen vorhandene Buchungen und speichert erst nach Nutzerpruefung.
- Analysebereich: Cashflow-Uebersicht, Kategorien, Haendler und Kategorie-Deep-Dive sowie
  getrennte Vermoegensanalyse. Alle Werte werden aus den bereits geladenen Finanzdaten
  berechnet, nicht in einer zweiten Analyse-Datenstruktur gespeichert.
- Ziele sind zentral, App-bearbeitbar und reine Zweckbindungen: Eine Zuweisung erfindet kein
  Geld. Fixkosten/Vertraege koennen in der App angelegt, geaendert und geloescht werden.
- Monatsplanung trennt Plan von echter Bestaetigung fuer Gehalt, Fixkosten und Sparrate.
  Reports duerfen Planwerte nicht als ausgefuehrt behaupten. Report-Erzeugung laeuft ueber
  eigene enqueue/worker/maintenance-Timer und nicht mehr als Nebenjob des Bot-Prozesses.
- Mentor/Future Assist nutzt echte Nutzerwerte und kurze, sachliche Sprache. Die Startkarte
  rotiert zwischen relevantem Budget-, Tracking-, Score- und Monatsstatus, statt immer denselben
  Hinweis zu zeigen. Fixkosten gelten bei Konsumfragen nicht als groesste freiwillige Ausgabe.
- Score und Rov.E Points sind erklaerbar, Tracking-Konstanz zaehlt ueber Zeit; geloeschte
  Testbuchungen nehmen unverdiente Trackingpunkte wieder zurueck.
- Push funktioniert als installierte PWA. Report-/Monatshinweise sowie die separat aktivierbare
  taegliche Tracking-Erinnerung nutzen dieselbe Push-Infrastruktur. Tracking-Push kommt nur um
  die lokale 20-Uhr-Stunde, wenn an diesem Tag keine Ausgabe erfasst wurde.
- Profil enthaelt Mentoring/Zugang, Rechtslinks, Datenexport, zweistufige Kontoloeschung und nur
  fuer den Admin sichtbares Kontrollzentrum mit Einladungen und Zugriff sperren/freigeben.

### Investments und Bankstatus

- ETF-, Aktien- und Kryptopositionen sind zentral persistent. Manuelle Werte, Stueckzahl,
  Ticker, Waehrung und optionale taegliche Kursaktualisierung sind vorhanden. Der Marktrefresh
  laeuft ueber `rove-market-refresh.timer`; Twelve Data und Leeway werden je nach Symbol genutzt.
- App-only-Persistenzfehler vom 14.08. ist behoben: Leere ETF-/Krypto-Platzhalter laufen im
  Bridge-Modus immer ueber `/v1/investments`, niemals nur ueber Browserzustand.
- Manuell angelegte, nicht live getrackte ETFs koennen dauerhaft geloescht werden. Der rote
  Glas-Chip verlangt zwei Tipps. Die API loescht nutzergebunden ueber die stabile Holding-ID,
  schuetzt live getrackte Positionen und alte Bot-Historie und vernichtet keinen bereits zuvor
  unzugeordneten Investmentbestand. Der Live-Test durch Furkan war erfolgreich.
- Die Bank-Anbindung ist nicht aktiv. Onboarding und Leerzustand sagen klar `Noch nicht aktiv`
  beziehungsweise `Werte selbst eintragen`; kein Klick darf eine echte Verbindung vortaeuschen.

### Betrieb und Sicherheit

- SSH: eigener Benutzer `roveadmin`, ED25519-Key, direkter Root-Login und Passwort-SSH deaktiviert,
  `MaxAuthTries 3`, `LoginGraceTime 30`; Root nur kontrolliert ueber `sudo`.
- UFW ist aktiv; oeffentlich erlaubt sind nur 22, 80 und 443 fuer IPv4/IPv6. Alte ungenutzte
  Container n8n, Dozzle und Portainer sind gestoppt, Restart-Policy `no`; Ports 5056, 5678,
  8000, 8888 und 9443 sind geschlossen. Fail2ban-Jail `sshd` ist aktiv.
- `.env` und `clarity.db` besitzen Rechte 600. System wurde auf Kernel 6.8.0-137 aktualisiert;
  Rov.E API, Bot, nginx, SSH und Fail2ban liefen nach dem Neustart aktiv.
- Eigene automatische SQLite-Backups laufen taeglich um 03:20 UTC mit Rotation. Backup-Service,
  `PRAGMA integrity_check`, Fremdschluesselpruefung und Restore in eine temporaere DB wurden
  erfolgreich getestet. Zusaetzlich wurde ein Hostinger-Snapshot erstellt.
- Noch offen fuer spaeteren Security-v2-Block: verschluesseltes Offsite-Backup, besseres
  Monitoring/Alarmierung, dedizierte Non-root-Service-Nutzer und bewusste Entscheidung zur
  Feldverschluesselung vor echten automatischen Bankdaten.

### Naechste sinnvolle Arbeit

1. App-Core mit echten neuen Nutzern testen: komplettes App-only-Onboarding, leere Zustaende,
   erster Monat, Screenshot-Import, Monatscheck und Report ohne Hilfe des Entwicklers.
2. Dynamische Mehrfachkonten als eigenen migrationssicheren Sprint planen; vorher Tabellen,
   Bewegungen, Sortier-Keys, UI-Deduplizierung und Rueckwaertskompatibilitaet gemeinsam festlegen.
3. Markttracking mit weiteren realen ETF-/Aktien-Symbolen testen und Providerfehler sauber
   beobachten; keine Brokerfunktion vortaeuschen.
4. Monitoring fuer API-Ausfall, Reportfehler, Backupfehler und auffaellige 5xx-Raten ergaenzen.
5. Vor Bezahl-Launch: Anwalt-Finalpruefung, Stripe/Paywall, finale AGB/Datenschutztexte und
   Support-/Abrechnungsprozesse. Open Banking, Broker-APIs und Kuendigungsservice bleiben eigene
   spaetere Module und sind heute nicht aktiv.

### Mehrfachkonten-Architekturaudit (14.08.2026)

- Der geforderte reine Analyseblock wurde ohne produktive Code-, DB-, API- oder UI-Aenderung
  abgeschlossen. Vollstaendiger Bericht: `work/DYNAMIC_ACCOUNTS_ARCHITECTURE_2026-08-14.md`.
- App-only-Nutzer besitzen nach abgeschlossenem Onboarding dieselben zentralen App-Kernrechte
  wie migrierte Telegram-Nutzer. Unterschiede sind nur fehlende Telegram-Historie/-Kommandos;
  gemeinsame offene Produktgrenzen sind geraetelokale Sachwerte und noch inaktive Bank-/Broker-
  sowie Zahlungsanbindungen.
- Empfehlung: Cash-only V1 mit `app_financial_accounts`, stabilen nutzergebundenen IDs,
  Standardkonten, nullable Referenzen, Dual-Write in Legacy-Aggregate und Feature-Flag. Alte
  Bot-Buchungen bleiben ehrlich ohne erfundene konkrete Konto-ID.
- Depots/Positionen, mehrere Immobilien, zentrale Sachwerte und Provider-APIs bleiben eigene
  spaetere Sprints. Die eigentliche Migration wird nicht als letzter Abendblock begonnen.

### Harte Leitplanken

- Keine bestehende Finanzlogik nebenbei umbauen. DB-first, Nutzergrenzen in jedem Schreib-/
  Loeschendpunkt, keine erfundenen Finanzwerte und keine lokal versteckten Korrekturen.
- Vor Python-Aenderungen die echte Tabelle und alle Leser pruefen; danach mindestens
  `python3 -m py_compile`. Geldlogik mit isolierter DB und erwarteten Zahlen testen.
- Nach App-Aenderungen alle eingebetteten JavaScript-Bloecke mit `node --check` pruefen und
  Mobile/PWA-Auswirkung beachten. Bestehendes Rov.E-Design erhalten, keine parallele App bauen.
- Reports, Bot, API und App teilen Daten: Auswirkungen immer bewusst pruefen. Secrets, `.env`,
  Datenbanken, WAL/SHM-Dateien und Backups niemals committen oder im Chat ausgeben.
- Furkan deployt selbst. Befehle immer klar in `Mac-Terminal` und `Server nach ssh rove`
  trennen; niemals verschachteltes SSH oder `sudo -i` in einen unklaren Mehrfachblock packen.

## Schnellerfassung und Budgets entschärft (12.08.2026)

- Der feste Beispiel-Chip `Gehalt 2450` wurde entfernt. Einkommen wird weiterhin ausschließlich
  mit dem persönlichen Betrag im Monatsplan bestätigt oder bewusst als eigene Einnahme erfasst.
- Budgetüberschreitungen bleiben rot sichtbar. Rov.E schlägt aber keine nachträgliche Umbuchung
  zwischen Kategorien mehr vor und verändert dadurch keine ursprünglich gesetzten Limits.
- Konten-Umbuchungen, zum Beispiel vom Girokonto auf das Tagesgeld, bleiben davon unberührt.
- Die Mentorleiste wird vor einem hinterlegten Zahltag nicht mehr täglich durch „Gehalt noch nicht
  bestätigt“ blockiert. Sie rotiert wieder zwischen Budget, Score, Rov.E Punkten und Tracking-
  Konstanz. Erst ab dem individuellen Zahltag kann die offene Bestätigung als dezenter Tageshinweis
  auftauchen; der Monatsplan öffnet sich wie bisher einmalig zum fälligen Zeitpunkt.
- Eine Budgetüberschreitung wird pro Kategorie und Monat einmal in der Glocke festgehalten, ohne
  Push. In der Mentorleiste hat sie höchstens zwei Tage Vorrang; die rote Budgetanzeige bleibt als
  dauerhafte sachliche Visualisierung bestehen.
- Die Mentor-Karte hat eine ruhigere Texthierarchie: Die Hauptaussage ist kleiner und leichter,
  während Budgetrahmen und Kategoriehinweis als gleich formatierte kleine Primärzeilen erscheinen.
- Die Summe der einzelnen Budgettöpfe erscheint nicht mehr in der Mentorleiste. Dort zählt nur der
  echte geplante beziehungsweise freie Gesamtrahmen; Kategorieüberschreitungen bleiben als kurze
  zweite Zeile sichtbar und die Topfdetails stehen ausschließlich im Cashflow.
- Budgetwarnungen werden in der gekoppelten App erst nach dem aktuellen API-Stand erzeugt. Der
  ältere Zugangssnapshot kann dadurch beim Start keine falschen Ereignisse mehr in die Glocke
  schreiben. Die Warnspeicherung wurde versioniert, damit bereits erzeugte falsche Zwischenstands-
  Meldungen nicht wiederhergestellt werden.
- Budgetüberschreitungen haben in der Glocke ein eigenes rotes Warnsymbol und verschwinden nach
  48 Stunden. Der Monatsmerker verhindert danach eine erneute Warnung derselben Kategorie. Normale
  Termin-, Report- und Fälligkeitseinträge behalten ihre bisherige Laufzeit von bis zu 14 Tagen.

## App Core v1 Review (11.08.2026)

- Security v1 ist fuer die aktuelle Beta abgeschlossen; der Fokus wechselt auf Produktqualitaet.
- Die naechsten 2-3 Wochen laufen als `Product Quality Sprint`.
- Ziel: Ein neuer Nutzer kann Rov.E ohne Erklaerung benutzen, versteht die Kernlogik und bekommt nach wenigen Tagen sichtbaren Mehrwert.
- Prioritaet: Onboarding, leere Zustaende, Mentor-Antworten, Monatsplanung, Cashflow, Score und Reports.
- Keine neue grosse Baustelle wie Bank-API, bis Rov.E ohne Bankanbindung rund genug ist.
- Detailreview und Testmatrix: `work/APP_CORE_V1_REVIEW_2026-08-11.md`.
- Erster Core-v1-Feinschliff umgesetzt: Reports haben nun einen echten Leerzustand, der Screenshot-
  Import erklaert vor dem ersten Bild klar die Pruefpflicht, und der Score ordnet junge Datenlagen
  ruhiger ein statt nur Zahlen zu zeigen.
- Geaendert wurde nur `work/rove-app/index.html`; kein Server-Neustart noetig.

## Report: Monatsplan nur als echte Ausführung ausweisen (27.07.2026)

- `report_engine.py` trennt Profil-Planwerte von bestätigten Monatsbewegungen.
- Einkommen, Fixkosten und Sparrate werden im Report nur dann als bestätigt bezeichnet,
  wenn sie im jeweiligen Monat in `app_monthly_plan_status` wirklich bestätigt wurden.
- Der KI-Text erhält denselben Status und darf eine vollständige Monatsbestätigung
  nicht mehr überschreiben.
- Juli bleibt damit ehrlich ein Plan-/Testmonat. Nach der Bestätigung im August kann
  der August-Report sauber sagen, dass Gehalt, Fixkosten und Sparrate erfasst wurden.

## Vermögenskurve: Zeitraum und Delta vereinheitlicht (27.07.2026)

- Der Header über der Kurve zeigt nun immer die Veränderung des gerade gewählten Zeitraums.
- Negative Entwicklung erscheint rot mit Pfeil nach unten, positive grün mit Pfeil nach oben.
- Die doppelte graue Delta-Zeile unter den Zeiträumen ist entfernt.
- Ein Tipp auf den Chip wechselt zwischen Prozent und dem exakten Euro-Unterschied.
- Der Zeitraum steht nur im aktiven Reiter; die Kennzahl darunter bleibt bewusst kompakt.

## Ziele: App übernimmt das Telegram-Hauptziel (28.07.2026)

- Das bestehende Hauptziel kann in der App jetzt zugeordnet, im Zielbetrag verändert und gelöscht werden.
- Die frühere Telegram-Sperre ist entfernt; Änderungen laufen direkt in die gemeinsame Datenbasis.
- Zielzuweisungen sind bewusst eine Zweckbindung: Sie erhöhen keinen Kontostand und erzeugen kein
  neues Vermögen. Das Geld bleibt z. B. auf dem Tagesgeld, wird aber dem Ziel zugeordnet.
- Die alte Anzeige, welche die monatliche Sparrate fälschlich als bereits gesparten Zielbetrag
  ausgegeben hat, ist entfernt. Ohne bereits erfasste Zweckbindung beginnt der Ziel-Fortschritt bei 0 EUR.

## Produktentscheidung

- Rov.E wird langfristig App-first.
- Telegram bleibt Beta- und Testkanal.
- Bestehende Beta-Nutzer bleiben kostenlos und werden nicht durch neue App-Arbeiten gestört.
- Regulärer App-Zugang später: 6,99 EUR pro Monat.
- Ablauf für neue Nutzer: Konto erstellen, E-Mail bestätigen, Paywall, Zahlung, Finanz-Onboarding.
- Zusatzleistungen: Finanz-Reset für 249 EUR einmalig und Rov.E Begleitung für 149 EUR monatlich.
- Bank-, Broker-, Krypto-APIs, automatisches Tracking und Vertragskündigungsservice bleiben Roadmap-Funktionen und werden erst beworben, wenn sie wirklich verfügbar sind.

## Aktuelle Architektur

- Telegram-Bot und Web-App schreiben in dieselbe SQLite-Datenbank auf Hostinger.
- App: `https://getrove.de/app/`
- Server-Code: `/root/clarity`
- App-API: `rove-app-api`, intern Port 5057, nginx unter `/app-api/`
- Bot-Service: `clarity-bot`
- Beta-Zugang aktuell über Telegram-State-Link und Pairing; dauerhaftes App-Konto ist noch nicht gebaut.

## App-Stand

- Echte Ausgaben, Budgets, Verträge, Ziele, Cash-Konten, Investments und Immobilien werden aus der Bot-Datenbank geladen.
- App-Ausgaben werden in die gemeinsame Datenbank geschrieben und erscheinen im Bot/Report.
- Girokonto, Tagesgeld und Bargeld sind getrennt.
- Umbuchungen verändern nur die Kontoverteilung, nicht das Gesamtvermögen.
- Immobilien und manuelle Investmentwerte bleiben beim App-Sync erhalten.
- Mentoring-Kachel mit Finanz-Reset und Rov.E Begleitung ist vorhanden.
- Rov.E-Zugang zeigt den späteren 6,99-EUR-Tarif als Beta-Vorschau; noch keine echte Zahlung.
- Zusaetzliche App-Ziele wie Notgroschen werden zentral in `app_goals` gespeichert. Sie
  ueberleben damit Schliessen, Neuoeffnen und einen spaeteren Geraetewechsel. Das einzelne
  Telegram-Hauptziel bleibt davon getrennt und wird nicht ueberschrieben.
- Der Zahltag-Schnellverteiler nutzt in der gekoppelten App ebenfalls nur zentral gespeicherte
  App-Ziele. Er veraendert niemals die hinterlegte monatliche Sparrate; eine Zielzuweisung ist
  eine Verteilung bestehenden Geldes, keine neue Sparrate.
- Neue, in der App angelegte Verträge werden zentral in `app_contracts` gespeichert. Sie werden
  direkt in die Fixkosten, das Monatsbudget und damit den Report einbezogen. Bestehende Bot-
  Verträge bleiben unverändert; sie werden weiterhin im Bot gepflegt.
- Die alte lokale Demo-Funktion fuer automatische Gehalts- und Fixkostenbuchungen ist im
  verknuepften App-Modus gesperrt. Die Beta-App erfindet deshalb keine Abbuchungen.
- Kündigungserinnerungen wurden ehrlich zurückgestuft: Die Glocke zeigt aktuell nur die nächsten
  Abbuchungen. Echte, serverseitige Kündigungserinnerungen kommen erst mit dem Vertragsservice.

## Letzter Fix, noch zu deployen

### Report-Validierung

- Der Webreport wurde gegen die PDF verglichen: Kategorie- und Ausgabendaten sind korrekt.
- Die PDF Money Map listet jetzt dynamisch bis zu sechs Kategorien statt nur drei auf.
- Im selben Paket: echte Tracking-Tage, Immobilien-Eigenkapital im Report-Nettovermoegen sowie dynamische Meilenstein-Skala und -Balken.
- Financial Story zeigt Immobilien-Eigenkapital jetzt auch transparent als eigenen dritten Vermoegensanteil neben Investments und Cash.
- Nach Deployment mit einem frischen `/testreport 2026-07` pruefen.

Die App-State-Brücke überträgt jetzt zusätzlich getrennt:

- ETF-Sparrate: `etfSparrate`
- Cash-Sparrate: `cashSparrate`
- Gesamt: `sparraten`
- Aufteilung: `sparratenParts`

Die Score-Logik verwendet nun das Einkommen aus `DATA.sts.income` statt nur nach einer Gehaltsbuchung im Ausgabenfeed zu suchen. Ohne Einkommen wird eine vorhandene Sparrate als `Erfasst` angezeigt und nicht fälschlich als schwach bewertet.

Geänderte Arbeitsdateien:

- `work/Calrity_Main/rove_app_state.py`
- `work/rove-app/index.html`

Prüfungen waren erfolgreich: Python-Kompilierung und JavaScript-Syntax.

Deployment:

1. Mac-Terminal: Arbeitsdateien in Main kopieren.
2. Mac-Terminal: `rove_app_state.py` committen und auf `feature_clarityr-report` pushen.
3. Mac-Terminal: `index.html` nach `/var/www/getrove/app/index.html` kopieren.
4. SSH-Server: `git pull origin feature_clarityr-report`.
5. SSH-Server: `systemctl restart rove-app-api`.
6. App komplett schließen und neu öffnen; bei Bedarf `/app` für einen frischen Zugang ausführen.

## Wichtige Monatslogik (22.07.)

- Die Glocke enthielt bisher Beispiel-Aktivitaeten wie "ETF-Sparplan ausgefuehrt" und
  "Haftpflicht heute abgebucht". Das waren reine Demo-Daten mit aktueller Uhrzeit,
  keine echten Buchungen und keine Abgaenge vom Konto.
- Fuer die verknuepfte Beta-App werden diese Beispiel-Eintraege jetzt entfernt. Die
  Glocke darf nur echte, vom Nutzer ausgeloeste Ereignisse zeigen.
- Hinterlegte ETF-/Cash-Sparraten sind eine Planung und werden im freien Monatsbudget
  reserviert. Sie werden nicht automatisch als echtes Investment gebucht.
- Eine echte Monatsbestaetigung erfolgt im Bot bewusst ueber `/investiert`. Erst dann
  werden Investment/Cash, Historie und die Savings-Execution-Punkte aktualisiert.
- Gehalt und Fixkosten duerfen ohne Bankanbindung ebenfalls nicht als tatsaechlich
  gebucht dargestellt werden. Sie sind Planung bzw. werden nur nach expliziter
  Bestaetigung oder spaeter per Bank-API als echte Bewegung gespeichert.

## Monatscheck (neu, noch zu deployen)

- Die App hat unter Einstellungen jetzt einen ruhigen `Monatscheck`; kein Push und
  keine Pop-up-Kette.
- Gehalt, Fixkosten und Sparrate werden weiterhin automatisch im Monatsbudget
  beruecksichtigt.
- Der Nutzer kann Gehalt und Fixkosten jeweils mit einem Tipp als `Bestaetigt`
  markieren oder wieder auf `Geplant` setzen.
- Die Sparrate ist ebenfalls antippbar: `Sparrate als ausgefuehrt bestaetigen`
  bucht ETF/Cash genau einmal in die zentrale Datenbasis, erstellt Investment-Historie
  und zaehlt auch fuer die Score-Umsetzung. Eine vorhandene `/investiert`-Bestaetigung
  wird erkannt und nicht doppelt gebucht.
- Am 1. eines Monats oeffnet sich der Monatscheck genau einmal beim ersten App-Start;
  danach bleibt Rov.E still. Der Monatscheck hat zudem einen klaren Schliessen-Button.
- Die Bestaetigung wird in `app_monthly_plan_status` pro Nutzer und Monat gespeichert.
  Sie erzeugt absichtlich keine künstliche Kontobewegung und veraendert weder
  Girokonto noch Vermoegen.
- Geaenderte Dateien: `rove_app_state.py`, `rove_app_api.py`, `work/rove-app/index.html`.

## App-Feinschliff Vermoegen (22.07.)

### Vereinfachte Kontofuehrung (23.07.)
- Detailansicht fuer Girokonto, Tagesgeld und Bargeld auf zwei klare Aktionen reduziert:
  - `Kontostand aendern` setzt den aktuellen Betrag und deckt auch Nachtraege/Geschenke ab.
  - `Umbuchen` verschiebt einen Betrag gezielt zwischen Girokonto, Tagesgeld und Bargeld, ohne das Gesamtvermoegen zu veraendern.
- Die verwirrenden Zusatzaktionen `Direkt erhoehen` und `Stand korrigieren` wurden entfernt.
- App-Hoehe auf dynamische sichtbare iPhone-Hoehe umgestellt, damit die untere Navigation ohne Leerraum buendig abschliesst.
- Die `Fertig`-Taste der iPhone-Tastatur funktioniert fuer Kontostand aendern und Umbuchen wieder korrekt.

### Investment-Ansicht (23.07.)
- Krypto und ETF/Aktien zeigen jetzt klarer: Gesamtwert, einzelne Positionen und einen eventuellen `Restbetrag`.
- Ein Restbetrag wird sichtbar hervorgehoben und kann per Tipp einer konkreten Position zugeordnet werden, ohne das Gesamtvermoegen doppelt zu zaehlen.
- Manuelle Positionen sind als solche markiert und koennen direkt aktualisiert werden; vorhandene Depotpositionen bleiben als echte, aus dem Bot uebernommene Daten erkennbar.
- Die iPhone-Tastatur fuehrt beim Hinzufuegen einer Investment-Position jetzt sauber durch Name → Wert → Speichern.

### Frischer Bot-App-Stand (23.07.)
- Beim Zurueckkehren in die App und alle 45 Sekunden waehrend sie sichtbar offen ist, werden die gemeinsamen Daten aus der Rov.E-Datenbank neu geladen.
- Im Hintergrund fragt die App nichts ab. Das schont Akku und Server, ohne dass Telegram-Buchungen beim aktiven Nutzen lange unsichtbar bleiben.

### Lesbarkeit (23.07.)
- Bereichsüberschriften in `Verträge` sowie die Monatsüberschrift im `Cashflow` verwenden jetzt die helle Hauptschrift statt eines blassen Grautons.
- Die Tages-Trenner im Cashflow (`Heute`, `Gestern`, Datum) sind ebenfalls hell und klar lesbar.

### Karten-Kontrast (23.07.)
- Dunkle Karten wurden minimal heller, ihre Kontur etwas klarer und mit einem sehr weichen Schatten versehen.
- Die Farbwelt bleibt unveraendert; die Kacheln heben sich nur besser vom Hintergrund ab.

### Händlerlogos in Buchungen (23.07.)
- Bekannte Händler wie Lidl, Aldi, Rewe, Kaufland, Amazon, Netflix und Aral erhalten in Buchungslisten und der Buchungsdetailansicht ihr echtes Logo.
- Die Logos werden direkt vom Brandfetch-CDN geladen. Dabei wird nur eine feste Händler-Domain abgefragt, nie Betrag, Nutzer-ID oder andere Finanzdaten.
- Unbekannte Händler, freie Texte und kleine lokale Geschäfte bleiben bewusst beim bisherigen farbigen Buchstaben-Icon. So wird kein Händler falsch dargestellt.
- Brandfetch erlaubt die direkte Logo-Einbindung kostenlos bis zu einem fairen Limit von 500.000 Abrufen pro Monat. Die verwendete Client-ID ist laut Anbieter für diese öffentliche Browser-Einbindung vorgesehen.

### Vermögens-Icons (23.07.)
- Die Vermögensübersicht nutzt jetzt ein eigenes, hochwertigeres Rov.E-Set statt einfacher Standard-Piktogramme: Bank, Tagesgeld, Portemonnaie, Wertpapier-Chart, Bitcoin, Immobilie und Sachwerte.
- Das Bitcoin-Symbol ist als klar erkennbare Coin-Marke umgesetzt; die übrigen Vermögensarten bleiben bewusst markenneutral.
- Händlerlogos und Vermögens-Icons bleiben getrennt: echte Marken nur bei Buchungen, einheitliche Rov.E-Symbole bei Konten und Vermögensarten.

## App-API Härtung (23.07.)

## Kontostand- und Sync-Härtung (25.07.)

- Das Girokonto darf bei einer Ausgabe ins Minus laufen. Beispiel: 100 EUR Giro minus
  600 EUR Kartenausgabe ergibt korrekt -500 EUR. Tagesgeld und Bargeld bleiben gegen
  negative Kontostände geschützt.
- Eine Barzahlung wird bei zu wenig Bargeld abgelehnt, statt eine Ausgabe zu speichern,
  die der Kontostand nicht decken kann.
- Wenn der App-POST für eine Ausgabe fehlschlägt, bleibt die Buchung nicht mehr als lokale
  Scheinbuchung sichtbar. Die App entfernt sie lokal und gleicht danach erneut mit der DB ab.
- Python- und JavaScript-Syntaxprüfung erfolgreich. Deployment und echter Live-Grenzfalltest
  stehen noch aus.

- Der öffentliche Health-Check verrät keinen Datenbank- oder Serverpfad mehr.
- API-Antworten sind mit `no-store`, `nosniff` und einer restriktiven Referrer-Regel gegen ungewolltes Zwischenspeichern und falsche Inhaltserkennung abgesichert.
- Nur `getrove.de` bzw. `www.getrove.de` darf aus einem Browser heraus Daten in der API verändern.
- Der achtstellige Telegram-Verbindungscode ist pro IP auf acht Versuche innerhalb von fünf Minuten begrenzt. Das schützt vor Rateversuchen, ohne normale Nutzer zu nerven.

## App-Konto / E-Mail-Login (23.07.)

- Brevo-Voraussetzungen sind geklärt: Absender `info@getrove.de` ist verifiziert und Server-IP `187.124.0.243` ist bei Brevo autorisiert.
- Live-Test ist erfolgreich: E-Mail-Code kommt an, Code-Verifikation funktioniert und die App bleibt nach dem Schliessen eingeloggt.
- Brevo blockierte zuerst die IPv6-Adresse `2a02:4780:79:5c24::1`; sie musste zusätzlich zu `187.124.0.243` autorisiert werden.
- Die App-API hat vorbereitete Endpunkte für E-Mail-Code-Login:
  `POST /v1/auth/request-code`, `POST /v1/auth/verify-code`, `GET /v1/auth/me`, `POST /v1/auth/logout`.
- Neue Tabellen: `app_accounts`, `app_login_codes`, `app_sessions`.
- Login-Codes werden nur gehasht gespeichert und laufen nach 10 Minuten ab.
- Sessions werden als HttpOnly-Cookie gespeichert, Standardlaufzeit 180 Tage.
- Bestehende Telegram-Beta-User werden beim ersten E-Mail-Login über ihren Telegram-App-Code mit der bisherigen `user_id` verknüpft. Danach reicht E-Mail-Code bzw. die gespeicherte Session.
- Komplett neue App-only-Accounts bleiben bewusst noch nicht freigeschaltet, bis Paywall/App-Onboarding sauber definiert ist.
- Benötigte Server-Env vor Live-Test:
  `BREVO_API_KEY`, `ROVE_APP_AUTH_SECRET`, optional `ROVE_LOGIN_FROM_EMAIL=info@getrove.de`, `ROVE_LOGIN_FROM_NAME=Rov.E`.
- Geänderte Dateien: `rove_app_api.py`, `work/rove-app/index.html`.

- Glocke/Benachrichtigungen: Der blaue Punkt in der App wird nicht mehr dauerhaft angezeigt.
  Rov.E merkt lokal, bis zu welchem Aktivitätszeitpunkt die Glocke gelesen wurde. Der Punkt erscheint
  nur noch bei echten neuen Aktivitäts-Einträgen und verschwindet beim Öffnen der Glocke.
- Geänderte Datei: `work/rove-app/index.html`.

- Das Konten-Detail fuer Girokonto, Tagesgeld und Bargeld wurde sprachlich und visuell beruhigt.
- Statt einer langen Erklaerflaeche zeigt Rov.E jetzt drei klare Aktionen:
  `Direkt erhoehen`, `Kontostand setzen` und bei Tagesgeld/Bargeld zusaetzlich
  `Zwischen Konten verschieben`.
- Die Hinweise unter den Buttons erklaeren jetzt sauber den Unterschied:
  neues Geld erhoeht Vermoegen, eine Korrektur setzt nur den Stand, Umbuchen veraendert
  nur die Verteilung.
- Geaenderte Datei: `rove-app/index.html` und synchronisiert in `work/rove-app/index.html`.

## App-Sprache / Mentor (24.07.)

- Der kleine Rov.E in der Mentor-Karte wurde optisch heller gesetzt, damit er naeher an der
  eigentlichen weissen Rov.E-Marke liegt und weniger wie ein blauer Telegram-Bot wirkt.
- Der Fallback im App-Mentor wurde bewusst kurz gehalten:
  `Das habe ich nicht verstanden. Stell die Frage bitte etwas konkreter.`
  Kein Statusblock, kein Support-Bot-Text, kein kuenstliches Entschuldigen.
- Die wichtigsten festen Mentor-Antworten wurden sprachlich geglaettet:
  Budget, Score, Vertraege, Abos, Vermoegen, Ausgaben, Sparrate, Zielprognose
  und Leisten-Koennen antworten jetzt kuerzer, direkter und fuehrender.
  Ziel: Rov.E soll wie ein ruhiger Finanzbegleiter wirken, nicht wie ein
  Online-Shop-Agent oder eine kaputte KI.
- Neue Leitlinie fuer App-Sprache: erst Ergebnis, dann Einordnung, dann nur bei
  Bedarf naechster Schritt. Keine langen Rechtfertigungen und keine
  generischen Statusbloecke bei unklaren Fragen.
- Budgetvorschlaege sind jetzt sicherer: Ein Klick auf `Budget vorschlagen`
  oder die Budget-Kachel ueberschreibt bestehende Budgets nicht mehr direkt.
  Rov.E zeigt erst eine Vorschau im Mentor-Chat. Erst `Budget uebernehmen`
  speichert den neuen Rahmen. `Budget verwerfen` laesst alles unveraendert.
  Wenn ein Vorschlag offen ist, aendert z. B. `Restaurants auf 150€` nur den
  Vorschlag, nicht das echte gespeicherte Budget.
- Budgetvorschlaege rechnen jetzt mit dem geplanten Monatsrahmen:
  Einkommen minus Fixkosten minus Sparrate. Nicht mehr mit dem Restbudget nach
  bereits getrackten Ausgaben. Grund: Sonst entstehen spaet im Monat absurde
  Vorschlaege wie 90 EUR Lebensmittel oder 30 EUR Mobilitaet.
- App-Coach kann jetzt die Onboarding-/Profilbasis direkt beantworten:
  Einkommen, Fixkosten, Sparrate inklusive ETF/Cash-Aufteilung,
  Budgetrahmen vor Ausgaben, bereits ausgegeben und aktuell noch frei.
  Beispiel-Fragen: `Wie viel verdiene ich?`, `Zeig meine Basisdaten`,
  `Wie hoch sind Fixkosten und Sparrate?`.
- LLM-Integration in der App ist bewusst auf nach dem echten Report-Test am 01.08. verschoben.
  Reihenfolge bleibt: feste DB-first-Logik zuerst, LLM nur als zweite Ebene fuer Formulierung
  und nicht eindeutig abgedeckte Fragen. Das LLM darf keine Finanzdaten erfinden und bekommt nur
  vorberechnete App-Daten als Kontext.
- Geaenderte Datei: `work/rove-app/index.html`.

## Arbeitsdatei der App – nur noch ein Pfad (25.07.)

- Gültig ist ausschliesslich `work/rove-app/index.html` unterhalb von
  `/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/`.
  Genau diese Datei wird bearbeitet und genau diese wird deployt.
- `/Users/furkanaltan/Documents/Codex/rove-app/index.html` ist ab sofort tot: nicht mehr lesen,
  nicht mehr beschreiben, nicht mehr als Referenz zitieren.
- Frühere Einträge in dieser Datei, die von einer Synchronisation beider Kopien sprechen
  (z. B. `rove-app/index.html` und synchronisiert in `work/rove-app/index.html`), sind damit überholt.
- Grund: Furkan und Codex arbeiten im last-login-Pfad, Claude hat zusätzlich in den zweiten Pfad
  gespiegelt. Zwei Kopien derselben Datei führen dazu, dass jemand am falschen Stand arbeitet und
  der Fehler erst beim Deploy auffällt.
- Vor jedem Edit die Datei frisch lesen. Furkan, Codex und Claude arbeiten teilweise parallel daran.
- Python-Dateien bleiben beim bisherigen Weg: `work/Calrity_Main/` → Main-Repo → Git → `/root/clarity`.
  Nur die `index.html` geht per scp, weil sie ausserhalb des Repos unter `/var/www/getrove/app/` liegt.
  Diese beiden Wege wurden am 25.07. einmal verwechselt; die Trennung bitte beachten.

## Buchungen löschen – gefixt und deployt (25.07.)

Ausgangslage: Im gekoppelten App-Modus liessen sich Buchungen nicht dauerhaft löschen. Der
Löschen-Button war nie defekt. Er entfernte die Buchung nur im Browser-Speicher; der
45-Sekunden-Refresh lud sie danach wieder aus der Datenbank. Im reinen Profil-Modus funktionierte
Löschen immer, weil dort kein Server zurückschreibt. Das Löschen war im gekoppelten Modus also
noch nie dauerhaft möglich, es war kein Folgeschaden einer früheren Änderung.

Drei Lücken waren die Ursache:

- `_build_tx()` in `rove_app_state.py` lieferte keine Server-ID mit. Die App konnte gar nicht
  benennen, welche Datenbankzeile gemeint ist.
- Es gab nur `POST /v1/expenses`, keinen DELETE-Endpunkt.
- Der Lösch-Handler in der App rief den Server nie.

Umgesetzt:

- `rove_app_state.py`: `_build_tx()` selektiert `id` mit und liefert sie als `sid` pro Buchung aus.
- `rove_app_api.py`: neuer `DELETE /v1/expenses/<id>` mit `WHERE id = ? AND user_id = ?`,
  plus eigene OPTIONS-Route für den Browser-Preflight.
- `work/rove-app/index.html`: neue Funktion `syncExpenseDeleteToServer()`, aufgerufen im Lösch-Handler.

Wichtig für spätere Arbeiten, der `sid`-Vertrag:

- Das Feld heisst bewusst `sid`, nicht `id`. Die App vergibt eigene lokale IDs (`TXID`);
  ein mitgeliefertes `id` würde in allen drei Lade-Sequenzen sofort überschrieben.
- Wer `_build_tx()` anfasst, muss `sid` drin lassen. Ohne `sid` ist das Löschen wieder wirkungslos,
  ohne dass sonst irgendetwas sichtbar kaputtgeht.
- Der DELETE braucht zwingend `user_id` im WHERE. Sonst könnte ein gültiges Token fremde Zeilen
  löschen, indem es IDs durchprobiert.
- Ein 404 gilt in der App als Erfolg (Doppelklick, oder der Bot war per `/undo` schneller).
- Schlägt der Server-Call fehl, verschwindet die Buchung lokal trotzdem sofort, es erscheint ein
  Hinweis und der Refresh holt den echten Datenbankstand zurück. Bewusst sichtbar statt still divergent.
- Server-seitig laufen alle drei Tx-Lade-Pfade über `build_live_app_data` → `_build_tx`.
  Ein Fix deckt damit `/v1/state`, `/v1/transactions` und den State-Link gemeinsam ab.

Geprüft: Server end-to-end gegen eine Kopie der `clarity.db` (Original unangetastet), inklusive
Fremdzugriff (fremde Zeile bleibt stehen), Doppel-Delete, fehlendem Token und fremdem Origin.
App im lokalen Preview gegen einen gestubbten Server geprüft, keine Konsolenfehler.

Status: deployt am 25.07., `rove-app-api` und `clarity-bot` neu gestartet, Service läuft.
`clarity-bot` muss dabei mit neu starten, weil `bot.py` das Modul `rove_app_state` importiert und
Python es im laufenden Prozess zwischenspeichert.

Bekannte Grenze, kein Fehler: `_build_tx()` filtert auf den laufenden Monat. Ältere Buchungen sind
in der App weder sichtbar noch löschbar. Laut Furkan irrelevant, alle Daten beginnen im Juli.

## Oberflächen-Feinschliff (25.07., deployt)

### Mentor-Karte, Schrift und Farbe

- Die Budgettopf-Zeile war durchgehend gleich schwer gesetzt und las sich dadurch komplett als Warnung.
- Jetzt: `Insgesamt noch X frei ·` fett als Hauptaussage, `Budgettöpfe: X` normal daneben, und der
  Hinweis `<Kategorie> X über Plan` normal statt halbfett.
- Zusätzlich die Farbe vereinheitlicht: `Budgettöpfe: X` stand weiss, der Über-Plan-Hinweis grau —
  zwei Nebenaussagen in zwei Farben. Beide liegen jetzt auf `--muted`. Nur der fette Frei-Betrag
  bleibt weiss und damit die einzige laute Aussage der Karte.
- Umgesetzt über zwei Spans `.m-lead` und `.m-sec` innerhalb der Mentor-Zeile, damit nur diese eine
  Zeile betroffen ist. Die übrigen Mentor-Zustände behalten bewusst ihr bisheriges Gewicht.

### Hell-Modus, Creme-Kacheln lesbar gemacht

- Im Hell-Modus waren die Creme-Kacheln praktisch unsichtbar. Ursache: die Kacheln setzen
  `color:#F4F1EA`, und die Icon-Striche zeichnen mit `currentColor` — also creme auf hellem Grund.
- Neue Regel unter `:root[data-theme="light"]` für `.prow .pic.is-white` sowie alle Kacheln mit der
  Creme-Tint (`.gicon`, `.logo`, `.iic` mit `F4F1EA` im style). Kachel bleibt hell, die Icon-Striche
  werden `--ink`, also so dunkel wie der Zeilentext daneben.
- Betrifft Reports, Einstellungen, Sachwerte, Miete und alles Weitere mit dieser Tint.
- Dunkel-Modus bleibt unverändert creme, gegengeprüft.
- Gleiche Ursache und gleiche Lösung wie bei Glocke und Avatar eine Regel höher in der Datei.

### Kategorie-Badges aus der Cashflow-Liste entfernt

- Die Badges unter jedem Buchungsnamen sind raus. Begründung Furkan: jeder weiss, dass Lidl
  Lebensmittel sind; der Badge kostet nur Ruhe in der Liste.
- Die Kategorie selbst bleibt vollständig erhalten und ist nur nicht mehr dauernd sichtbar:
  - Im Buchungsdetail steht weiterhin `Tag · Kategorie`, plus die Chips zum Umkategorisieren.
  - Rov.E ordnet Buchungen unverändert automatisch ein.
  - Die Suche filtert weiterhin nach Kategorie und Alltagswörtern (`essen` findet Lebensmittel
    und Restaurant), obwohl kein Badge mehr sichtbar ist.
  - Budgets und Report rechnen unverändert mit den Kategorien.
- Betroffen sind nur `txRow` und `histRow`; beide werden ausschliesslich in `renderTx()` verwendet.
  `margin-bottom` bei `.tx .nm` entfiel mit, sonst säße der Name ohne Badge außermittig.

- Geänderte Datei: `work/rove-app/index.html`. Geprüft im lokalen Preview, hell und dunkel,
  keine Konsolenfehler.

Status: von Furkan am 25.07. per scp nach `/var/www/getrove/app/index.html` hochgeladen. Live.

## Mentor-Karte, Nebenzeilen auf ein Mass (25.07., DEPLOYT + abgenommen)

Furkan nach dem Deploy: „drei verschiedene Größen und Farben, pass die unteren zwei gleich an,
obere bleibt gleich." Die Farbe war bereits einheitlich (`--muted`), der Unterschied kam von der
Schriftgrösse: `.m-sec` erbte die 16px von `.m-free`, `.m-note` stand auf 12,5px. Grössere Schrift
in derselben Farbe wirkt heller — daher las es sich als drei Stufen.

- `.m-sec` ist jetzt baugleich mit `.m-note`: 12,5px, `line-height:1.4`, `--muted`, Gewicht 400.
- `.m-sec` zusätzlich `display:block`. Nötig, weil zwei gleich grosse Aussagen sonst auf einer
  Zeile kleben würden; als eigene Zeile steht sie exakt wie `.m-note` darunter.
- Deshalb entfiel das „·" am Ende von `.m-lead` und der Punkt am Ende von `.m-sec` — Inline-Trenner
  ohne Inline.
- `.m-nudge` (Report-/Ziel-Anstoss) auf dasselbe Mass gesetzt. Sonst wäre im ruhigen Zustand genau
  die dritte Grösse zurückgekommen, die weg sollte. Das blaue fette Stichwort bleibt, das ist der
  Klick-Anker.
- Oberste Zeile unverändert: 16px, Gewicht 700, weiss, Betrag blau/800.

Geprüft im Preview mit gemessenen Computed Styles, beide Zustände (Alarm und ruhig), keine
Konsolenfehler. Geänderte Datei: `work/rove-app/index.html`.

```
scp "…/work/rove-app/index.html" root@187.124.0.243:/var/www/getrove/app/index.html
```

## Bargeld-Abhebung wird jetzt gespeichert (25.07., DEPLOYT)

Furkan: „wenn ich sage ich hab 50 Euro Bargeld abgehoben bucht er das um, wenn ich die App
schliesse löscht er das wieder."

Ursache waren zwei Lücken, die zusammen griffen:

1. `syncExpenseToServer()` beginnt mit `if(e.transfer || e.a>=0) return;` — eine Abhebung ist
   `transfer:true`, der Server-Sync stieg also sofort aus.
2. `saveBridgeLocal()` filtert alle Namen aus `BRIDGE_BOT_ASSET_NAMES` heraus, und dort stehen
   **Girokonto und Bargeld** drin. Also auch keine lokale Sicherung.

Die Umbuchung lebte damit nur im Arbeitsspeicher. Wichtig: die Bargeld-Funktion war NICHT kaputt
und auch nicht gelöscht — der Server kann Cash-Konten längst über `/v1/accounts`, der `+`-Weg hat
den Endpunkt nur nie aufgerufen.

Neu: `syncCashWithdrawal(amount, reverse)` als gemeinsame Hülle über
`syncCashAccount("transfer", {from:"giro", to:"bargeld", amount})`. Aufgerufen an drei Stellen:

- **`+`-Weg** (`addEntry`, im `e.transfer`-Zweig) — schreibt die Abhebung serverseitig fest.
- **Coaching-Zeile** — neuer Handler `ans_cash_withdrawal()`, eingehängt VOR `ans_cash_income()`.
  Vorher passierte dort gar nichts: `ans_cash_transfer()` kennt nur buchen/überweisen/verschieben,
  „abgehoben" fiel durch die ganze Kette bis zur Namenssuche und traf dort das Asset „Bargeld" —
  es kam also nur eine Auskunft zurück. Fragen sind bewusst ausgenommen („kann ich 50 € abheben?",
  „wie viel habe ich abgehoben") — sonst bucht eine Rückfrage ungefragt Geld um.
- **Löschen einer Abhebung** (`txdDel`, `item.transfer`-Zweig) — mit `reverse=true`, sonst bliebe
  das Geld serverseitig verschoben, während die App es zurückbucht.

Bewusst NICHT gemacht: die Abhebung als Ausgabe mit Kategorie „Bargeld" zu speichern. Der Server
kennt diese Kategorie nicht (`APP_TO_BOT_CATEGORY`), sie würde als echte Ausgabe in Budgets, Bot
und Report zählen — ein Geldfehler.

Geprüft im Preview: sechs Formulierungen durch `roveAnswer()` geschickt, drei Abhebe-Varianten
treffen den Handler, Fragen und normale Ausgaben nicht; `parseEntry()` liefert `transfer:true`;
keine Konsolenfehler.

Die drei damals noch offenen Punkte sind mit dem Paket unten erledigt.

## Bargeld-Thema abgeschlossen (25.07., NOCH ZU DEPLOYEN — Python + index.html)

Alle drei geplanten Punkte umgesetzt. Kern der Lösung: eine eigene App-Tabelle
`app_cash_movements`. Sie merkt genau die zwei Fälle, die die Bot-Tabelle `expenses` nicht
abbilden kann. An `expenses` selbst wurde nichts geändert, der Bot bleibt unberührt.

| `kind` | Was es ist | Wirkung |
|---|---|---|
| `withdrawal` | Abhebung Girokonto → Portemonnaie, keine Ausgabe | eigene Buchungszeile „Bargeld abgehoben" |
| `payment` | echte Ausgabe, bar bezahlt | Ausgabe bleibt in `expenses`, Zeile merkt nur `bar` + abgezogenen Betrag |

### 1. Bar bezahlen geht vom Bargeldbestand ab

„30 Euro Döner mit Bargeld bezahlt" ist eine normale Ausgabe (Budget, Bot, Report rechnen
unverändert damit), zieht aber vom Portemonnaie. Umgesetzt NICHT über einen zweiten
`/v1/accounts`-Call, sondern über ein Feld `paid_cash` am bestehenden `POST /v1/expenses`:

- Ein Aufruf, eine Transaktion — Buchung und Bargeldstand können nicht halb gespeichert sein.
- Kein zweiter Call heißt auch kein zweites `refreshAppDataFromServer()` mitten im Speichern.
  Das hätte `DATA.tx` ersetzt und die gerade getippte Buchung für 45 s aus der Liste geworfen.
- Der Server merkt sich dabei, DASS bar gezahlt wurde. Ohne diese Notiz wäre nach einem Refresh
  nicht mehr erkennbar, woher das Geld kam — Löschen hätte es dem Girokonto gutgeschrieben.

**Doppelzählung geprüft, es gibt keine:** `POST /v1/expenses` senkt kein Konto. Serverseitig
reduziert eine Ausgabe nur `available` (Einkommen − Fixkosten − Sparraten − Monatsausgaben),
nie `current_cash` — genauso wie im Bot. Das Bargeld ist die einzige Zahl, die eine Ausgabe
serverseitig senkt, und nur beim Bar-Fall.

**Bewusst asymmetrisch, bitte gegenlesen:** Bar bezahlen senkt jetzt dauerhaft das Bargeld (und
damit das Gesamtvermögen). Eine Kartenzahlung senkt das Girokonto weiterhin NUR lokal in der
Anzeige; der nächste Refresh holt den Serverwert zurück. Begründung: Bargeld kann sich niemand
sonst korrigieren, ein Girokonto liest man ab. Wenn Kartenzahlungen ebenfalls dauerhaft vom
Girokonto abgehen sollen, ist das eine Änderung am Geldmodell des BOTS (`current_cash`) und
gehört getrennt entschieden — nicht nebenbei in der App.

### 2. Listenzeile „Bargeld abgehoben" überlebt den Refresh

`_build_tx()` liefert jetzt Ausgaben UND Abhebungen des laufenden Monats, nach Zeit gemischt
sortiert. Die Abhebung trägt bewusst `csid` statt `sid`:

- **Gefahr, die das verhindert:** mit `sid` hätte die App beim Löschen
  `DELETE /v1/expenses/<id>` gerufen — und damit eine fremde Ausgabe mit derselben Nummer
  getroffen. Der `sid`-Vertrag aus dem Lösch-Fix bleibt unverändert gültig.
- Neuer Endpunkt `DELETE /v1/cash-movements/<id>`: löscht die Zeile UND bucht das Geld
  zurück aufs Girokonto, in einer Transaktion. `user_id` steht im WHERE (gegen fremde IDs).
- Ist das Bargeld schon ausgegeben, wird die Rücknahme mit `cash_already_spent` abgelehnt statt
  Geld aufs Girokonto zu erfinden. Die App sagt das und lässt die Zeile stehen.
- Ein normales Umbuchen im Konten-Detail erzeugt weiter KEINE Zeile. Nur die App-Abhebung
  schickt `log:"withdrawal"` mit.
- Löschen einer bar bezahlten Ausgabe gibt genau den damals abgezogenen Betrag zurück, nie mehr.
  Wer bar mehr zahlt als hinterlegt ist, bucht die Ausgabe trotzdem; das Portemonnaie geht nur
  bis 0 und die Rückgabe entspricht diesem Teilbetrag.

### 3. Coaching-Zeile bucht den Bar-Fall

Vorher buchte sie ihn nicht — die Antwort-Kette hat überhaupt keinen Ausgaben-Zweig, „bargeld"
traf am Ende die Namenssuche und Rov.E gab nur den Portemonnaie-Stand zurück. Neu:
`ans_cash_expense()`, eingehängt direkt hinter `ans_cash_withdrawal()`. Absichtlich eng:
es braucht das ausdrückliche „bar/in bar/mit Bargeld bezahlt" (`isCashPayment`), Fragen sind
ausgenommen. Ein beliebiger Satz mit einer Zahl kann also keine Ausgabe auslösen. Allgemeines
Buchen per Chat („Kaffee 4,50") ist eine eigene Produktentscheidung und bleibt beim `+`-Weg.

Nebenbei korrigiert: `ans_expenses()` zählte Abhebungen als Ausgaben mit. Budgets und
`trackedOuts()` filterten sie schon über `fixedMerchantSet()`, die Gesamtsumme nicht.
Für den `+`-Weg und die Coaching-Zeile gibt es jetzt eine gemeinsame Funktion `commitEntry()`.

### Geprüft

- Server end-to-end gegen eine KOPIE der `clarity.db` (Original unangetastet), 15 Fälle:
  Abhebung erscheint und überlebt einen zweiten unabhängigen Abruf; stilles Umbuchen erzeugt
  keine Zeile; Bar-Ausgabe senkt nur Bargeld; Kartenzahlung lässt Bargeld unberührt; Löschen
  gibt korrekt zurück; Doppel-Delete → 404; ausgegebenes Bargeld → Rücknahme abgelehnt;
  Bar-Zahlung über den Bestand → Rückgabe nur des Teilbetrags; fremde Zeilen (Bewegung UND
  Ausgabe) bleiben stehen; ohne Token 401; fremder Origin 403; Abhebung aus dem Vormonat
  bleibt draußen.
- App im Preview: `+`-Weg und Coaching-Zeile gerechnet (Bargeld/Girokonto/Vermögen bei
  Buchen und Löschen), sechs Formulierungen durch `roveAnswer()` (Fragen buchen nicht),
  gesendete Request-Bodies mit gestubbtem `fetch` geprüft (`paid_cash`, `log:"withdrawal"`,
  Transfer schickt keine Ausgabe). Keine Konsolenfehler.
- `python3 -m py_compile` für beide Python-Dateien, JS-Syntax beider Script-Blöcke.

### Deployment (Python über Git, index.html per scp — nicht verwechseln)

Geänderte Dateien: `rove_app_state.py`, `rove_app_api.py` (bereits ins Main-Repo kopiert),
`work/rove-app/index.html`.

```bash
cd "/Users/furkanaltan/Documents/Project Clarity/Calrity_Main"
git add rove_app_state.py rove_app_api.py
git commit -m "Bargeld: Abhebungen und Bar-Zahlungen serverseitig speichern"
git push origin feature_clarityr-report
scp "/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/work/rove-app/index.html" root@187.124.0.243:/var/www/getrove/app/index.html
ssh root@187.124.0.243 "cd /root/clarity && git pull origin feature_clarityr-report && systemctl restart rove-app-api clarity-bot"
```

`clarity-bot` muss mit neu starten, weil `bot.py` das Modul `rove_app_state` importiert und
Python es im laufenden Prozess zwischenspeichert. Die Tabelle `app_cash_movements` legt sich
beim ersten Bargeld-Vorgang selbst an, es ist keine Migration nötig.

## Aufräumen 25.07. — was sich am Ablageort geändert hat

- **`Documents/Codex/rove-app/` ist KEINE Leiche.** Die doppelte `index.html` ist raus, aber der
  Ordner enthält die einzigen Kopien von `manifest.webmanifest`, `app-icon.png`, `logo.png`,
  `DATENMODELL.md`, `DEPLOYMENT.md`, `start.command`. Manifest und Icons gehören zur PWA. Der
  Ordner darf nicht gelöscht werden, es wird dort nur nicht mehr an der App gearbeitet.
- **`work/rove-app/index.ROLLBACK-2026-07-25.html`** ist der einzige verbliebene Rückfallstand der
  App (Stand vor den drei UI-Änderungen vom 25.07.). Nicht bearbeiten, nicht deployen, nicht
  löschen, solange es keine Versionskontrolle gibt.
- Gelöscht: 19 alte `index.before_*.html`-Zwischenstände, 3 Landingpage-Backups, 8 `tmp_pdf_*`-
  Ordner aus den PDF-Experimenten, sämtliche Python-Caches. 53 MB → 33 MB.
- Behalten: die zwei `waitlist.backup-*.html`. `waitlist.html` ist www.getrove.de live und es gibt
  kein Git — die Backups sind die einzige Rückfallebene. Ebenso behalten: `rove-app/_review`,
  `work/report_preview`, `work/reference` (visuelle Referenzen).
- **`Documents/Codex/` steht unter keiner Versionskontrolle.** Jede gelöschte Datei ist endgültig
  weg. Vor grösseren Aufräumaktionen daher immer prüfen, ob eine Datei die letzte ihrer Art ist.

## Bargeld-Abschluss und Kanal-Sicherheit (25.07., NOCH ZU DEPLOYEN)

Codex hat Claudes gesamten Bargeld-Workflow gegen Arbeitskopie, Git-Repo und eine wegwerfbare
Kopie der `clarity.db` geprüft.

- Der sichere Bargeld-Kern aus Commit `1443e35` ist korrekt:
  - Abheben: Girokonto -50 €, Portemonnaie +50 €, keine Ausgabe.
  - Bar bezahlen: echte Ausgabe in Budget/Report, Portemonnaie sinkt.
  - Löschen: nur der damals wirklich abgezogene Bargeldbetrag kommt zurück.
  - Abhebung löschen: Portemonnaie zurück aufs Girokonto; bereits ausgegebenes Bargeld blockiert
    die Rücknahme statt Geld zu erfinden.
- Nach Furkans Klarstellung „keine neuen Ausgaben mehr in Telegram, App ist das Produkt" wurde
  der Dauerabzug für neue App-Kartenzahlungen fertiggestellt:
  - normale App-Ausgabe senkt dauerhaft das Girokonto;
  - Barzahlung senkt dauerhaft das Portemonnaie;
  - Löschen erstattet genau den wirklich abgezogenen Betrag auf das Ursprungskonto;
  - API liefert die drei exakten Kontostände zurück, damit die Anzeige auch bei Teilabzug sofort
    dem Serverstand entspricht.
- Bereits erfasste App-Ausgaben ab 21.07. werden mit
  `backfill_app_card_expenses.py` einmalig nachgebucht. Das Skript läuft standardmäßig nur als
  Vorschau, erkennt ausschließlich `Via Rov.E App` ohne vorhandene Kontobewegung, ist
  idempotent und erstellt bei `--apply` automatisch ein SQLite-Backup.
- Alte Telegram-Ausgaben werden nicht rückwirkend vom Girokonto abgezogen. Furkan nennt den
  20.07. (Lidl 44 €) als letzte echte Telegram-Ausgabe; so vermeiden wir Doppelzählung.
- Echte Mischkanal-Lücke geschlossen:
  - `/undo` und natürliche Löschsätze im Telegram-Bot geben bei einer bar bezahlten App-Ausgabe
    den abgezogenen Betrag wieder ins Portemonnaie.
  - `/editlast` passt bei einer bar bezahlten App-Ausgabe Bargeldstand und gespeicherte
    Kontowirkung mit an.
  - Alte Bot-Ausgaben ohne App-Kontowirkung bleiben unverändert; es wird kein Geld erfunden.

Geänderte zusätzliche Datei: `work/Calrity_Main/bot.py`.

Prüfungen:

- `py_compile`: `bot.py`, `rove_app_api.py`, `rove_app_state.py` grün.
- JavaScript aus `work/rove-app/index.html`: `node --check` grün.
- API-End-to-End gegen DB-Kopie: Abheben → bar zahlen → löschen → Abhebung löschen grün.
- Bot-DB-Test gegen DB-Kopie: Bar-Ausgabe 30 € → `/editlast` 20 € → löschen; Portemonnaie
  endet wieder exakt beim Startwert, Bewegungszeile ist entfernt.
- Karten-Ausgabe 44 €: Giro 1.000 € → 956 €; Löschen → exakt 1.000 €.
- Backfill-Test: zwei App-Ausgaben über 64 € werden einmalig abgezogen; zweiter Lauf findet
  null Kandidaten und zieht nichts erneut ab.

Noch nicht deployt: Karten-Dauerabzug, exakte Frontend-Kontosynchronisierung und Backfill-Skript.
Die Bot-Kanal-Sicherung ist bereits Commit `dc27fec`; dafür ist kein weiterer Bot-Umbau nötig.
Zu deployen sind nur `rove_app_api.py`, `rove_app_state.py`,
`backfill_app_card_expenses.py` über Git sowie `work/rove-app/index.html` separat per `scp`.

Erledigt: Schrift und Mentor-Leiste hat Furkan am 25.07. live abgenommen („Schrift und
Mentorleiste sind okay"). Die Nebenzeilen bleiben grau — damit ist die Frage geschlossen.

## Auto-Update bestätigt (25.07.)

Das eingebaute Auto-Update greift. Furkan hat die neue Version erhalten, ohne die App vom
Homescreen löschen und neu installieren zu müssen. Damit ist "App löschen und neu hinzufügen"
endgültig kein zulässiger Fix mehr — dabei stirbt der Login. Gilt für Claude und Codex
gleichermassen.

## Kategorie-Korrektur zentralisiert (25.07., noch zu deployen)

- Die Kategorieänderung im Buchungsdetail war bisher nur lokal im Browser gespeichert. Nach dem
  45-Sekunden-Refresh blieb die ursprüngliche Kategorie in der Datenbank, im Bot und im Report.
- Der Fix schreibt nun die konkrete `expenses`-Zeile zentral um und speichert den Händler in der
  vorhandenen Tabelle `user_category_rules`. Die Tabelle nutzt auch der Telegram-Bot bereits.
- Folge: Korrigiert ein Nutzer zum Beispiel `Snackautomat` einmal auf `Lebensmittel`, bleibt die
  aktuelle Buchung in App, Bot, Budget und Report korrekt und künftige Snackautomaten werden in
  beiden Kanälen automatisch so eingeordnet.
- Geprüft: Python-Kompilierung, JavaScript-Syntax und ein API-End-to-End-Test gegen eine frische
  SQLite-Testdatenbank (Buchung + Regel wurden beide auf `LEBENSMITTEL` gespeichert).

## Bargeld-Sprache verbreitert (26.07., NOCH ZU DEPLOYEN — nur `index.html` per scp)

Furkan: „wenn ich zb sage 1000 euro bargeld ausgeben schreibt er ausgabe einfach vom normalen
konto ab". Ursache: `isCashPayment()` verlangte zwingend `bezahlt|gezahlt|gekauft`. „ausgeben"
stand in keiner der fünf Regeln, der Satz fiel also auf den Giro-Standardpfad zurück.

Der Bargeld-Wortschatz liegt jetzt in **einer** Quelle (`CASH_WALLET`, `CASH_SPEND`,
`CASH_FROM_WALLET_SRC`, `CASH_NEAR`), die sich Quick-Add, Coaching-Zeile und die
Händlernamen-Bereinigung teilen. Getrennte Listen waren der eigentliche Fehler: die Erkennung
verstand „bargeld", die Namensbereinigung kannte dieselbe Wendung nicht und hätte
„Bargeld ausgeben" als Händler in die DB geschrieben. Wer den Wortschatz erweitert, ändert
nur diese Konstanten — nicht die einzelnen Regexe.

Neu verstanden: `bargeld/bar/cash + ausgeben|ausgegeben|kaufe|hingelegt|abgedrückt` in beiden
Wortstellungen, `aus dem Portemonnaie / Geldbeutel / Geldbörse / Brieftasche`, `vom Bargeld`,
`Döner 12 bar` (nur am Satzende), `Kiosk 8 cash`. Abhebung zusätzlich: `am Automaten`, `ATM`,
`Bankomat`, `Bargeld geholt/gezogen`.

Bewusste Grenzen, bitte nicht ohne Grund aufweichen:

- **„Bar" ist doppeldeutig.** „Bar 30 Euro" und „Kaffee 12 in der Bar" sind die Kneipe und laufen
  weiter übers Girokonto. Nur ein `bar` am Satzende ohne `in/der/die/eine …` gilt als Barzahlung.
  `cash` ist dagegen eindeutig und zählt überall.
- **Abhebung schlägt Barzahlung.** `isCashPayment()` gibt bei einem Abhebe-Satz hart `false`
  zurück, sonst würde „50 Bargeld geholt" als Ausgabe im Budget landen.
- **Absicht ist keine Buchung.** In der Coaching-Zeile lehnt `ans_cash_expense()` jetzt zusätzlich
  `will|möchte|würde|plane|hätte|dürfte` ab. Ohne diesen Riegel hätte „ich will 1000 Euro bar
  ausgeben" nach der Erweiterung eine echte 1.000-Euro-Ausgabe gebucht.

Nebenbei: Füllwörter am Rand des Händlernamens fliegen raus — „30 Bargeld für Döner ausgegeben"
heißt jetzt `Döner` statt `Für döner`, „Kaffee 12 in der Bar" heißt `Kaffee` statt `Kaffee in der`.

Geprüft: 26 Sätze durch `parseEntry()` (Abhebung / bar / Giro), alle korrekt — sowohl in Node als
auch im echten Browser-Preview an der geladenen App. Fünf Frage- und Absichtssätze durch
`ans_cash_expense()`, alle abgelehnt. `node --check` über beide Script-Blöcke grün, keine
Konsolenfehler. Kein Python geändert, also **kein** Git-Deploy nötig — nur:

```bash
scp "/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/work/rove-app/index.html" root@187.124.0.243:/var/www/getrove/app/index.html
```

## Einnahmen werden endlich gespeichert + Dispo sichtbar (26.07., DEPLOYT + im Betrieb bestätigt)

Aus einem Fremd-Audit der Geldpfade („finde jeden Weg, wie ein Nutzer nach einer Buchung einen
falschen Kontostand sieht"). Vier Wege gefunden, **drei davon gefixt**.

### 1. Einnahmen gingen nie an den Server — der schwerste Fehler

`syncExpenseToServer()` stieg bei `e.a >= 0` aus. „Gehalt 2450" hob das Girokonto nur im
Browser; `refreshAppDataFromServer()` ersetzt `DATA.assets` nach 45 s durch die Serverwerte →
Geld UND Buchungszeile kommentarlos weg. **Warum das seit dem Karten-Dauerabzug richtig weh
tut:** Ausgaben senken das Konto seitdem dauerhaft, Einnahmen hoben es nie → der Kontostand
driftete systematisch nach unten. Vorher war es symmetrisch und fiel nicht auf.

Neu: `POST /v1/income` und Bewegungsart `income` in `app_cash_movements`.

- **Bewusst NICHT in `expenses`.** Dort würde die Einnahme als Ausgabe in Budget, Bot und
  Report gegengerechnet. Wer das später ändern will: das ist ein Geldfehler, kein Detail.
- **Bewusst NICHT als Änderung an `users.income`.** Das ist das monatliche Profil-Einkommen,
  keine einzelne Buchung.
- Die Zeile trägt `csid`, nicht `sid` — gleicher Vertrag wie bei den Abhebungen. Ein `sid`
  würde beim Löschen `DELETE /v1/expenses/<id>` auslösen und eine fremde Ausgabe treffen.
- `DELETE /v1/cash-movements/<id>` nimmt jetzt auch Einnahmen zurück. Bewusst **ohne**
  Deckungsprüfung: das Giro darf ins Minus (Furkans Entscheidung), sonst wäre eine längst
  ausgegebene Einnahme unlöschbar.
- Neue Spalte `label` in `app_cash_movements`, per `ALTER TABLE` nachgerüstet. Der Lesepfad
  `_cash_movements_this_month()` nutzt jetzt `SELECT *` — mit einer Spaltenliste hätte eine
  Datenbank ohne `label` einen `OperationalError` geworfen, und der Except-Zweig hätte **alle**
  Bewegungen des Monats verschluckt, also auch die Abhebungen.

### 2. Negatives Girokonto wurde in der Anzeige auf 0 geklemmt

`rove_app_api.app_cash_accounts()` erlaubte den Dispo (gewollt), `rove_app_state.get_app_cash_accounts()`
machte `max(0.0, …)` über **alle** Konten. Belegt per Test: DB hielt -500, `/v1/state` lieferte 0.
Die App zeigte direkt nach der Buchung -500 (aus der POST-Antwort) und 45 s später 0 — die
Überziehung unsichtbar, das Gesamtvermögen um genau diesen Betrag zu hoch. Das widersprach der
Zusage weiter oben in dieser Datei („-500 EUR korrekt"). Giro ist jetzt auch im Lesepfad
vorzeichenecht; Tagesgeld und Bargeld bleiben Guthabenkonten.

### 3. Fehlgeschlagener Sync ließ den Kontostand gesenkt

Der `catch` entfernte nur die Buchungszeile, nie die Kontowirkung — `refreshTransactionsFromServer()`
aktualisiert ausschließlich `DATA.tx`. Neu: `snapshotCashAccounts()` in `commitEntry()` **vor**
der lokalen Buchung, `rollbackFailedBooking()` stellt Zeile und Konten wieder her. Der Snapshot
muss aus `commitEntry` kommen — im Sync gemessen wäre er schon um den Buchungsbetrag verschoben.

### 4. Verlorenes Update bei parallelen Buchungen

Damals bewusst offen gelassen, um den Einnahmen-Fix allein deployen zu können.
**Inzwischen gefixt — siehe eigenen Abschnitt weiter unten.**

### Prüfungen

- `py_compile` für `rove_app_api.py` und `rove_app_state.py` grün, `node --check` über beide
  Script-Blöcke grün, keine Konsolenfehler.
- **End-to-End gegen eine Wegwerf-Kopie der `clarity.db`:** Giro 1.000 → `POST /v1/income` 2.450
  → `/v1/state` liefert 3.450 **und** die Zeile `{n:"Gehalt", cat:"Einnahme", a:2450, csid:1}` →
  Löschen → wieder exakt 1.000, Zeile weg.
- Dispo: Giro 1.000, Ausgabe 1.600 → POST-Antwort **und** `/v1/state` liefern beide -600.
- Barzahlung ohne Deckung wird weiter mit `cash_balance_insufficient` abgelehnt, Giro unberührt.
- In der geladenen App: Rollback stellt Girokonto und `sts.konto` exakt wieder her.

Deployt am 26.07. Furkan hat im echten Betrieb gegengeprüft: „Gehalt bleibt bestehen nach
App-Schliessung und 2 Minuten warten." Damit ist Fund 1 nicht nur im Test, sondern live belegt.

## Parallele Buchungen verlieren keinen Abzug mehr (26.07., NOCH ZU DEPLOYEN — nur Python)

Fund 4 aus dem Audit oben, jetzt als eigener Schritt.

### Was tatsächlich passiert ist

Die Beschreibung „App und Bot schreiben gleichzeitig" war irreführend. Der reale Auslöser ist
**die App gegen sich selbst**: `commitEntry()` ruft `syncExpenseToServer()` ohne `await` auf
(`index.html`), damit die Eingabe nicht eine Sekunde hängt. Bei schlechtem Netz ist der erste
POST noch unterwegs, wenn der zweite abgeschickt wird — Doppeltipp auf Speichern zählt genauso.
Auch das noch nicht gelaufene `backfill_app_card_expenses.py` schreibt parallel.

Im Server lag das `SELECT` der Kontostände **ausserhalb** jeder Transaktion: Pythons `sqlite3`
öffnet die Transaktion erst beim ersten schreibenden Statement. Zwei Requests lasen also
denselben Stand, rechneten beide von dort, der zweite überschrieb den ersten. Beide Ausgaben
standen in `expenses`, nur eine war vom Konto weg — ohne Fehlermeldung.

### Warum es dringender war als gedacht

Gemessen gegen eine Wegwerf-Kopie der `clarity.db`, mit der echten Flask-App und **ohne**
künstliche Verzögerung: acht gleichzeitige Buchungen à 30 EUR senkten das Giro um 30 statt um
240. Sieben von acht Abzügen gingen verloren. Die Annahme „selten" stimmte für zwei Requests,
nicht für mehr.

### Der Fix

Neue Funktion `begin_write(conn)` in `rove_app_api.py`: ein `BEGIN IMMEDIATE` als erste Zeile im
`with db() as conn`-Block. Damit liegt die Schreibsperre **vor** dem Lesen, der zweite Request
wartet und liest danach den bereits gesenkten Stand. Dazu `timeout=15.0` in `db()`, damit
Warten nicht in „database is locked" endet. `bot.py` verbindet mit `timeout=20`, wartet also
ebenfalls sauber.

Eingesetzt an den sechs Endpunkten, die einen Kontostand lesen und daraus einen neuen berechnen:
`/v1/monthly-plan`, `/v1/accounts`, `POST /v1/expenses`, `/v1/income`,
`DELETE /v1/expenses/<id>`, `DELETE /v1/cash-movements/<id>`.

**Regel für alle, die hier weiterbauen:** ein neuer Endpunkt, der einen Kontostand liest und
davon ausgehend einen neuen schreibt, braucht `begin_write(conn)` als erste Zeile. Reine Leser
wie `/v1/state` brauchen es nicht — die Sperre dort würde nur unnötig blockieren.
`POST /v1/expenses/<id>/category` bekommt es bewusst nicht: dort wird ein fester Wert
geschrieben, nicht aus einem gelesenen Stand hochgerechnet.

### Prüfungen

- `py_compile` grün.
- **Reproduktion vor dem Fix** (echte App, DB-Kopie): 2 parallele Buchungen → 30 EUR aus dem
  Nichts; 8 parallele Buchungen → 210 EUR aus dem Nichts. Nach dem Fix beide Fälle exakt richtig.
- **Regressionstest über alle sechs Endpunkte**, jeder Wert vorher ausgerechnet: Kartenausgabe,
  Abhebung, bar bezahlt, Ablehnung bei zu wenig Bargeld, Einnahme, Ausgabe löschen, Einnahme
  löschen, Dispo (Giro -400 erlaubt), sowie Ausgabe 50 und Einnahme 50 **gleichzeitig** → Giro
  unverändert. Alle 11 grün.

Zu deployen: nur `rove_app_api.py` über Git, danach `rove-app-api` neu starten.
`index.html` ist unverändert, kein scp nötig.

## Untere Leiste springt beim Start (26.07., NOCH ZU DEPLOYEN — nur `index.html` per scp)

Furkan: „wenn die Seite geladen ist, springt die Leiste unten hoch und runter", meistens beim
App-Start, manchmal nach dem Eintragen einer Ausgabe.

### Fund

`#app` stand auf `height:100vh;height:100dvh;min-height:100svh`. Der Kommentar direkt darüber
erklärt seit dem 17.07. **wörtlich mit demselben Symptom**, warum hier `svh` stehen muss: `dvh`
ändert sich auf iOS, während die Safari-Leiste ein- und ausfährt, die App reflowt und die fixe
Tabbar folgt der wandernden Unterkante. Der Fix war aber nur im Kommentar angekommen — in der
wirksamen Zeile stand weiter `dvh`. `min-height:100svh` fängt das nicht ab, weil bei `height`
die letzte Deklaration gewinnt. Jetzt `height:100svh`.

Die Regeln für die installierte App (`@media (display-mode: standalone)` und `html.is-standalone`)
bleiben bewusst auf `dvh`: dort gibt es keine Safari-Leiste, und die Tabbar soll die echte
Unterkante erreichen.

### Zwei Theorien, die sich beim Messen als falsch erwiesen haben

Beide hier notiert, damit sie niemand nochmal verfolgt:

- **`.tab{transition:.2s}` als `transition:all`** — wäre eine Erklärung für animierte Sprünge
  bei Layout-Änderungen. Computed Style zeigt aber `transform, opacity / 0.15s`, eine spätere
  Regel überschreibt die Kurzform. Kein `all`, also kein Effekt.
- **Font-Swap von Manrope ändert die Tabbar-Höhe** — gemessen mit erzwungenem Fallback-Font:
  Höhe 70 px und Oberkante 742 px in beiden Fällen, Differenz 0. Der Font ist ohnehin als
  data-URI inline, es gibt keinen Netzwerk-Nachlader.

### ⚠️ Der Fix greift für Furkans Fall NICHT — Ursache weiter offen

Nachgefragt und beantwortet: **Furkan öffnet die App immer über das Icon auf dem Homescreen**,
also als installierte PWA im Standalone-Modus. Genau dort erzwingen
`@media (display-mode: standalone)` und `html.is-standalone` weiterhin `height:100dvh`. Die
`svh`-Korrektur betrifft nur den Safari-Tab-Pfad und kann sein Springen nicht behoben haben.
Rückmeldung nach dem Deploy: „beim ersten Öffnen ist die kurz noch gesprungen." Er beobachtet,
wie oft es noch auftritt.

Die `svh`-Korrektur bleibt trotzdem drin: der Kommentar von 17.07. beschreibt sie als gewollt,
und im Safari-Tab ist das Verhalten belegbar falsch gewesen.

Das Verhalten auf echtem iOS konnte hier nicht geprüft werden. Das Preview läuft im Mock-Modus
auf dem Desktop; eine Messsonde über 4 Sekunden und 206 Frames zeigte dort eine völlig stabile
Leiste (Oberkante konstant 742).

**Nächster Schritt: am Gerät messen, nicht weiter raten.** Offene Hypothesen für Standalone:
`env(safe-area-inset-bottom)` löst sich verzögert auf, wodurch sich Padding und damit die Höhe
der Tabbar nach dem ersten Paint ändern; oder etwas in der Startsequenz. Ein temporärer
Debug-Streifen, der `visualViewport.height` und die Tabbar-Oberkante über die ersten Sekunden
mitschreibt, wäre der ehrlichste Weg — vorher mit Furkan abstimmen, das ist sichtbare UI.

### Nicht angefasst, aber auffällig

Beim Start in der Bot-Brücke laufen zwei bis drei volle Neu-Renderings innerhalb einer halben
Sekunde: `loadBridgeState()` rendert komplett und hängt selbst noch ein
`refreshAppDataFromServer` nach 250 ms an, und `pageshow` feuert beim Laden ein zweites.
`appDataRefreshInFlight` greift dabei nicht, weil die Sperre `loadBridgeState` nicht kennt.
Das erklärt springende **Inhalte**, nicht die Leiste — deshalb bewusst nicht in diesen Fix
gepackt. Drei Server-Anfragen beim Start statt einer sind trotzdem unnötig.

## Messsonde für den Leisten-Bug (26.07., temporär, noch zu deployen)

Der oben beschriebene „nächste Schritt" ist jetzt gebaut und mit Furkan abgestimmt. Statt weiter
zu raten, misst die App den Startvorgang auf seinem Gerät selbst — der Fehler tritt nur in der
installierten PWA auf und ist im Desktop-Preview nicht reproduzierbar.

**Warum überhaupt in der App und nicht per Web Inspector:** In der installierten PWA gibt es keine
Adressleiste, ein `?debug=`-Parameter ist also unmöglich. Der Schalter musste deshalb in die App
selbst, und das Ergebnis muss einen App-Neustart überleben — daher localStorage.

Aufbau:

- `index.html`, direkt nach `#splash`: ein Skript, das nur läuft, wenn `localStorage['rove-diag']`
  auf `1` steht. Es setzt das Flag sofort auf `0` zurück (ein Durchgang pro Aktivierung) und
  zeichnet ab dem ersten Frame bis zu 30 Sekunden lang auf: `innerHeight`,
  `documentElement.clientHeight`, `visualViewport.height` und `.offsetTop`, den aufgelösten Wert
  von `env(safe-area-inset-bottom)`, Oberkante und Höhe der Tabbar sowie `scrollY`.
  Gespeichert wird nur, wenn sich einer dieser Werte geändert hat — sonst ertrinkt das Protokoll
  in identischen Zeilen. Ohne Flag kostet der Block ein einziges localStorage-Lesen.
- `env(safe-area-inset-bottom)` ist aus JavaScript nicht direkt lesbar. Ein unsichtbarer 1px-
  Streifen mit `height:env(safe-area-inset-bottom,0px)` macht den aufgelösten Wert als Höhe
  messbar. Das ist genau die Hauptverdächtige: springt der Wert nach dem ersten Paint von 0 auf
  34, wächst die Tabbar-Höhe und ihr Inhalt rutscht nach oben.
- `removeSplash()` setzt eine Marke ins Protokoll, damit sichtbar wird, ob der Sprung mit dem
  Verschwinden des Splash zusammenfällt.
- Bedienung in den Einstellungen unter „Diagnose", **versteckt hinter fünfmal Tippen auf die
  Zeile „Version"** — die anderen Beta-Nutzer sollen keinen Eintrag sehen, mit dem sie nichts
  anfangen können. Danach: starten, App schliessen, übers Icon neu starten, Ergebnis ansehen und
  über „Kopieren" aus der App holen.

Im Desktop-Preview gegengetestet: der komplette Weg funktioniert (freischalten, scharf stellen,
Neustart, Protokoll ansehen, kopieren, zurück). Das Protokoll enthielt dort erwartungsgemäss nur
zwei Zeilen — der Startwert und die Splash-Marke —, weil sich auf dem Desktop nichts bewegt.
Genau diese Ruhe ist die Vergleichsbasis für das, was auf dem iPhone herauskommt.

Beide `<script>`-Blöcke mit `node --check` geprüft.

**Das ist Wegwerf-Code.** Alle Stellen sind mit `⚠️ TEMPORÄR (26.07.)` markiert:
das Sondenskript nach `#splash`, die Marke in `removeSplash()`, `settingsDiagRows()`,
`showDiagLog()` und die `diag-*`-Zweige im `setbody`-Klick-Handler. Raus damit, sobald die
Ursache gefunden ist.

## Leisten-Bug: gemessen, Ursachen gefunden (26.07.)

Furkan hat die Messung auf seinem iPhone (iOS 18.7, installierte PWA, `standalone=true`,
`display-mode=true`, dpr 3) gefahren. 1354 Frames, 135 aufgezeichnete Zustandswechsel. Damit ist
Schluss mit Raten: es waren **zwei voneinander unabhängige Ursachen**.

### Ursache 1 — der Sprung beim Start

```
t=28    ih=844  ch=844  safeBot=0   barTop=773  barH=71
t=125   — splash entfernt —
t=147   ih=844  ch=797  safeBot=34  barTop=739  barH=105
```

`env(safe-area-inset-bottom)` ist in den ersten ~120 ms noch 0 und wird erst danach zu 34.
Die Tab-Leiste bezieht ihr unteres Padding daraus, wächst deshalb von 71 auf 105 px, und ihre
Oberkante springt um exakt 34 px nach oben. Der Splash ist zu diesem Zeitpunkt schon weg
(125 ms), der Nutzer sieht den Sprung also.

**Fix:** Der Safe-Area-Wert eines Geräts ändert sich zwischen zwei Starts nicht. Er wird jetzt in
`localStorage['rove-safe']` abgelegt und im `<head>` — vor dem ersten Frame — als
`--safe-top`/`--safe-bot` auf `documentElement` gesetzt. Ab dem zweiten Start gibt es nichts mehr
zu springen. Ein Gegenstück im body misst die echten `env()`-Werte über zwei unsichtbare
1px-Streifen nach und hält den Zwischenspeicher aktuell, auch bei `resize`/`orientationchange`
(im Querformat ist der untere Wert 21 statt 34). **0/0 wird bewusst nie gemerkt und nie
angewendet** — eine gemerkte 0 würde den Sprung beim nächsten Start selbst herbeiführen; auf dem
Desktop ist 0/0 dagegen der richtige Wert und dort gibt es ohnehin nichts zu springen.

Im Preview gegengetestet: mit gesetztem Zwischenspeicher (59/34) greift der Wert schon im ersten
Frame (`padding-bottom` 42 px, Leiste 104 statt 70 px hoch), und die Desktop-Messung von 0/0
überschreibt ihn korrekt **nicht**. Ohne Zwischenspeicher verhält sich alles wie bisher.

### Ursache 2 — das Zappeln nach dem Eintragen

Zwischen t=147 und t=14435 stand die Leiste **vollkommen still** — kein einziger Zustandswechsel
über 14 Sekunden. Das entlastet jeden Timer und jede Render-Schleife in der App: es liegt nicht an
uns, dass es später wackelt.

Der Auslöser ist die Tastatur. Beim Eintragen fällt `innerHeight` auf 441 (`visualViewport.offsetTop`
309). Nach dem Schliessen sollte iOS auf 844 zurückgehen. Beim ersten Mal tut es das auch, aber
zappelig über drei Sekunden (838/841/838/841…). **Beim zweiten Mal bleibt es hängen:** sechs
Sekunden lang wechselt `innerHeight` im Takt von ~70 ms zwischen 810 und 811, danach zwischen 818
und 819 — bis zum Ende der Aufzeichnung, ohne je wieder 844 zu erreichen. Die Leiste hängt an
`bottom:0` und macht jede dieser Bewegungen mit: `barTop` ist in allen 135 Zeilen exakt
`innerHeight − Leistenhöhe`.

Bemerkenswert: `documentElement.clientHeight` blieb die ganzen 30 Sekunden konstant bei 797. Der
Dokumentkasten ist also stabil, nur die Fensterunterkante driftet.

**Erster Versuch (Hypothese, nicht bewiesen):** `closeSheet()` gibt den Fokus jetzt hart ab und
setzt den Fensterscroll zurück (`dismissKeyboard()`). Das bisherige `quickIn.blur()` reichte
nicht, weil beim Speichern längst ein anderes Element den Fokus haben kann. Ob iOS den Viewport
daraufhin sauber wiederherstellt, muss die nächste Messung zeigen.

**Falls das nicht reicht**, sind das die nächsten Hebel, in dieser Reihenfolge:
`interactive-widget` im Viewport-Meta; die Leiste an den stabilen Dokumentkasten hängen statt an
die Fensterunterkante (Vorsicht: `#app` endet bei 797, das Fenster bei 844 — ein naives
`position:absolute` würde die Leiste 47 px zu hoch setzen, die Differenz ist noch ungeklärt).

Die Messsonde bleibt bis dahin drin.

## Push-Benachrichtigungen: Fundament gebaut (27.07.)

Auslöser: Der Schalter „Benachrichtigungen" in den Einstellungen war **reine Deko** — `PREFS.notif`
wurde nirgends gelesen, es gab weder Service Worker noch Push. Ebenso „Haptisches Feedback":
`navigator.vibrate` gibt es auf iOS nicht, auf dem iPhone passierte also garantiert nichts.
**Beide toten Schalter sind raus** (Haptik erscheint nur noch auf Geräten, die wirklich vibrieren).

### Service Worker — `rove-app/sw.js` (NEUE Datei, muss mit deployt werden)

⚠️ **Er hat bewusst KEINEN `fetch`-Handler.** Ein Service Worker, der Anfragen aus einem Cache
beantwortet, ist der häufigste Grund dafür, dass eine installierte PWA für immer eine alte Version
zeigt — und würde damit das Auto-Update aushebeln, das der Grund ist, warum niemand Rov.E löschen
muss (Löschen killt den Login). Ohne fetch-Handler kann er die Auslieferung nicht beeinflussen.
Wer dort Caching einbaut, muss vorher wissen, wie danach aktualisiert wird.

Er kann genau zwei Dinge: Push-Nachrichten anzeigen und beim Antippen ein bestehendes App-Fenster
fokussieren statt ein zweites zu öffnen.

### Server

- `app_push_subscriptions` (mehrere Geräte pro Nutzer erlaubt, `endpoint` ist UNIQUE).
- `POST /v1/push/subscribe` · `POST /v1/push/unsubscribe` · `GET /v1/push/key`.
- `send_push_to_user(conn, user_id, title, body, tag, url)` — abgelaufene Abos (404/410) werden
  gelöscht, alle anderen Fehler nur geloggt. **Eine fehlgeschlagene Benachrichtigung darf niemals
  eine Buchung oder einen Report scheitern lassen.**
- Braucht `pywebpush` und die VAPID-Schlüssel als Umgebungsvariablen.

⚠️ **Fehlt die Bibliothek oder ein Schlüssel, meldet `/v1/push/key` `available:false`, die App
blendet den Schalter gar nicht ein und der Versand ist ein stiller No-Op.** Ein Deploy ohne
Einrichtung kann also nichts kaputtmachen — und es entsteht kein neuer toter Schalter.

### Noch zu tun, bevor wirklich etwas ankommt

1. `pywebpush` in die Server-venv installieren.
2. VAPID-Schlüsselpaar erzeugen, in die `.env` (`ROVE_VAPID_PUBLIC`, `ROVE_VAPID_PRIVATE`,
   `ROVE_VAPID_SUBJECT`). **Nicht ins Repo.**
3. Danach: Auslöser einbauen — Gehalt am Zahltag, Fixkosten am Vortag, Report fertig. Furkans
   Vorgabe: **dezent**, nur wenn es zählt, kein Spam.

⚠️ **iOS-Besonderheit:** Push funktioniert dort NUR in der installierten PWA (ab iOS 16.4) und der
Erlaubnis-Dialog erscheint nur nach einem echten Tippen. Deshalb hängt `togglePush()` am Knopf in
den Einstellungen und läuft nicht automatisch beim Start.

## Identitäts-Leck bei den ersten Beta-Testern — gefixt (27.07., bestätigt)

Furkan hat drei Tester eingeladen. **Alle drei sahen in den Einstellungen seinen Namen und seine
E-Mail-Adresse.** Zwei Ursachen, beide behoben:

1. In `index.html` standen Name und Adresse **fest im Markup** (`<div class="pn">…`).
2. `applyProfileIdentity()` würde das überschreiben, wurde aber **nur in `loadProfileState()`**
   aufgerufen — im App-Modus lief es nie.

**Fix:** Server liefert `identity` (`_identity()` in `rove_app_state.py`, Quelle `app_accounts`),
die Platzhalter im HTML sind neutral (`Dein Profil` / `—`), und `applyProfileIdentity()` steht jetzt
in allen drei Lade-Sequenzen. Fallback für den Namen ist der Teil vor dem @ der **eigenen**
Login-Adresse — nie ein fremder Name, lieber gar keiner. Auch aus dem Code-Kommentar wurde die echte
Adresse entfernt, sonst wäre sie weiter im ausgelieferten HTML gestanden.

**Neu dazu:** `POST /v1/profile` setzt einen Anzeigenamen (`app_accounts.display_name`, per
Migration ergänzt). In der App unter Einstellungen → Profil. Die E-Mail bleibt bewusst
unveränderlich — sie ist der Login-Schlüssel, ein Wechsel ohne neue Bestätigung wäre eine
Übernahmelücke.

Getestet, 9 Prüfungen grün. Von Furkan am Gerät bestätigt.

✅ **Kein Datenleck — an drei echten Konten geprüft.** Furkan hat kontrolliert, ob die Tester auch
seine Zahlen sehen: „Zahlen, Verträge, Sparrate usw. von den Kunden ist alles richtig übernommen
worden." Jeder Tester hängt an seinem eigenen Bot-Konto, nur Name und Adresse kamen aus dem
Markup. **Damit ist das Mehrbenutzer-Modell erstmals im echten Betrieb belegt** und nicht mehr nur
Theorie — wichtig für den Rollout an die vier.

⚠️ **Damit ist dieselbe Bug-Klasse an EINEM Tag viermal aufgetreten**: Glocke (25.07.), Zeitreihe,
Prozent-Pfeil und Identität (alle 27.07.). Immer dasselbe Muster — eine Funktion fehlt in einer der
drei Lade-Sequenzen `loadProfileState()` / `loadBridgeState()` / `refreshAppDataFromServer()`.
**Wer eine Funktion hinzufügt, die etwas anzeigt, prüft alle drei Listen nebeneinander.** Wo sich
dieselbe Logik mehrfach wiederholt, gehört sie in eine Funktion (Vorbild: `updateChangeBadge()`).

**Nebenbefund, noch offen:** Alte `/app`-Links enthalten kein `identity` — der eingefrorene Snapshot
stammt aus der Zeit davor. Da die Startsequenz seit dem 26.07. die Live-Abfrage abwartet, ist das
beim Öffnen unsichtbar. Neue Links enthalten es.

## ⚠️ GRUNDSATZFUND: Der Bot hat nie ein Gehalt gebucht (27.07.)

Beim Nachrechnen von Furkans Kontostand aufgefallen und in `bot.py` verifiziert:

- **Es gibt in `bot.py` keinerlei Zahltag-Logik.** Kein Payday, kein Monatswechsel. Das Wort
  „Kontostand" kommt in der ganzen Datei nicht vor.
- `current_cash` wird an genau drei Stellen geschrieben: Onboarding (Schritt 7), Sparraten-
  Bestätigung über `/investiert`, und die App-Synchronisation.
- **Ausgaben aus dem Bot senken den Kontostand ebenfalls nicht.** Nur App-Ausgaben tun das.

Für reine Telegram-Nutzer war der Kontostand damit eine **eingefrorene Onboarding-Zahl**. Budget,
Score und Report waren immer korrekt — die sind reine Rechnungen aus Ausgaben und Planwerten. Nur
das Guthaben wurde nie fortgeschrieben. Aufgefallen ist es erst, als die App die Zahl prominent
nach vorne stellte.

Die Onboarding-Angabe `income` ist ein **Planungswert** (Budget, Score, Report), kein Geldeingang.
Das ist so gebaut, war aber nirgends erklärt.

### Der Fix: Monatscheck bucht jetzt wirklich

Es brauchte **keinen neuen Ablauf** — der Monatscheck existierte längst, öffnet sich automatisch
am 1. eines Monats (`maybeOpenMonthlyPlanOnMonthStart`, nur im Bridge-Modus) und kennt
`confirm_income` / `confirm_fixed_costs` / `confirm_savings`. Nur: **`confirm_savings` buchte seit
jeher echtes Geld, die anderen beiden nur einen Status.** Genau diese Asymmetrie war die Ursache.

- `confirm_income` → bucht `income + other_income` als `income`-Bewegung, hebt das Girokonto.
- `confirm_fixed_costs` → bucht `fixed_costs` als **neue Bewegungsart `fixed`**, senkt das Giro.

⚠️ **`fixed` gehört bewusst NICHT in `expenses`.** Das Budget rechnet
`verfügbar = Einnahmen − Fixkosten − Sparraten − Ausgaben`; stünden die Fixkosten zusätzlich in
`expenses`, wären sie doppelt drin und würden Budget, Bot und Report verfälschen.

**Doppelbuchungssperre über die Bewegung, nicht über den Status** — ein Status kann von der Wahrheit
abweichen (Nutzer löscht die Buchung), die Bewegung nicht. Reopen + erneut bestätigen bucht deshalb
nicht doppelt; nach dem Löschen der Buchung bucht es korrekt neu.

Mitgezogen, damit nichts inkonsistent bleibt:
- `_build_tx`: `fixed` erscheint als sichtbare Buchungszeile (`csid`, negativer Betrag, FIXED_TINT).
- `_daily_net_deltas`: `fixed` senkt die Vermögenskurve — sonst hätte sie an dem Tag einen Sprung
  gemacht, den es nie gab.
- `DELETE /v1/cash-movements/<id>`: kennt `fixed` und gibt das Geld aufs Giro zurück.
- **Text im Monatscheck korrigiert.** Dort stand „Es wird dadurch kein Geld künstlich gebucht" —
  ab jetzt falsch. Neu: „Rov.E bucht es dann auf dein Girokonto — einmal pro Monat und nur auf dein
  Tippen hin, nie von selbst." Der Toast nennt jetzt den Betrag statt „Monatsplan bestätigt".

**Mit echten Endpunkt-Aufrufen getestet** (Flask-Testclient, Kopie der DB, Erwartungswerte vorher
festgelegt), 13 Prüfungen, alle grün: Gehalt bucht 532,20 → 4.962,20 · erneutes Bestätigen bucht
nicht doppelt · Fixkosten 4.962,20 → 2.880,88 · Löschen gibt zurück · danach erneut buchbar ·
Tagesgeld und Bargeld unberührt · Tagesdelta der Kurve exakt 2.348,68.

**Bewusst NICHT gebaut:** Bot-Ausgaben ans Konto hängen. Telegram wird abgeschaltet, das wäre der
riskanteste Teil gewesen und erledigt sich von selbst ([[project-bot-wird-abgeschaltet]]).

### Zwei Folgebugs am selben Abend, von Furkan im echten Betrieb gefunden

**1. Die Fixkosten-Buchung liess sich nicht löschen und kam nach 45 s zurück.** Ursache war eine
einzige Bedingung in `index.html`:

```js
if(item.a>0 && item.csid!=null) syncCashMovementDelete(item.csid);   // vorher
```

`item.a>0` heisst **nur Einnahmen**. Die Fixkosten-Buchung hat einen negativen Betrag, fiel durch
die Bedingung und wurde nur lokal entfernt — der Server erfuhr nie davon, der Refresh holte sie
zurück und senkte das Guthaben erneut. Beim Gehalt (positiv) funktionierte es deshalb.
**Regel: das Vorzeichen sagt nicht, wo eine Zeile lebt — `csid` tut das.** Jede Zeile mit `csid`
steht in `app_cash_movements` und muss auch dort gelöscht werden.

**2. „Doch noch nicht da" nahm die Buchung nicht zurück**, sondern stellte nur den Status um. Das
Geld blieb liegen, Furkan musste die Zeile von Hand im Cashflow löschen. `reopen_income` und
`reopen_fixed_costs` löschen jetzt die Buchung des laufenden Monats und stellen den Kontostand
wieder her. Ein Knopf, der „doch nicht" sagt, muss zurückbuchen — sonst ist er eine Lüge.

Beide mit echten Endpunkt-Aufrufen nachgetestet, zusammen jetzt 20 Prüfungen, alle grün.

⚠️ **Fixkosten prüfen bewusst NICHT auf Deckung.** Bei Furkan (Giro 532,20, Fixkosten 2.081,32)
geht das Konto ins Minus. Das ist richtig — die Abbuchung hat real stattgefunden, der Nutzer
bestätigt sie nur. Praktische Folge: erst Gehalt bestätigen, dann Fixkosten.

### Zahltag — gebaut und getestet (27.07.)

Der Monatscheck öffnete sich stur am **1.**, unabhängig davon, wann das Gehalt kommt (Furkan bekommt
am 15.). Ein Zahltag-Feld gab es nirgends — weder im Bot noch im API. Furkan hat sich für den
direkten Weg entschieden: **die App fragt einmal, der Wert liegt serverseitig.**

- `users.payday` (INTEGER 1–31, nullable) per `ensure_payday_column()` ergänzt.
- `POST /v1/profile` nimmt jetzt `name` **und/oder** `payday`. ⚠️ Es wird nur geschrieben, was im
  Payload steht — sonst würde eine Namensänderung den Zahltag löschen (ist als Testfall abgedeckt).
- `_payday_block()` liefert `{day, faellig, gebucht}`. `gebucht` kommt aus den Bewegungen, nicht aus
  einem Merker — ein Merker kann von der Wahrheit abweichen, sobald jemand die Buchung löscht.
- App: Einstellungen → Profil → **Zahltag**. `maybeOpenMonthlyPlanOnMonthStart()` heisst zwar noch
  so, entscheidet aber jetzt über den Zahltag statt über den 1.

**Ohne gesetzten Zahltag meldet sich Rov.E gar nicht von selbst.** Lieber still als zur falschen
Zeit eine Gehaltsbuchung vorschlagen — geraten wird nichts.

11 Prüfungen grün, u. a.: ungültiger Tag → 400, Namensänderung lässt den Zahltag stehen, Zahltag 31
gilt in kürzeren Monaten am Monatsletzten, gebuchtes Gehalt schaltet die Nachfrage ab.

**Noch offen:** `maybeAutoBookIncome()` / `maybeAutoBookFixedCost()` in der App laufen weiterhin nur
im lokalen Profil-Modus und schreiben nur in den Browser-Speicher. Für Bridge-Nutzer irrelevant,
aber wer den Profil-Modus ernst nimmt, muss sie auf dieselben Endpunkte umstellen.

## Chart zeigte nie echte Zahlen — gefixt (27.07.)

Furkan: „1W/1M/6M/1J/Max bewegen sich nicht, ich hab die Woche Geld ausgegeben, da muss doch was
stehen." Drei Ursachen, alle drei behoben.

**1. Der Server schickte einen Platzhalter.** `rove_app_state.py` hatte
`"series": {r: [net_worth_k, net_worth_k] for r in (...)}` — für jeden Zeitraum derselbe Wert
zweimal. Also eine Waagerechte, und die Zeile darunter (`letzter − erster`) exakt 0. Es war kein
Anzeigefehler und nichts wurde gelöscht: **diese Zahlen gab es nie.**

**2. Die App-Historie läuft nur im Profil-Modus.** `syncNetHistory()` steigt bei
`APP_MODE !== "profile"` sofort aus, `saveProfile()` ebenso, und `loadBridgeState()` ruft die
Historien-Funktionen gar nicht auf. Furkan ist über Telegram angemeldet, also im Bridge-Modus.

**3. Der Live-Refresh hat die Zeitreihe nie übernommen** und `drawChart()` nie aufgerufen — selbst
mit echten Serverdaten wäre die Kurve stehen geblieben. Gleiche Lücke wie beim Glocken-Bug: eine
Render-Funktion in einer der drei Lade-Sequenzen vergessen.

### Der Fix: Verlauf rückwärts aus echten Buchungen

Keine neue Tabelle nötig. Das heutige Vermögen ist bekannt und jede Buchung seit einem Zeitpunkt
auch, also ist der Stand von damals rechenbar: `Wert(gestern) = Wert(heute) − Veränderung(heute)`.
Damit ist der Verlauf **sofort** echt, rückwirkend so weit wie der Bot Buchungen hat — statt erst ab
morgen zu wachsen.

`_net_worth_series()` + `_daily_net_deltas()` in `rove_app_state.py`. Ausgaben senken, Einnahmen
(`app_cash_movements.kind='income'`) heben. Bewusst NICHT mitgezählt: `withdrawal` (neutral, Geld
wechselt nur das Konto) und `payment`/`card` (reine Buchhaltung zu einer Ausgabe, die schon in
`expenses` steht — würde doppelt zählen).

Der Server schickt zusätzlich `histDates` mit, damit die App die Datums-Labels nicht mehr raten
muss. Die App übernimmt beides jetzt in **allen drei** Sequenzen.

**Mit handgerechneten Werten geprüft** (10.000 € heute; heute −50, gestern −30, vor 3 Tagen +2000
Gehalt, vor 6 Tagen −20; dazu eine Abhebung und eine bar bezahlte Ausgabe als Fallen):
erwartet `[8100, 8080, 8080, 8080, 10080, 10080, 10050, 10000]` — genau so geliefert, Delta-Zeile
`+1.900,00 €`. In der App gegengetestet: alter Platzhalter ergab „+0 € in 1 Woche", die echte
Ausgabenwoche ergibt „−412 € in 1 Woche".

⚠️ **Grenze der Genauigkeit, bewusst so:** rekonstruiert werden Ausgaben und Einnahmen.
Kursbewegungen von ETF/Krypto, Änderungen am Immobilienwert und von Hand korrigierte Kontostände
lassen sich rückwärts nicht trennen — die wirken so, als hätten sie immer den heutigen Wert gehabt.
Für 1W und 1M ist die Kurve auf den Cent genau; für 1J zeigt sie die Spar- und Ausgabenbewegung,
nicht die Kursentwicklung. Wer das später sauber will, braucht echte Tages-Snapshots auf dem Server.

**Noch offen, bewusst nicht angefasst:** Der Score-Faktor „Vermögensaufbau" steht in
`loadBridgeState()` fest auf `p=0, v="—"` mit dem Kommentar „v1-Bridge hat noch keine echte
Zeitreihe". Die gibt es jetzt — der Faktor könnte berechnet werden.

## LEISTEN-BUG GELÖST — gemessen, hergeleitet, nachgerechnet (26.07.)

Furkan hat die zweite Sonde gefahren: 150 Zeilen, 23 Sekunden, installierte PWA, drei Eintragungen
hintereinander. Damit ist der Bug keine Hypothese mehr.

### Was die Messung zeigt

```
ms     innerH  vvH   vvTop  clientH  barTop  barH
1      844     844   0      797      739     105   Ruhe, korrekt
1093   441     441   309    797      383     105   Tastatur offen
1870   797     797   0      797      692     105   nach dem 1. Eintragen: 47 px zu hoch
8697…  803→844                                     kriecht über 3 s zurück
15893  810 / 807 / 811 / 807 …                     ab dem 2. Eintragen: Dauerpendeln, 60-ms-Takt
20145  822 / 823 / 822 / 823 …                     bis zum Ende der Aufzeichnung
```

Drei harte Befunde:

1. **`innerH` und `vvH` sind in JEDER Zeile identisch.** Die `visualViewport`-Hypothese ist damit
   tot — es gibt keinen zweiten, korrekten Wert, den man auslesen könnte.
2. **`clientH` ist konstant 797.** Über die ganze Aufzeichnung, durch jede Tastatur.
3. **`barTop = innerH − barH`, ausnahmslos.** `bottom:0` hängt also am *visuellen* Viewport.

Damit ist auch erklärt, warum beide früheren Umbauten scheitern mussten: Der stabile Wert (797)
liegt 47 px über der echten Bildschirmunterkante (844). Wer die Leiste daran hängt, bekommt sie
dauerhaft zu hoch — genau das ist am Gerät passiert.

### Der Fix

Nicht der eine oder der andere Anker, sondern die **Differenz**: unten bleiben und um
`volle Höhe − innerHeight` nach unten korrigieren.

- CSS: `.tabbar{ … transform:translateY(var(--bar-fix,0px)) }`
- JS (`LEISTEN-ANKER`): merkt sich das Maximum von `innerHeight` je Lage (die Tastatur macht den
  Wert nur kleiner, nie grösser → das Maximum ist die echte Fensterhöhe), legt es unter
  `localStorage['rove-vh']` ab und setzt `--bar-fix` bei jedem `resize`/`visualViewport`-Ereignis.

Gegen Furkans echte Messwerte nachgerechnet, jede Zeile einzeln:

```
innerH  barTop alt  --bar-fix  barTop neu
797     692         47         739
803     698         41         739
810     705         34         739
817     712         27         739
823     718         21         739
844     739          0         739
```

**Alle Ruhezustände exakt 739.** Das Pendeln wird rechnerisch auf null gebracht.

Im DOM gegengeprüft: `--bar-fix` steht im Ruhezustand auf `0px` (kein Versatz, wenn nichts kaputt
ist), auf `47px` verschiebt sich die Leiste um exakt 47 px, und `liftTabbar()` — das
`style.transform=""` setzt — killt den Fix **nicht**, weil er aus der Stylesheet-Regel kommt und
nicht aus einem Inline-Stil.

⚠️ Wer `bottom` anfasst oder den `transform` entfernt, holt das Springen zurück.

### Bestätigt am Gerät, Sonde wieder ausgebaut

Dritte Messrunde, fünf Eintragungen, 31 Zeilen (vorher 150):

```
ms     innerH  vvH   vvTop  clientH  barTop  barH  appH
1      844     844   0      797      739     105   844
926    441     441   356    797      383     105   844   Tastatur, Leiste ausgeblendet
1759   844     844   0      797      739     105   844   sofort zurück auf 844
17120  844     844   0      797      739     105   844
```

`barTop` konstant 739, `appH` konstant 844, über alle fünf Eintragungen. Furkan: „jetzt war es
perfekt, keine Auffälligkeiten".

**Unerwarteter Nebeneffekt, wichtig fürs Verständnis:** `innerHeight` selbst kommt jetzt nach jeder
Tastatur sofort auf 844 zurück. Vorher blieb es bei 797 hängen und pendelte minutenlang. Es war
also eine **Rückkopplung**: iOS ändert die Fenstergrösse → `100dvh` ändert die Rahmenhöhe → das
Layout fliesst neu → iOS misst nach → ändert wieder. Seit `#app` nicht mehr an `dvh` hängt, ist die
Schleife offen. Deshalb 31 statt 150 Zeilen. Das erklärt rückblickend auch, warum alle früheren
Versuche, die *am Rahmen* ansetzten, das Zappeln eher verstärkt haben.

**Der Fix besteht aus zwei Teilen, die nur zusammen funktionieren:**
1. Leiste per festem `top` statt `bottom:0` → entkoppelt von `innerHeight`.
2. `#app` per `--app-h` statt `100dvh` → entkoppelt den Rahmen, öffnet die Rückkopplung.
Teil 2 war ohne Teil 1 unmöglich: solange die Leiste am Rahmen hing, hat eine feste Rahmenhöhe sie
mitverschoben (der gescheiterte `svh`-Versuch, 47 px zu hoch).

**Sonde ist komplett ausgebaut** — alle fünf `TEMPORÄR`-Stellen entfernt (Sonde, Diagnose-Sektion,
`verTaps`, `roveDiag.start()` in `openSheet()`, das `· D3` in der Version-Zeile). `grep` findet
keine Reste. Der Aufräumer in Zeile ~1058 löscht `rove-diag` beim Start ohnehin von den Geräten.

### Frühere Fassung, nur zur Historie

Die erste Fassung korrigierte per `transform:translateY(var(--bar-fix))` bei jedem Viewport-
Ereignis. Rechnerisch richtig, aber sie lief dem Sprung einen Tick hinterher — im Protokoll als
„gleicher innerHeight, zwei barTop-Werte" (807→743 direkt gefolgt von 807→739), am Gerät als ±4 px
Nachwippen. Ausserdem zog sie die Leiste bei offener Tastatur 400 px aus dem Bild. Beides ist mit
dem festen `top` strukturell erledigt: es gibt nichts mehr, was nachlaufen könnte.

### Nicht mehr offen (Historie)

Die Sonde blieb für **eine** Bestätigungsrunde drin: `barTop` muss jetzt über die ganze
Aufzeichnung konstant 739 sein. Danach fliegen alle fünf `⚠️ TEMPORÄR (26.07.)`-Stellen raus
(Sonde, Diagnose-Sektion, `verTaps`, `roveDiag.start()` in `openSheet()`, das `· D3` in der
Version-Zeile).

## Leisten-Bug: zwei Versuche, beide zurückgenommen (26.07.)

**Ergebnis vorweg: der Code steht wieder exakt auf dem Stand, der vorher live war.** Beide
Umbauten unten sind rückgängig gemacht. Wer hier weiterarbeitet, soll sie nicht wiederholen.

Ausgangslage war Furkans Urteil nach dem `dismissKeyboard()`-Deploy: „es hat nur minimal ab und an
etwas gezuckt". Das war der bis dahin beste Zustand.

**Versuch 1 — Leiste in den Fluss nehmen.** `.tabbar` von `position:fixed; bottom:0` auf ein
Flex-Kind von `#app` umgestellt, um sie von `window.innerHeight` zu lösen. Am Gerät: Start perfekt,
aber nach dem Eintragen rutschte die Leiste hoch und blieb dort. Der Dokumentkasten endet im
Standalone-Modus höher als das Fenster — genau die Differenz von 47 px, vor der der Abschnitt
„Ursache 2" oben schon gewarnt hatte. Die Warnung war richtig, der Versuch trotzdem gefahren.

**Versuch 2 — `dvh` durch `svh` ersetzen.** Annahme: `svh` ist statisch und im Standalone genauso
gross wie `lvh`/`vh`. Am Gerät falsch — die Leiste sass danach dauerhaft zu weit oben.

**Zurückgenommen:** `.tabbar` wieder `position:fixed`, `.screen` wieder 120 px Polster unten,
Standalone-Zweig wieder `dvh`.

**Einzige verbliebene Änderung — ein echter Bug, unabhängig vom Layout:** Der „+"-Knopf trägt
ebenfalls `class="tab"`, hat aber kein `data-tab`. Der Sammel-Listener rief deshalb bei jedem
Tippen auf „+" ein `go(undefined)`: Zeile 1 nahm **allen** Screens das `.active`, Zeile 2 warf dann
an `null.classList`. Wer das Sheet abbricht statt zu speichern, stand danach vor einem leeren
Screen, bis er einen Tab antippte. Beim Speichern fiel es nie auf, weil `addEntry()` danach
`go("tx")` ruft. `go()` steigt jetzt bei unbekanntem Tab aus. Im Preview vorher/nachher belegt:
nach Abbrechen 0 aktive Screens, jetzt 1.

### Splash-Logo war weg — gefixt (26.07.)

Furkan: „das Logo ist weg, die App geht einfach stumpf auf". Ursache war die `Promise.race` in der
Startsequenz: Sie nimmt den Splash raus, **sobald die Daten da sind**. Bei 83 ms Antwortzeit steckt
das Logo dann noch mitten im Einblenden (`splashIn` läuft 0,7 s) und ist praktisch unsichtbar. Das
CSS war immer auf 1,75 s ausgelegt (einblenden, stehen, ab 1,2 s ausblenden) — nur wurde nie darauf
gewartet. Die race stand schon länger drin; am 26.07. wurde sie angefasst, ohne dass auffiel, dass
sie das Logo frisst.

**Fix:** `Promise.all([datenDa, animationDurch])` statt nur `datenDa`, mit
`animationDurch = setTimeout(1750)`. Untergrenze, keine Obergrenze — antwortet der Server langsamer,
bleibt der Splash länger stehen, die 4-Sekunden-Notbremse ist unverändert. Isoliert nachgemessen mit
83 ms als Datenzeit: alt 84 ms, neu 1751 ms.

⚠️ Wer die 1750 senkt oder das `Promise.all` wieder zur `race` macht, killt das Logo erneut.

### Nächster Anlauf auf den Leisten-Bug: `interactive-widget` (26.07., zu deployen)

Furkan will die Ursache weg, nicht gedämpft. Reihenfolge deshalb bewusst vom billigsten Hebel aus:
`interactive-widget=resizes-content` im Viewport-Meta. Das sagt dem Browser ausdrücklich, wie die
Tastatur den Viewport behandeln soll, statt iOS den sichtbaren Ausschnitt nach eigenem Gutdünken
verschieben zu lassen. Eine Zeile, unbekannte Viewport-Schlüssel werden ignoriert, in Sekunden
rücknehmbar. Ob iOS 18.7 den Schlüssel kennt, zeigt nur das Gerät.

**Zu prüfen, beides:** (1) dreimal hintereinander eine Ausgabe eintragen — Leiste muss stehen
bleiben. (2) Beim Tippen muss das Eingabefeld weiterhin über der Tastatur sichtbar sein. Punkt 2 ist
die eigentliche Gefahr dieser Zeile: wenn iOS den Schlüssel kennt und anders auslegt als erwartet,
könnte das Sheet hinter der Tastatur landen.

**Falls es nichts ändert:** dann wird gemessen statt geraten. Offene Frage ist genau eine — meldet
`visualViewport.height` nach dem Schliessen der Tastatur den richtigen Wert, während `innerHeight`
bei 810 hängen bleibt? Wenn ja, ist der Fix, die Leiste über `visualViewport` zu positionieren.
Wenn nein, ist das in einer PWA nicht sauber lösbar. Die alte Sonde ist ausgebaut; eine neue muss
im Speicher sammeln und darf nur auf `pagehide` bzw. auf Knopfdruck schreiben.

**Rückfalltür:** `index.STAND-2026-07-26-vor-dienstag.html` im selben Ordner ist eine byte-genaue
Kopie des Stands vor diesen Versuchen (gleiche Prüfsumme). Ein `scp` und alles ist zurück.

**Lehre für den Leisten-Bug:** Der Preview kann iOS-Viewport-Verhalten nicht nachstellen. Jeder
Layout-Umbau am Rahmen ist deshalb ein Blindflug mit Deploy als einzigem Test — und kostet Furkan
jedes Mal einen kaputten Zustand auf dem echten Gerät. Nächster Schritt darf nur etwas sein, das
**messbar** ist (Sonde, aber gedrosselt — siehe die Lehre weiter unten) oder **klein und
rücknehmbar** (`interactive-widget` im Viewport-Meta). Nicht wieder der Rahmen.

### Zweite Messung nach dem Deploy — Ursache 1 ist erledigt

```
t=26   safeBot=0   barTop=739  barH=105     <- schon im ERSTEN Frame richtig
t=88   — splash entfernt —
t=139  safeBot=34  barTop=739  barH=105     <- Leiste bewegt sich nicht
```

Der Zwischenspeicher greift: die Leiste steht vom ersten Frame an dort, wo sie hingehört. Beim
Auflösen von `env()` ändert sich nur noch `safeBot` und `ch`, die Leiste bleibt stehen. Vorher
sprang sie hier von 773 auf 739.

### Ursache 2 — deutlich besser, aber noch nicht fertig

`dismissKeyboard()` hat das pathologische Zappeln beseitigt: das sekundenlange Flackern 810↔811
im 70-ms-Takt kommt in der zweiten Messung **kein einziges Mal** mehr vor. Nach jedem Schliessen
beruhigt sich `innerHeight` innerhalb von ~250 ms und bleibt dann still.

Was bleibt: es beruhigt sich auf dem **falschen** Wert. Nach drei Eingaben landet `innerHeight`
bei 807, 808 und 809 statt bei 844 — die Leiste sitzt also rund 36 px zu hoch und darunter klafft
Hintergrund. Erst nach mehreren Sekunden kriecht der Wert langsam zurück (815 → 819 → 821).

Nächster Hebel dafür, aber **erst nach der Zahlen-Frage unten**, damit nicht zwei Änderungen
gleichzeitig gemessen werden: `interactive-widget` im Viewport-Meta.

### Neu aufgetaucht: falsche Zahlen blitzen beim Start auf

Von Furkan bestätigt: beim Start sind kurz falsche Zahlen zu sehen, die sich sofort korrigieren.
Der Splash verschwindet nach 88 ms — viel zu früh, um irgendetwas zu verdecken.

Die Sonde schreibt deshalb jetzt zusätzlich den angezeigten Nettovermögens-Text mit (Spalte
`netto`) und setzt Marken bei `bruecke-geladen`, `bruecke-FEHLGESCHLAGEN` und
`nachgeladen (refreshAppDataFromServer)`. Damit steht auf einer Zeitachse, welcher Wert wann auf
dem Schirm stand und wer ihn geschrieben hat.

Im Preview (Mock-Modus) sichtbar gemacht: der countUp-Ticker zählt zwischen 366 und 847 ms von
−4.337,97 € auf die Mock-Zahl 42.850,00 € hoch. Auf dem Gerät ist der Splash zu diesem Zeitpunkt
längst weg. Ob der Ticker der Schuldige ist oder eine der Render-Sequenzen, entscheidet die
Messung — der Lock in `loadBridgeState()` sollte den Ticker eigentlich sofort töten.

### Dritte Messung: Sonde hat die App lahmgelegt — wieder ausgebaut

Furkan: „jetzt ist die App eskaliert, konnte fast nichts mehr machen." Der Fehler lag an der
Sonde, nicht an der App. Sie schrieb ihr **komplettes** Protokoll bei **jeder** Änderung neu in
den lokalen Speicher. Beim Zappeln der Fensterhöhe hiess das: alle ~75 ms das gesamte, stetig
wachsende JSON serialisieren und synchron wegschreiben. Bei 288 Zeilen ist das ein spürbarer
Block auf dem Hauptthread — und weil iOS genau in dieser Zeit den Viewport zurückstellen wollte,
hat die Sonde das Zappeln, das sie messen sollte, selbst am Leben gehalten.

**Lehre für eine nächste Sonde:** höchstens alle paar Sekunden und auf `pagehide` speichern,
niemals pro Änderung. Und nie einen wachsenden Puffer komplett neu serialisieren.

Die Sonde ist komplett ausgebaut (Skript, Marken, Einstellungen-Bedienung). Beim nächsten Start
räumt die App das zurückgebliebene Protokoll aus dem lokalen Speicher. Was der Aufbau war, steht
oben — nachbauen dauert Minuten, falls wieder gemessen werden muss.

Aus dem Lauf trotzdem verwertbar: **Ursache 1 bestätigt sich erneut** (t=32 schon `barTop=739`).
Ursache 2 lässt sich aus diesem Lauf nicht beurteilen, die Sonde hat ihn verfälscht.

### Falsche Zahlen beim Start: Ursache gefunden und behoben

```
t=83   — bruecke-geladen —
t=106  netto = 40.126,00 €
t=383  — nachgeladen (refreshAppDataFromServer) —
t=393  netto = 39.658,20 €
```

Beide Zahlen sind **echte** Daten, aber aus zwei verschiedenen Quellen:

- `BRIDGE_STATE_URL` zeigt auf eine statische JSON-Datei, die der Server **einmal** beim Erzeugen
  des `/app`-Links geschrieben hat (`build_app_state()` in `rove_app_state.py` schreibt
  `public/app-state/<token>.json`). Die App lädt bei jedem Start dieselbe Datei — der Inhalt ist
  so alt wie der Zugangslink, bis zu 30 Tage.
- `/v1/state` (`build_live_app_data()`) liefert den Live-Stand.

Der Startwert war also kein Mock und kein Rechenfehler, sondern ein eingefrorener Stand. Die
467,80 € Differenz ist schlicht alles, was seit dem Erzeugen des Links passiert ist. Der Ticker
und die Render-Sequenzen waren unschuldig.

**Fix in der Startsequenz:** Der Splash geht jetzt erst, wenn die Live-Abfrage durch ist
(`loadBridgeState().then(ok => ok ? refreshAppDataFromServer() : null)`). Der veraltete
Zwischenstand ist damit nie sichtbar. Die 4-Sekunden-Notbremse bleibt, die App hängt also nicht,
wenn der Server schweigt. Ist die Brücke selbst fehlgeschlagen, wird die Live-Abfrage übersprungen
und der bisherige „Zugang abgelaufen"-Weg läuft unverändert.

Nebenbei zwei überflüssige Server-Anfragen beim Start entfernt: das `setTimeout(…, 250)` in
`loadBridgeState()` entfällt, und `pageshow` lädt nur noch bei `e.persisted` nach (also beim
Zurückholen aus dem Hintergrund, nicht beim ersten Laden). Damit ist der im Abschnitt
„Nicht angefasst, aber auffällig" beschriebene Dreifach-Start erledigt.

Geprüft: alle Skriptblöcke `node --check`; die Startsequenz isoliert in Node gegen drei Fälle
durchgespielt (Brücke ok + Live ok → Splash erst nach dem Live-Wert; Brücke fehlgeschlagen →
Live-Abfrage wird übersprungen; Server hängt → Notbremse greift, App wird trotzdem sichtbar);
App im Preview ohne Konsolenfehler, Leiste und Tabs unverändert.

### Offen

Die Leiste nach dem Eintragen. Stand aus Messung 2: kein Flackern mehr, aber `innerHeight`
beruhigt sich auf ~807 statt 844, die Leiste sitzt also rund 36 px zu hoch. Nächster Hebel wäre
`interactive-widget` im Viewport-Meta — bewusst noch nicht angefasst, damit erst die Startsequenz
im echten Betrieb beobachtet werden kann.

## Telegram-Abschaltung: kontrollierte Migration

Die App ist die neue Hauptoberfläche. Der Telegram-Bot bleibt jedoch mindestens bis nach zwei
vollständig geprüften Monatszyklen aktiv, weil er den bestehenden Reportversand noch trägt.
Frühester sinnvoller Termin für die vollständige technische Abschaltung ist Mitte September 2026.

Vorher wird Telegram für etwa eine Woche auf einen ruhigen Weiterleitungsmodus reduziert: keine
neuen App-Funktionen, klare Nachricht zur App, Report- und Datenpfad weiter überwacht. Erst wenn
der August- und September-Report inklusive App-Archiv sauber gelaufen sind und alle aktiven Nutzer
einen dauerhaften App-Login haben, kann `clarity-bot` ohne Daten- oder Report-Risiko enden.

## Bestätigter App-Stand (27.07.)

Furkan hat die zwei heute kritischsten End-to-End-Ergebnisse im echten Betrieb bestätigt:

- **Die untere Navigation sitzt stabil.** Nach mehreren Eingaben bleibt sie bündig; kein
  Springen und kein Flackern mehr. Der Mess-/Diagnosecode ist wieder entfernt.
- **Der Monatscheck bewegt jetzt echtes Geld.** Beim Bestätigen wird Einkommen als Einnahme auf
  dem Girokonto gebucht; Fixkosten und App-Ausgaben senken es. Nach Schließen/Neustart bleiben
  Kontostand und Buchungszeilen erhalten.

Damit ist die App nicht mehr nur eine Budgetanzeige: der sichtbare Girostand folgt den in der App
bestätigten Einnahmen und Ausgaben dauerhaft. Telegram bleibt bis zur geplanten Migration parallel
aktiv, aber neue Finanzlogik darf nicht mehr stillschweigend nur lokal im Browser leben.

### Versionssicherung

- Backend-Stand: GitHub-Branch `feature_clarityr-report`, aktuell Commit `1e5954d`
  (`Reverse monthly check bookings on reopen`).
- Der aktuelle App-Stand liegt weiter in `work/rove-app/index.html` und wird live per `scp`
  ausgeliefert. Er muss zusätzlich als Git-Snapshot abgelegt werden; bis dahin ist der aktuelle
  Frontendstand nicht verlässlich über GitHub wiederherstellbar.

## Sparrate ist eine Umschichtung, keine zweite Einnahme (27.07., noch zu deployen)

Review-Fund von Codex nach dem Monatscheck-Fix:

- `confirm_savings` erhöhte bisher ETF/Investments und Tagesgeld, zog dieselbe Summe aber nicht
  vom Giro ab. Nach einem bereits bestätigten Gehalt erschien die Sparrate dadurch ein zweites
  Mal im Nettovermögen.
- Beispiel vor dem Fix: Giro 5.000 €, Tagesgeld 1.000 €, Investments 1.000 €; ETF 300 € plus
  Cash 700 € sparen ergab fälschlich 8.000 € Gesamtvermögen statt weiterhin 7.000 €.

Fix in `rove_app_api.py`:

- ETF-Sparrate: Giro minus ETF, Investments plus ETF.
- Cash-Sparrate: Giro minus Cash, Tagesgeld plus Cash.
- `reopen_savings` dreht beide Transfers exakt um, löscht nur die eigenen
  `app_monthly_plan`-Ereignisse und bricht sicher ab, falls Tagesgeld oder Investments danach
  bereits separat vermindert wurden. So kann eine Rückbuchung kein Geld erfinden.

Geprüft mit Flask/API gegen eine Datenbankkopie:

1. 5.000 € Giro, 1.000 € Tagesgeld, 1.000 € Investments
2. 300 € ETF + 700 € Cash bestätigen → 4.000 € Giro, 1.700 € Tagesgeld, 1.300 € Investments
3. Gesamtvermögen bleibt exakt 7.000 €
4. Rückgängig → alle drei Konten und die Ereignisse exakt beim Ausgangsstand

Wichtig nach dem Deploy: Einmal prüfen, ob Furkan im Juli bereits eine Sparrate bestätigt hatte.
Alte Bestätigungen vor diesem Fix haben den Giro noch nicht gesenkt und brauchen dann eine
einmalige, bewusst kontrollierte Korrektur — niemals blind für alle Nutzer nachbuchen.

## Produktprinzipien aus dem Technik-Briefing (27.07.)

Das Briefing ist verbindliche Produktleitlinie: Rov.E baut den Kreislauf
**Erfassen → Verstehen → Handeln → Fortschritt**, nicht eine Sammlung beliebiger
Finanzfunktionen.

### Bereits erfüllt oder weit fortgeschritten

- **Zahlen als Vertrauensbasis:** App-Ausgaben, Einnahmen, Fixkosten, Bargeld, Transfers und
  Sparraten werden als getrennte Vorgänge behandelt; Kontowirkungen sind serverseitig und
  Löschungen/Rücknahmen korrigieren sie wieder.
- **Korrekturen zentral:** Kategorien korrigieren die Datenbank, Budgets, Reports und die
  zukünftige Händlerregel — nicht nur die sichtbare App-Zeile.
- **Keine stillen Demo-Werte:** Der Start wartet auf den Live-State; der alte 30-Tage-Linkstand
  wird nicht mehr kurz als aktueller Kontostand dargestellt.
- **Manueller Modus:** App-Einträge, Vermögen, Verträge, Ziele und Budgets funktionieren ohne
  Bank- oder Brokerzugang. Das bleibt ein vollwertiger, datensparsamer Produktweg.
- **LLM-Grenze:** Der Mentor rechnet heute daten-/regelbasiert; ein späteres LLM darf nur
  formulieren und erklären, nie Kontostände erfinden oder selbst Geld bewegen.
- **Frontend versioniert:** Der stabile App-Stand liegt seit Commit `7878967` zusätzlich auf
  GitHub (`rove-app/index.html`) und nicht nur im Live-scp-Pfad.

### Noch offen, aber richtige nächste Priorität

1. **Stufe 1 abschließen:** Sparraten-Transfer deployen, reale Monatslogik/Report vollständig
   prüfen, Backups und Fehlerprotokolle kurz auditieren.
2. **Datenfrische sichtbar machen:** In der App eine zurückhaltende Anzeige wie
   „Zuletzt aktualisiert: gerade eben" für servergeladene Werte. Bei späteren APIs zusätzlich
   Quelle, Zeitpunkt, Fehlerzustand und manueller Refresh.
3. **Aktivierung statt Feature-Flut:** Nach wenigen Einträgen erste echte Kategorieverteilung,
   ein klarer nächster Schritt und Fortschritt zum Report. Test mit Nicht-Finanzmenschen.
4. **Mentor danach:** DB-first-Antworten mit konkreten Zahlen und Bestätigung vor Änderungen;
   LLM erst ergänzen, wenn diese Faktenbasis stabil steht.
5. **Score und Rov.E Points glätten:** Die Gamification bleibt Teil des Produkts, wird aber erst
   nach der Monatswechsel- und Infrastrukturprüfung wieder vertieft. Dann werden Score,
   Punktequellen, Fortschritt und Sprache gegen echte App-Daten geprüft und als ruhige Motivation
   gestaltet — nie als Anreiz, Geld auszugeben oder Daten zu verfälschen.

### Bewusst später

- Bank-, Broker- und Krypto-APIs: erst mit Aktualitätsstatus, manueller Korrektur und sauberem
  Fallback.
- Kündigungsservice und Vertragsoptimierung: erst wenn sie eine echte, sichere Handlung liefern.
- Keine provisionsgetriebene Vermittlung, keine Finanznachrichten ohne persönlichen Kontext.

**Prüffrage vor jedem neuen Feature:** Verbessert es Erfassung, Verständnis, Handlung oder
Fortschritt? Falls nicht, kommt es nicht in die aktuelle App.

## Nächste Reihenfolge

1. Zentrale App-Verträge deployen und testen: neuen Vertrag anlegen, App schliessen,
   neu oeffnen, Betrag aendern und loeschen. Fixkostensumme im Bot gegenprüfen.
2. App-Stabilität testen: Daten-Sync, Budgets, Vermögen, Immobilien, Reports.
3. August-Report mit echten Daten prüfen.
4. API-Sicherheit, Backups und Fehlerprotokolle prüfen.
5. E-Mail-Login deployen und mit einem Telegram-Beta-User testen.
6. Danach App-only-Onboarding/Paywall sauber planen.
7. Erst danach Stripe vorbereiten.

## Wichtige Regeln

- Keine neuen Features auf Verdacht vor Stabilitätsprüfungen.
- Keine Zahlung aktivieren, bevor Login, Kündigung und Zugangsstatus sicher funktionieren.
- Keine Daten an Telegram-IDs als dauerhafte Kundenidentität binden.
- Keine Roadmap-Funktionen als bereits enthalten bewerben.
- Bei jeder Änderung zuerst Arbeitskopie ändern, prüfen und dann gezielt deployen.
- Die App wird ausschliesslich in `work/rove-app/index.html` bearbeitet und von dort deployt.
  Der zweite Pfad `Documents/Codex/rove-app/` wird nicht mehr verwendet (Regel vom 25.07.).
- Repo-Dateien (`bot.py`, `report_engine.py`, `rove_app_*.py`) gehen über Git nach `/root/clarity`.
  Nur `index.html` geht per scp nach `/var/www/getrove/app/`. Nicht verwechseln.
- Wird `rove_app_state.py` geändert, müssen `rove-app-api` UND `clarity-bot` neu starten.
- Grössere abgeschlossene Arbeiten werden hier eingetragen, damit Furkan, Codex und Claude
  denselben Stand sehen statt ihn zu erraten. Konkret: nach jedem grösseren Schritt, immer wenn
  Furkan "sichern" sagt, und immer wenn seit dem letzten Eintrag viel passiert ist. Nicht nach
  jeder Kleinigkeit. Auch was deployt wurde und was noch offen ist gehört hier rein, nicht nur
  was gebaut wurde.
- Die App wird nie gelöscht und neu installiert, um ein Update zu erzwingen. Auto-Update ist
  bestätigt (25.07.); Löschen killt den Login.

## Web Push: Ende-zu-Ende aktiviert und bestaetigt (29.07.)

Der bereits vorbereitete Push-Block wurde auf dem Produktivserver vollstaendig aktiviert und mit
Furkans installiertem iPhone getestet.

- `pywebpush` ist in `/root/rove-app-api-venv` installiert.
- Das VAPID-Schluesselpaar liegt ausschliesslich unter `/root/clarity/secrets/`; der private
  Schluessel ist nicht im Repository und nicht in der App.
- Die Serverumgebung enthaelt nur den oeffentlichen VAPID-Key, den Pfad zum privaten PEM-Schluessel
  und `mailto:info@getrove.de` als Absenderkennung.
- `GET /app-api/v1/push/key` liefert produktiv `available: true`.
- Furkan hat in der installierten Homescreen-PWA Benachrichtigungen aktiviert. Das Geraete-Abo
  wurde in `app_push_subscriptions` gespeichert.
- Ein echter Test-Push ueber `send_push_to_user(...)` ist auf dem iPhone angekommen.

Wichtig: Der Service Worker hat weiterhin keinen `fetch`-Handler und kann deshalb das bestaetigte
Auto-Update nicht einfrieren. Push funktioniert auf iOS nur ueber die installierte Homescreen-PWA,
nicht im Safari-Tab.

### Noch nicht bauen, bevor ein Anlass feststeht

Die Infrastruktur ist aktiv, aber es gibt bewusst noch keine automatischen Ausloeser. Der naechste
separate Produktentscheid ist, welche wenigen Ereignisse wirklich eine Nachricht verdienen (z. B.
Report bereit, Monatscheck am Zahltag oder Fixkosten-Hinweis). Keine Push-Nachricht darf Buchungen,
Reports oder Monatslogik beeinflussen; Versandfehler bleiben immer folgenlos.

### Naechster Ausloeser: Monatsreport

Der erste automatische Ausloeser wird bewusst der bereits bestehende Monatsreport sein: Erst wenn
PDF und Web-Report erfolgreich per Telegram versendet wurden, erhaelt ein Nutzer mit aktivierter
App genau eine Push-Nachricht. Der Bot uebergibt den Auftrag dafuer nur ueber localhost und ein
eigenes Server-Secret an `rove-app-api`, weil dort die Push-Bibliothek und der private
VAPID-Schluessel liegen. Die Nachricht hat pro Berichtsmonat einen festen Tag und ersetzt dadurch
bei einem erneuten Versand die vorherige statt zu stapeln. Zahltag- und Fixkosten-Hinweise werden
erst separat gebaut, weil Zeitpunkt und Inhalt aus echten Nutzerdaten kommen muessen.

### Monatscheck am Zahltag

Als zweiter und letzter regelmaessiger Push ist ein taeglicher, aber streng gefilterter
`rove_monthly_reminders.py`-Job vorgesehen. Er laeuft morgens, sendet jedoch nur am individuell
hinterlegten Zahltag und nur, wenn Einkommen, Fixkosten oder Sparrate noch nicht bestaetigt sind.
Pro Nutzer und Monat speichert `app_push_delivery_log` genau eine erfolgreiche Zustellung. Die
eine Nachricht buendelt alle offenen Punkte; es gibt keine separaten Gehalts- und Fixkosten-Pushes.

## Push-Einstellung: stabiler Startzustand (30.07.)

Die Zeile "Benachrichtigungen" in den Einstellungen wurde vorher nur sichtbar, wenn der
Service Worker *nach* der Bot/App-Verbindung fertig wurde. Auf manchen iPhone-Starts war die
Verbindung noch nicht geladen, als der erste Push-Abruf lief; die Zeile fehlte dann bis zu einem
zufaelligen spaeteren Refresh.

`index.html` fragt den Push-Status jetzt nach erfolgreicher Bridge-Verbindung und bei jedem
Oeffnen der Einstellungen neu ab. Gleichzeitige Abfragen werden zusammengelegt. Das betrifft nur
die Anzeige; bestehende Push-Erlaubnisse und Abos werden weder geloescht noch neu angefordert.

## Monatsrahmen: klare Warnstufe unter null (31.07.)

Die Mentor-Karte unterscheidet jetzt drei saubere Situationen: Budgetrahmen aufgebraucht bei noch
positivem Monatsgeld, freies Monatsgeld positiv und den echten Negativfall. Bei `frei < 0` hat der
Gesamt-Monatsrahmen Vorrang vor einem einzelnen Budgettopf und zeigt nur zwei ruhige Zeilen: den
exakten Fehlbetrag sowie die variablen Ausgaben mit dem Hinweis, welcher Betrag zur geplanten
Sparrate fehlt. Das vermeidet die fruehere, zu schwere Dreifach-Erklaerung.

Die Sparrate wird dabei bewusst nur als *eingeplant* bezeichnet. Erst die explizite Monatsplan-
Bestaetigung bucht sie; Rov.E behauptet vorher nie, der Kunde habe sie bereits nicht eingehalten.
Der Assistent beantwortet ausserdem "Schaffe ich meine Sparrate?" mit derselben Live-Rechnung.

## Einkommen und Sparrate: eine gemeinsame Quelle (31.07.)

Der Mentor beantwortet direkte Fragen wie "Wie hoch sind meine Einnahmen?" jetzt kurz aus den
echten Onboarding-Daten statt mit einer allgemeinen Profilantwort.

Die ETF- und Cash-Sparrate kann der Assistent nur noch getrennt aendern, zum Beispiel
`ETF 300 EUR und Cash 700 EUR`. In der gekoppelten App wird diese Aenderung ueber
`/v1/profile` dauerhaft in `users.etf_savings` und `users.cash_savings` gespeichert und danach
aus der gemeinsamen DB neu geladen. Dadurch nutzen Coach, Monatsplan, Score und Report wieder
denselben Wert. Eine bereits im laufenden Monat bestaetigte Sparrate bleibt gesperrt: Sie ist eine
echte Umschichtung und darf nicht nachtraeglich still umgeschrieben werden.

Bei einer bestaetigten laufenden Sparrate wird die neue Eingabe nicht rueckwirkend umgesetzt,
sondern klar als Vormerkung fuer den Folgemonat behandelt.

## Sparrate ab naechstem Monat vormerken (31.07.)

Eine bereits bestaetigte Sparrate kann nun trotzdem fuer den Folgemonat geaendert werden.
`app_scheduled_savings` speichert ETF-, Cash-Teil und Wirksamkeitsmonat pro Nutzer. Der laufende
Monat bleibt unveraendert; beim ersten App-Zugriff im neuen Monat uebernimmt Rov.E die Vormerkung
als neue Planungsrate. Das ist keine Geldbewegung. Die echte Umschichtung erfolgt weiterhin erst,
wenn der Nutzer die Sparrate im Monatscheck bestaetigt.

Beim 45-Sekunden-Refresh wurde zuvor nur die Summe der Sparrate, nicht aber ihr ETF-/Cash-Split
uebernommen. Das ist korrigiert: Jeder Refresh uebernimmt jetzt beide Teilwerte und die Summe aus
derselben DB-Antwort.

Die Negativwarnung ist visuell bewusst zurueckgenommen: nur der Fehlbetrag ist rot, die Erklaerung
bleibt in normaler Schrift. Die Vermoegenskurve startet bei jedem App-Start auf `1W`; der Nutzer
kann danach wie gewohnt seinen Zeitraum waehlen.

## Report-Queue: Berliner Zeit durchgehend verwenden (01.08.)

Der APScheduler plante die Report-Jobs bereits in `Europe/Berlin`, der Queue-Worker verglich den
naiven SQLite-Zeitstempel aber mit der UTC-Serverzeit. Das haette jeden zufaellig geplanten
Report um zwei Stunden verzoegert. Planung, Abholung, Wiederholungen und Statuszeitstempel nutzen
nun dieselbe Berliner Zeit. Bereits angelegte Jobs bleiben gueltig und werden nach dem
Service-Neustart entsprechend ihrer vorgesehenen Berliner Uhrzeit abgearbeitet.

## August-Start: Planung nicht als eingegangenes Geld ausgeben (01.08.)

Der Cashflow startet im neuen Monat bewusst bei null: Juli-Buchungen bleiben in der Datenbasis,
im Report und in der Historie, erscheinen aber nicht mehr in der August-Monatsliste. Die
Mentor-Leiste unterscheidet nun vor der Gehaltsbestaetigung klar zwischen dem geplanten
Ausgabenrahmen und tatsaechlich frei verfuegbarem Geld. Erst nach `Gehalt ist eingegangen` darf
Rov.E formulieren, dass ein Betrag diesen Monat frei verfuegbar ist.
# Monatswechsel 01.08.2026: Cashflow-Historie

- Die Juli-Daten wurden nicht geloescht. Die Live-Bridge lieferte bisher nur Buchungen des laufenden Monats, weshalb die vorhandene Cashflow-Navigation fuer Juli leer blieb.
- `rove_app_state.py` liefert nun die letzten drei abgeschlossenen Monate als schreibgeschuetzte `txHistory`; die App uebernimmt diese beim Start und bei jedem Live-Refresh.
- Dieser Schritt ist bewusst read-only: Er kopiert keine Budgets, bestaetigt kein Gehalt und bucht weder Fixkosten noch Sparraten. Die Budget-/Zahltag-Logik wird danach separat geloest.
- Der Rollout braucht nur einen Neustart von `rove-app-api`; der laufende Report-Versand im separaten `clarity-bot` wird davon nicht unterbrochen.

## Incident: leerer App-Start nach Frontend-Update

- Die API und `clarity-bot` waren gesund; `/v1/state` antwortete durchgehend mit HTTP 200. Die Finanzdaten waren nicht geloescht.
- Primaere Ursache des kompletten Startabbruchs: Der neue Mentor-Text griff in `mentorLine()` auf `REPORT_MONTHS_DE` zu. `mentorLine()` laeuft beim ersten Rendern, die `const`-Liste wird aber erst spaeter im Report-Abschnitt initialisiert. JavaScript brach mit `ReferenceError: Cannot access 'REPORT_MONTHS_DE' before initialization` ab; deshalb blieben Nettovermoegen, Mentor und Assets als leeres Geruest stehen.
- Fix: Der fruehe Mentor-Text formatiert den Monatsnamen direkt ueber `Date.toLocaleDateString()`. Damit gibt es keine Abhaengigkeit mehr auf spaeter initialisierte Konstanten.
- Zusaetzliche Absicherung in `rove-app/index.html`: State-Abruf mit 8-Sekunden-Grenze; danach zuerst Wiederherstellung der 180-Tage-Sitzung, andernfalls sichtbarer Login statt leerer Finanzansicht. Keine Mock-Daten und keine Datenbankmutation im Fehlerfall.
- Die monatliche Finanzlogik bleibt kalenderbasiert (1. bis Monatsende). Ein Zahltag am 15. bedeutet bis dahin „Ausgabenrahmen geplant, Gehalt noch nicht bestaetigt“, nicht einen verschobenen 15.-bis-15.-Monat.

## Budget-Historie und bewusste Monatsuebernahme (01.08.)

- `rove_app_state.py` liefert Budgetrahmen der letzten drei abgeschlossenen Monate getrennt als `budgetHistory`. Vergangene Limits werden niemals in den aktuellen Monat hineingerechnet.
- Der Budget-Reiter verwendet jetzt dieselbe Monatsnavigation wie der Cashflow. Abgeschlossene Monate zeigen Rahmen, damaligen Verbrauch und Rest/Abweichung schreibgeschuetzt an.
- Ist der aktuelle Monat leer, zeigt Rov.E das letzte gespeicherte Budget als Vorlage. Erst der ausdrueckliche Knopf `Budget aus <Monat> uebernehmen` schreibt neue Zeilen fuer den aktuellen Monat.
- Gekoppelte App-Nutzer erhalten keine automatisch aus den ersten Monatsausgaben erfundenen Hilfsbudgets mehr. Bis zur bewussten Uebernahme bleibt der aktuelle Rahmen leer.
- Diese Aenderung bucht weder Gehalt noch Fixkosten oder Sparraten und aendert keine Reportdaten. Die Zahltag-/Monatscheck-Logik wird als eigener Schritt behandelt.

## Report-Texte: Vermoegen nur noch faktisch beschreiben (02.08.)

- Die bisherige feste Aussage `Mehr als die Haelfte deines Vermoegens arbeitet bereits fuer dich` ist aus Weblink und PDF entfernt. Sie war fuer Nutzer ohne Investments oder mit negativem Nettovermoegen offensichtlich falsch.
- Beide Report-Ausgaben teilen weiter denselben Kontext aus `rove_web_report_renderer.py`. Die Aussage richtet sich jetzt nach Nettovermoegen und dem tatsaechlich investierten Anteil: negativer Stand, 0 EUR, nur liquide Mittel, unter 50 Prozent investiert oder mindestens 50 Prozent investiert.
- Auch die Monatsfazit-Zeile behauptet eine Investition oder Ruecklage nur bei einer echten Monatsbuchung. Andernfalls bezeichnet sie die Sparrate korrekt als geplant.
- Der alte HTML-Fallback-Renderer verwendet dieselben Schutzfaelle, falls der normale WeasyPrint-PDF-Renderer einmal nicht verfuegbar ist.

## Score, Rov.E Punkte und Mentor: gemeinsame Live-Logik (02.08., noch zu deployen)

- Neu: `rove_score.py` ist die einzige Berechnung fuer den ernsthaften Rov.E Score. App-API,
  Report-Engine und der verbleibende Telegram-Bot verwenden damit dieselben vier Faktoren:
  Budget-Kontrolle, Sparrate, Tracking-Konstanz und finanzielle Struktur.
- Der App-State liefert den kompletten Live-Score inklusive Faktorwerten, ehrlicher Erklaerung
  und naechstem Hebel. Die App ersetzt beim Start und beim 45-Sekunden-Refresh nun den ganzen
  Score, nicht nur Zahl und Rang. Dadurch bleiben keine alten Demo-Balken im Profil zurueck.
- Rov.E Punkte (RP) bleiben bewusst eine kleine Gewohnheits-Belohnung und kein zweiter
  Finanzscore. Die erste echte App-Buchung eines Tages vergibt RP und Streak genau einmal,
  transaktionssicher in der gemeinsamen Datenbank. Die App zeigt die Belohnung einmal als
  kurze Bestaetigung; im Score-Screen steht nur der aktuelle Fortschritt und der naechste Rang.
- Die Mentor-Karte priorisiert weiterhin immer: Monatscheck vor Gehalt, echter Negativbetrag
  und frische Budgetwarnung. Im ruhigen Zustand rotiert sie stabil pro Kalendertag zwischen
  Budgetrahmen, Score-Hebel, RP-Fortschritt und Tracking-Konstanz. Kein Wechsel bei jedem
  Refresh und keine Motivationssprüche ohne Datenbasis.
- Geaenderte Dateien: `rove_score.py`, `rove_app_state.py`, `rove_app_api.py`, `report_engine.py`,
  `bot.py`, `work/rove-app/index.html`.
- Lokal geprueft: Python-Kompilierung, JavaScript-Syntax, Git-Diff-Whitespace und eine echte
  lokale Score-Berechnung mit vier Faktoren/RP. Nach Deployment einmal die App neu oeffnen und
  eine kleine echte Ausgabe eintragen: Score-Faktoren, Profil-Badge und maximal eine RP-Bestaetigung
  pruefen.

## RP gegen Buchen-und-Loeschen abgesichert (02.08., noch zu deployen)

- Neue Tracking-Belohnungen erhalten in `rove_point_events` einen eindeutigen Tagesnachweis und
  die ID der Buchung, die den Punkt ausgeloest hat. Bestehende alte RP werden nicht migriert oder
  neu berechnet.
- Wird die ausloesende Buchung geloescht und es gibt am selben Tag noch eine andere Ausgabe,
  wandert der Nachweis auf diese Buchung: RP und Streak bleiben korrekt bestehen.
- Wird die letzte Ausgabe des Tages geloescht, nimmt Rov.E exakt die fuer diesen Tag vergebenen
  RP zurueck. Das gilt auch fuer den 7-/30-Tage- oder spaeteren Wochenbonus. Der aktuelle Streak
  und das letzte Aktivitaetsdatum werden aus den verbleibenden echten Buchungstagen neu gebildet.
- Buchung, Kontogutschrift und RP-Ruecknahme laufen in derselben gesperrten DB-Transaktion. Ein
  Refresh oder paralleler Request kann deshalb keinen halben Zustand hinterlassen.
- Lokal geprueft: zwei Buchungen am selben Tag (erste Loeschung 0 RP, letzte Loeschung -1 RP)
  sowie Tag-7-Bonus (+11 RP, nach Loeschung exakt -11 RP und Rueckkehr zum 6-Tage-Streak).
- Geaenderte Dateien: `rove_score.py`, `rove_app_api.py`. Nur `rove-app-api` muss neu starten.

## Seitenhierarchie und doppelte Kopfzeilen bereinigt (02.08., noch zu deployen)

- Auf allen vier Hauptseiten steht die grosse weisse Hauptueberschrift jetzt zuerst. Auf der
  Uebersicht folgt das Datum darunter; bei Zielen folgt `Deine Toepfe` darunter.
- Cashflow zeigt den Monat nur noch in der Pfeilnavigation. Die zweite Zeile mit demselben Monat
  und derselben Buchungsanzahl im Seitenkopf ist entfernt.
- Vertraege beginnt direkt mit der Hauptueberschrift. `Fixkosten · monatlich` ist entfernt; die
  Summenkarte erklaert bereits `Deine Fixkosten pro Monat`. Das zusaetzliche `/Monat` direkt am
  grossen Betrag ist ebenfalls entfernt.
- Hilfreiche Unterzeilen wurden bewusst behalten, weil sie nicht dieselbe Information wiederholen.
- Geaenderte Datei: `work/rove-app/index.html`. JavaScript-Syntax und verbleibende Header-Texte
  wurden geprueft; fuer das statische Frontend ist kein Service-Neustart erforderlich.

## PDF-Report: Schlussmonat und Score-Seite korrigiert (02.08., noch zu deployen)

- Der Abschlusssatz im PDF ist nicht mehr fest auf August gesetzt. Er nennt jetzt den Monat,
  in dem der naechste Monatsreport erscheint: Ein Juli-Report sagt daher korrekt
  `Wir sehen uns im September`. Der Jahreswechsel November -> Januar ist mit abgedeckt.
- Der Plan auf Seite 10 bleibt bewusst beim direkten Folgemonat: Im Juli-Report ist das der
  Plan fuer August. Der Webreport verwendet diese Logik bereits korrekt und enthaelt keinen
  fehlerhaften festen Abschlusssatz.
- Die vier kleinen Fortschrittsbalken der Score-Aufschluesselung wurden nur aus dem statischen
  PDF entfernt. WeasyPrint platzierte sie ausserhalb ihrer Zeilen und dadurch ueber
  `Dein naechster Schritt`, `Status` sowie unterhalb der Karte. Zahlen und Score-Aufteilung
  bleiben vollstaendig sichtbar; die Web-Animationen bleiben unveraendert.
- Kreisfortschritt, Rangbeschreibung und naechster Rang auf PDF-Seite 6 kommen jetzt aus den
  echten Score-Daten statt aus fest hinterlegten Beispielwerten.
- Lokal geprueft: Python-Kompilierung, Juli -> September, Jahreswechsel November -> Januar,
  Web-Plan Juli -> August, PDF weiterhin exakt 10 Seiten sowie visuelle Kontrolle der Seiten
  6 und 10.
- Geaenderte Dateien: `rove_web_report_renderer.py`, `report_templates/rove_pdf_report.html`.

## Light Mode: dunklen Tabbar-Saum entfernt (02.08., noch zu deployen)

- Die untere Navigation nutzt im hellen Modus jetzt eine deckende weisse Flaeche ohne Blur.
  Der iOS-Blur konnte an der Safe-Area einen dunklen Schatten erzeugen, obwohl kein sichtbarer
  Schatten im Design vorgesehen war. Der dunkle Modus behaelt seine bisherige Glasoptik.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## Light Mode: iOS-Systemfarbe angleichen (02.08., noch zu deployen)

- Die iOS-Systemfarbe (`theme-color`) war bisher fest dunkel. Sie wird nun bereits vor dem
  ersten Rendern und beim Umschalten auf die helle App-Flaeche gesetzt. Damit kann die untere
  Safe-Area im installierten Light Mode keinen dunklen Streifen mehr von der alten Systemfarbe
  uebernehmen.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## Light Mode: Schatten der geschlossenen Sheets entfernt (02.08., noch zu deployen)

- Ursache des schwarzen Nebels unter der Tabbar: Alle etwa zwanzig geschlossenen Eingabe-Sheets
  lagen zwar unterhalb des sichtbaren Bereichs, behielten aber jeweils ihren schwarzen
  Aufwaerts-Schatten. Im hellen Modus addierten sich diese Schatten sichtbar.
- Geschlossene Sheets haben nun keinen Schatten mehr. Erst das aktiv geoeffnete Sheet bekommt
  seinen Schatten zurueck. Die Oeffnungsanimation, Positionierung und der dunkle Modus bleiben
  unveraendert.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Bestaetigungs-Chip in Glasoptik (02.08., noch zu deployen)

- Alle kurzen Bestaetigungen nach einer Eingabe nutzen jetzt dieselbe ruhige Glasoptik wie die
  restlichen iOS-inspirierten Komponenten: abgerundete Karte statt flacher Pill-Form, heller
  Rand, dezenter gruener Verlauf, Innenlicht und weicher Schatten.
- Im hellen Modus ist der Chip entsprechend heller gehalten, damit er nicht wie ein fremder
  gruener Standard-Toast wirkt. Verhalten, Text und Dauer der Rueckmeldung bleiben unveraendert.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Monatscheck im Cashflow statt in Einstellungen (03.08., noch zu deployen)

- Gehalt, Fixkosten und Sparrate sind monatliche Cashflow-Ereignisse, keine Einstellung. Der
  bisherige Abschnitt `Monatsplanung` samt `Monatscheck` wurde deshalb aus den Einstellungen
  entfernt.
- Der Cashflow hat jetzt einen eigenen `Monatsplan`-Chip. Der Chip ist horizontal erreichbar,
  ohne die untere Tab-Leiste mit einem weiteren Symbol zu ueberladen.
- Die Einstellungen enthalten damit keine monatliche Handlung mehr.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Monatsplan-Hinweis als einmaliger goldener Wegweiser (03.08., noch zu deployen)

- Der `Monatsplan`-Chip steht vor `Budgets`, weil die Reihenfolge damit dem Monatsablauf folgt:
  Einnahmen und Ausgaben, Monatsplan, dann Budgetkontrolle.
- Der vorherige goldene Dauerpunkt ist ersetzt. Ein kleiner goldener Punkt erscheint nur, bis der
  Kunde den Monatsplan im jeweiligen Monat erstmals geoeffnet hat. Danach verschwindet er auch
  dann, wenn eine Bestaetigung bewusst erst spaeter erfolgt. Mit einem neuen Monatsplan ist er
  wieder einmal sichtbar.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Ziel-hinzufuegen als Nebenaktion (03.08., noch zu deployen)

- Unter bestehenden Zielen erscheint keine grosse gestrichelte Empty-State-Karte mehr. Sie sah
  aus wie ein weiterer Hero und nahm den echten Zielen ihre visuelle Prioritaet.
- Der Einstieg ist jetzt eine schmale, neutrale Plus-Zeile `Weiteres Ziel anlegen`. Die Funktion
  und der Ziel-Anlegen-Flow bleiben unveraendert.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Kopfbereiche weiter reduziert (03.08., noch zu deployen)

- `Deine Toepfe` unter `Deine Ziele` und die Erklaerung unter `Vertraege` wurden entfernt. Die
  nachfolgenden Karten machen beide Inhalte bereits selbst klar, daher waren die Zeilen doppelt.
- Glocke und Profilkreis auf der Uebersicht sind von 44 auf 40 px verkleinert. Sie bleiben gut
  bedienbar, konkurrieren aber nicht mehr mit dem Vermoegenswert als Hauptinformation.
- Das Datum auf der Uebersicht wurde bewusst nicht verschoben; dessen Platzierung wird separat
  entschieden.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Vertraege-Hierarchie geglaettet (03.08., noch zu deployen)

- Zwischen `Vertraege` und der Fixkosten-Karte liegen jetzt bewusst 20 px. Der Kopf klebt nicht
  mehr an der Hauptinformation, bleibt aber kompakt.
- `+ Vertrag hinzufuegen` ist von einer gefuellten blauen Aktion zu einem ruhigen blauen Umriss
  reduziert. Er bleibt auffindbar, konkurriert aber nicht mehr mit Fixkosten und Vertragsliste.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Uebersicht-Kopf beruhigt (03.08., noch zu deployen)

- `Dein Ueberblick` wurde zu `Uebersicht` reduziert und ist mit 23 px bewusst kleiner als der
  Vermoegenswert. Der Titel orientiert, ohne selbst zum Hero zu werden.
- Das Datum steht direkt darunter als kleine Kontextzeile. Glocke und Profil bleiben bewusst
  links beziehungsweise rechts im vertrauten, ausgeglichenen Kopfbereich.
- Die Vermoegenszahl beginnt direkt nach dem kompakten Kopf; keine Information wurde entfernt.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Vermoegens-Kopf weiter reduziert (03.08., noch zu deployen)

- `Uebersicht` wurde im Home-Kopf komplett entfernt, weil die Tab-Leiste den Ort bereits eindeutig
  macht und ein zweiter Seitentitel nur visuelle Lautstaerke erzeugt.
- Das Datum steht jetzt zuerst als dezenter Kontext. Darunter ist `Gesamtvermoegen` die klare,
  fett gesetzte Ueberschrift in der bisherigen ruhigen Titelgroesse.
- Die grosse Vermoegenszahl wurde von 44 auf 40 px reduziert. Sie bleibt die wichtigste Zahl,
  wirkt aber nicht mehr wie ein zweiter Hero ueber der Seite.
- Nach dem zweiten visuellen Feinschliff steht `Gesamtvermoegen` nun vor dem Datum; die Zahl liegt
  bei 38 px. Das liest sich natuerlicher als Titel, Kontext, Wert und gibt der Kurve mehr Raum.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Zahltag aus Einstellungen in Monatsplan verschoben (03.08., noch zu deployen)

- Der Zahltag steht nicht mehr zwischen Name und E-Mail in den Profileinstellungen. Diese Ansicht
  bleibt damit auf echte Kontodaten beschraenkt.
- Im Monatsplan erscheint er als eigene, antippbare kleine Kachel direkt unter der Einordnung.
  Dort kann der Kunde den Tag auch jederzeit aendern oder erstmals festlegen, ohne ihn in den
  Einstellungen suchen zu muessen.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: Fixkosten von Konsum-Insights getrennt (03.08., noch zu deployen)

- Fixkosten bleiben als echte Abbuchungen im Cashflow sichtbar. Das ist wichtig fuer den vollstaendigen
  Verlauf und die Nachvollziehbarkeit.
- Coach-Fragen wie `Was war meine groesste Ausgabe?`, Ausgaben-Uebersichten, Budget- und
  Konsum-Berechnungen behandeln Miete, Kredit, Hausgeld, Versicherungen und Abos aber nicht mehr
  als frei gewaehlten Konsum. Die groesste Ausgabe meint damit verlässlich die groesste Konsumausgabe.
- Die Gesamtuebersicht nennt Konsumausgaben und Fixkosten getrennt, statt beides in eine Zahl zu
  werfen. So wird eine normale Miete nie mehr als persoenlicher Ausgaben-Ausreisser bewertet.
- Geaenderte Datei: `work/rove-app/index.html`. Nur statisches Frontend, kein Service-Neustart.

## App: ETF-Sparplan getrennt von Cash-Sparen (03.08., noch zu deployen)

- Ein ETF-Sparplan ist jetzt ein eigener, dauerhaft gespeicherter Ablauf: Ausfuehrungstag
  (1 bis 31), Quellkonto (Giro oder Tagesgeld), Modus (automatisch in Rov.E erfassen oder
  kurz bestaetigen) und Pause/Aktivierung liegen in `app_etf_savings_plan`.
- Bestehende App-Kunden werden nicht still umgestellt. Sobald eine ETF-Sparrate vorhanden ist,
  erscheint einmalig bei `ETF & Investments` und im `Monatsplan` die Einrichtung. Bis dahin
  bleibt der bisherige Monatscheck unveraendert.
- Der lokale Erststart-Wizard fragt die drei ETF-Plan-Werte ebenfalls ab und speichert sie im
  lokalen Profil. Die produktive serverseitige Buchung beginnt im aktuellen Beta-Ablauf erst,
  sobald ein Konto mit Rov.E verbunden ist (heute ueber den bestehenden App-/Telegram-Zugang).
  Ein vollstaendiges Direkt-Registrieren ohne vorheriges Konto bleibt ein eigener, spaeterer
  Account-Onboarding-Block und wird nicht als bereits live verkauft.
- Wird ein Plan nach seinem Ausfuehrungstag eingerichtet, beginnt er erst im Folgemonat. Dadurch
  gibt es keine rueckwirkende oder doppelte Buchung. Der automatische Modus erfasst beim ersten
  sicheren App-Kontakt am bzw. nach dem Ausfuehrungstag ausschliesslich die interne Rov.E-
  Umschichtung; es wird niemals eine echte Bank- oder Broker-Order behauptet.
- ETF und Cash bleiben absichtlich getrennt: `Cash-Sparen` wird weiter nur durch die bewusste
  Monatsbestaetigung vom Giro aufs Tagesgeld umgebucht. Ein eingerichteter ETF-Plan wird dabei
  nicht mehr versehentlich mitgebucht.
- Der ETF-Plan kann in der ETF-Kachel oder per Mentor mit Formulierungen wie `ETF-Sparplan
  pausieren`, `ETF-Sparplan weiterlaufen lassen` und bei Bestaetigungsmodus `ETF-Sparplan jetzt
  erfassen` gesteuert werden.
- ETF-Plan-Buchungen zaehlen fuer Score und Monatsreport als echte Sparbewegung. Die
  Zahltags-Erinnerung nennt nur noch flexibles Cash-Sparen, damit ein separater ETF-Plan nicht
  doppelt angemahnt wird.
- Verifiziert mit einer isolierten SQLite-Pruefung: 300 EUR ETF vom Tagesgeld reduzieren
  Tagesgeld 500 -> 200 und erhoehen Investments 2.000 -> 2.300; ein zweiter Lauf im selben
  Monat erzeugt keine zweite Buchung.
- Geaenderte Dateien: `work/Calrity_Main/rove_app_state.py`, `rove_app_api.py`,
  `rove_score.py`, `report_engine.py`, `rove_monthly_reminders.py`, `bot.py` und
  `work/rove-app/index.html`. API-Neustart erforderlich; `bot.py` wird nur fuer die gleiche
  Sparbewegungs-Erkennung im weiterhin laufenden Beta-Bot neu geladen.

## App: ETF-Plan-Auswahl und Holding-Korrektur (03.08., noch zu deployen)

- Die unruhigen Browser-Pop-ups `OK`/`Abbrechen` fuer den ETF-Sparplan wurden durch ein eigenes
  Rov.E-Sheet ersetzt: Ausfuehrungstag, Giro oder Tagesgeld und automatisches Erfassen oder
  kurze Bestaetigung sind als ruhige, sichtbare Auswahl formatiert.
- Bestehende ETF-Holdings aus `portfolio_holdings` zeigen nun ebenfalls den Bearbeiten-Stift.
  Die Korrektur aktualisiert genau dieses Holding und den Gesamtwert. Sie legt keine zweite
  Aktie/ETF-Position an, wie es der bisherige allgemeinen Aktienweg getan haette.
- Jede ETF-Wertkorrektur bekommt einen Audit-Eintrag `manual_adjustment`; der Monatsreport
  zaehlt ihn bewusst nicht als neue Investition. Man korrigiert damit einen Stand, nicht den
  Sparfortschritt.
- Manuell angelegte Aktien wie Under Armour bleiben beim bisherigen Aktien-Korrekturpfad.
- Geaenderte Dateien: `work/rove-app/index.html`, `work/Calrity_Main/rove_app_api.py` und
  `rove_app_state.py`. API-Neustart erforderlich.

## App: Sparraten wieder klar bearbeitbar (03.08., noch zu deployen)

- Der ETF-Plan steuert nur den Ablauf einer ETF-Buchung. Die Hoehe von ETF- und Cash-Sparen hat
  jetzt wieder einen eigenen, sichtbaren Weg: `Monatsplan -> Sparrate aendern` oder im ETF-Plan
  `Rate aendern`.
- Das eigene Sheet zeigt beide Werte nebeneinander. So kann ein Kunde seine ETF-Rate und seine
  flexible Cash-Ruecklage in einem Schritt aendern, ohne unruhige Browser-Pop-ups oder eine
  versteckte Mentor-Eingabe.
- Ist in diesem Monat bereits Cash-Sparen bestaetigt oder der ETF-Plan ausgefuehrt, bleibt die
  echte Buchung unveraendert. Die neue Rate wird verbindlich fuer den Folgemonat vorgemerkt.
  Dadurch wird kein bereits sichtbarer Monatsstand rueckwirkend veraendert.
- Der API-Check erkennt nun auch eine ETF-Plan-Ausfuehrung als bereits erfolgte Sparbewegung. Ohne
  diese Absicherung konnte eine nachtraegliche Aenderung bei ausgefuehrtem ETF-Plan noch im
  laufenden Monat an der Vorgabe drehen.
- Geaenderte Dateien: `work/rove-app/index.html` und `work/Calrity_Main/rove_app_api.py`.
  API-Neustart erforderlich.

## App: ETF-Logik-Audit und reduzierte Bedienung (03.08., noch zu deployen)

- Die ETF-Umschichtung wurde als Geldfluss erneut vollstaendig geprueft: Der Betrag sinkt exakt
  auf dem gewaehlten Quellkonto und steigt exakt bei Investments. Das Gesamtvermoegen bleibt dabei
  unveraendert. Giro darf negativ werden; Tagesgeld lehnt eine nicht gedeckte Rate ohne Teilbuchung ab.
- Pause, Bestaetigungsmodus, automatische Erfassung und die Einmal-pro-Monat-Sperre wurden mit
  isolierten SQLite-Szenarien geprueft. Auch `Monatsende` wird in kurzen Monaten korrekt auf den
  28., 29. oder 30. gelegt.
- Wichtiger Monatswechsel-Fix: Eine vorgemerkte neue Sparrate wird jetzt vor dem ETF-Lauf aktiviert.
  Damit kann am ersten Ausfuehrungstag nicht mehr versehentlich noch einmal die alte Rate gebucht
  werden.
- Eine vorgemerkte Rate wird nun im App-State mitgeliefert und beim erneuten Bearbeiten als
  Ausgangswert verwendet. Mehrfaches Aendern vor dem Folgemonat ueberschreibt dadurch nicht mehr
  unbemerkt einen Teil der bereits vorgemerkten ETF-/Cash-Aufteilung.
- Die ETF-Oberflaeche ist reduziert; alte Mini-Links, dauerhafte Pause-Knoepfe und
  Live-Kurs-Erklaertexte wurden entfernt.
- Nach dem Handy-Check weiter reduziert: Die normale ETF-Kachel zeigt neben dem Betrag nur noch
  `Einstellungen aendern`. Rate, Termin, Quellkonto, Automatik/Bestaetigung/Pause und eine eventuell
  faellige manuelle Erfassung liegen ausschliesslich auf der Einstellungsseite.
- Verifiziert: Python-Kompilierung, JavaScript-Syntax sowie Rechentests fuer Giro, Tagesgeld,
  fehlendes Guthaben, Pause, Bestaetigung, Doppelbuchung und vorgemerkte Folgemonatsrate.
- Geaenderte Dateien: `work/rove-app/index.html`, `work/Calrity_Main/rove_app_api.py`,
  `work/Calrity_Main/rove_app_state.py`. API-Neustart erforderlich; der Bot bleibt unberuehrt.

## Reports: App-Ziel und Sparraten-Wahrheit (04.08., noch zu deployen)

- Der Report liest das alte Profil-Hauptziel aus `users`. App-first Nutzer speichern neue Ziele
  jedoch in `app_goals`. Fehlt das alte Hauptziel, nimmt der Report nun das zuerst angelegte
  App-Ziel als Report-Ziel. Dadurch erscheint nicht mehr der Platzhalter `Dein Ziel` mit `0 EUR`.
- Ein automatisch ausgefuehrter ETF-Plan ist eine echte ETF-Umschichtung, aber keine
  Bestaetigung der gesamten Sparrate: Die Cash-Sparrate kann bewusst noch offen sein. Der Report
  wertet deshalb nur die explizite Monatsplan-Bestaetigung als `Sparrate bestaetigt`; einen
  automatischen ETF-Lauf beschreibt er getrennt als tatsaechliche Investition.
- Geaenderte Datei: `work/Calrity_Main/report_engine.py`. Kein API- oder Bot-Logikumbau; nach
  dem Git-Deploy wird `clarity-bot` neu gestartet, damit `/testreport` den aktuellen Report-Code
  importiert.
- Verifiziert mit isolierten SQLite-Tests: App-Ziel `Haus / 450.000 EUR` wird als Ziel-Fallback
  gelesen; `app_etf_plan` laesst `savings_confirmed` offen; eine echte Monatsplan-Bestaetigung
  setzt ihn auf bestaetigt.

## Reports: Zieltopf statt Gesamtvermoegen (04.08., noch zu deployen)

- PDF und Webreport verwenden auf der Zielseite jetzt ausschliesslich `goal.current_amount` als
  `Im Zieltopf`. Das Gesamtvermoegen erscheint dort nicht mehr als scheinbarer Zielstand.
- Die Ziel-Fortschrittsleiste im PDF ist nicht mehr statisch, sondern nutzt denselben berechneten
  Prozentsatz wie der Webreport.
- Der Cover-Block trennt klar zwischen `Sparrate` (monatlich geplant, noch nicht bestaetigt) und
  `Sparfortschritt` (tatsaechlich investiert oder zurueckgelegt). Eine Planung wird nicht mehr mit
  `+... naeher am Ziel` dargestellt.
- Zielzeit und Zielhebel bleiben deterministisch an Zieltopf und Sparplan gebunden. KI-Texte
  duerfen diese Finanzfakten nicht mehr ueberschreiben. Der KI-Prompt bekommt dieselbe Regel.
- Wiederholung reduziert: Die Uebersicht beschreibt bei investiertem Vermoegen die Aufteilung,
  statt erneut `Mehr als die Haelfte ...` zu behaupten.
- Geaenderte Dateien: `report_engine.py`, `rove_web_report_renderer.py`,
  `report_ai_text.py`, `rove_pdf_report_renderer.py`,
  `report_templates/rove_web_report.html`, `report_templates/rove_pdf_report.html`.
  Alle sind Git-Deploy-Dateien; danach nur `clarity-bot` neu starten.
- Verifiziert: Python-Kompilierung, Diff-Whitespace-Check und synthetische Report-Szenarien fuer
  leeren Zieltopf, geplante Sparrate und tatsaechlichen ETF-Sparfortschritt.

## Reports: Sparfortschritt folgt dem App-Monatsplan (04.08., noch zu deployen)

- Die Cover-Kachel verwendet nicht mehr pauschal alle Investment-Ereignisse des Monats. Eine
  einmalige oder alte Investment-Buchung darf deshalb nicht mehr wie eine vollstaendig bestaetigte
  Monats-Sparrate aussehen.
- Es gibt drei eindeutige Zustaende: `offen` mit geplanter Rate, `ETF-Sparplan` nur fuer den
  tatsaechlich automatisch gebuchten ETF-Betrag, und `Sparfortschritt` nur nach der expliziten
  App-Bestaetigung der gesamten Sparrate. Der volle Betrag erscheint ausschliesslich im letzten
  Zustand.
- Geaenderte Dateien: `work/Calrity_Main/report_engine.py` und
  `work/Calrity_Main/rove_web_report_renderer.py`. Beide gehen per Git auf den Server; danach
  `clarity-bot` neu starten und einen Testreport neu erzeugen.
- Verifiziert mit drei isolierten Render-Szenarien: ungeplant/offen trotz sonstiger
  Investment-Bewegung, automatischer ETF mit Teilbetrag und bestaetigter Gesamtplan.

## Reports: Zieltexte folgen Datenlage (04.08., noch zu deployen)

- Zieltexte sind jetzt zustandsabhaengig statt statisch: Ein leerer Zieltopf bekommt einen klaren
  Startpunkt, ein teilweise gefuellter Topf beschreibt echten Fortschritt, ein voller Topf wird als
  erreicht benannt. Das Gesamtvermoegen wird dabei weiterhin nie als Ziel-Fortschritt ausgegeben.
- Der bisher feste PDF-Text `+128 EUR/Monat` und `7 Jahre frueher` wurde entfernt. Der Hebel wird
  ausschliesslich aus echten Kategorien, Sparplan und Zieltopf gerechnet.
- Bei weniger als drei Tracking-Tagen gibt der Report bewusst keine Kategorie-Empfehlung vor.
  Stattdessen sagt Rov.E klar, dass die Datenbasis noch zu klein ist. Auch die Report-Zusammenfassung
  zieht dann keine ueberhasteten Schluesse aus einzelnen Tagen.
- Der PDF-Header `Your Goal` wurde zu `Dein Ziel` vereinheitlicht.
- Geaenderte Dateien: `work/Calrity_Main/report_engine.py`,
  `work/Calrity_Main/rove_web_report_renderer.py`,
  `work/Calrity_Main/report_templates/rove_web_report.html`,
  `work/Calrity_Main/report_templates/rove_pdf_report.html`.
  Alles geht per Git-Deploy auf den Server; danach nur `clarity-bot` neu starten.
- Verifiziert: Python-Kompilierung, Jinja-Syntax beider Templates und Render-Szenarien fuer
  leeren, laufenden und erreichten Zieltopf.

## Reports: Budgetrahmen als Kontext, nicht als Urteil (05.08., noch zu deployen)

- Die Report-Engine liest gesetzte Kategorie-Budgets bereits aus `category_budgets`. Diese Daten
  werden nun in PDF und Webreport auf der Money-Map-Seite gezeigt, statt eine wiederholte
  `Beste Entscheidung`-Karte zu wiederholen.
- Nur wenn der Nutzer Budgets gesetzt hat, erscheint `Rov.E Budgetrahmen`: entweder alle Rahmen
  eingehalten oder klar der erste ueberzogene Bereich samt Betrag und Zahl der eingehaltenen
  Rahmen. Ohne Budgets bleibt die bisherige Karte sichtbar.
- Budgets sind bewusst kein Kriterium fuer einen `erfolgreichen Monat`. Sparrate/Monatsplan und
  realer Cashflow bleiben getrennte Aussagen; der Budgetblock ist ausschliesslich Orientierung.
- Geaenderte Dateien: `work/Calrity_Main/rove_web_report_renderer.py`,
  `work/Calrity_Main/report_templates/rove_web_report.html`,
  `work/Calrity_Main/report_templates/rove_pdf_report.html`.
  Git-Deploy, danach nur `clarity-bot` neu starten.
- Verifiziert: drei Render-Szenarien (alle Rahmen eingehalten, einzelner ueberzogener Rahmen,
  keine Budgets), Python-Kompilierung, Jinja-Syntax und Diff-Whitespace-Check.

## Betrieb: Automatische Datenbank-Backups (05.08., noch zu deployen)

- Neues Skript `work/Calrity_Main/backup_clarity_db.py` erstellt mit der SQLite-Backup-API einen
  konsistenten Snapshot der produktiven `clarity.db`, ohne Bot oder App-API anzuhalten.
- Jede Sicherung wird vor Erfolgsmeldung mit `PRAGMA integrity_check` geprueft. Schlaegt der Check
  fehl, wird die unvollstaendige Zieldatei wieder entfernt.
- Automatische Dateien tragen den Praefix `clarity_auto_` und werden nach 30 Tagen bereinigt;
  manuelle `/backupnow`-Sicherungen bleiben davon unberuehrt.
- Auf dem Server wird daraus ein systemd-Timer: taeglich um 03:20 Uhr, mit Nachholung nach einem
  Serverausfall. Der Timer wird einmalig direkt gestartet und gegen eine echte Backup-Datei
  geprueft.
- Das ist die lokale Schutzstufe gegen Bedienfehler, fehlerhafte Deploys und Datenbankkorruption.
  Vor echtem Bezahl-Launch folgt als zweite Stufe ein verschluesseltes Offsite-Backup gegen
  kompletten Serververlust.
- Verifiziert mit einer isolierten SQLite-Testdatenbank: Snapshot erstellt, Integritaetscheck `ok`,
  Testinhalt vollstaendig lesbar. Kein Service-Neustart erforderlich.

## App: Taeglicher Wertpapier-Tracker fuer ETF und Aktien (05.08., noch zu deployen)

- `ETF & Investments` kann bestehende ETF- und Aktienpositionen jetzt auf eine echte taegliche
  Kursbewertung umstellen. Pro Position werden Boersenkürzel, Stueckzahl und Kurswaehrung erfasst.
- Der vorhandene Twelve-Data-Key bleibt die Kursquelle. EUR-Positionen werden direkt bewertet;
  USD, GBP und CHF werden beim Abruf in EUR umgerechnet. Passt die vom Anbieter gemeldete
  Waehrung nicht zur Auswahl, wird die Einrichtung abgelehnt statt ein falscher Wert gespeichert.
- Wechselkurse werden pro Tageslauf je Waehrung nur einmal abgerufen. Mehrere US-Aktien verbrauchen
  deshalb nicht fuer jede Position einen weiteren USD/EUR-Aufruf.
- Die automatische Bewertung wird erst nach einem erfolgreichen Kursabruf aktiviert. Bis dahin
  bleibt der bisherige manuelle Positionswert die verbindliche Wahrheit.
- Beim ersten Abruf wird nur die Differenz zwischen bisherigem Wert und berechnetem Marktwert auf
  `users.current_investments` gebucht. Spaetere Abrufe wenden ebenfalls nur das Tagesdelta an;
  dadurch gibt es keine Doppelzaehlung.
- Kursbewegungen erhalten den Audit-Typ `market_valuation`. Sie veraendern Vermoegen und Chart,
  gelten aber weder als neue Sparrate noch als Investment-Einzahlung im Monatsreport.
- Bestehende Beta-Positionen ohne Stueckzahl behalten ihre bisherige taegliche Prozentanzeige und
  werden nicht still auf automatische Vermoegensbewertung umgestellt.
- Eine live gepflegte Position kann nicht parallel ueber die alte manuelle Wertkorrektur veraendert
  werden. Schlaegt eine einzelne Position fehl, laufen alle anderen Bewertungen weiter; Symbol und
  Fehlergrund erscheinen im Serverprotokoll, ohne den Tagesjob abzubrechen.
- Nach dem ersten Handytest wurde der Fehlerpfad nachgeschaerft: fehlender/abgelehnter API-Key,
  Rate-Limit, falsche Waehrung, unbekanntes Symbol und Provider-Ausfall werden getrennt gemeldet.
  Der Kursdialog hat neben dem oberen X einen zweiten Schliessen-Button und kann auch nach einem
  fehlgeschlagenen Abruf nicht mehr zur Sackgasse werden. `/health` zeigt ohne Secret nur noch an,
  ob der Kurs-Key im App-Service angekommen ist (`marketDataConfigured`).
- Ursache des feststehenden Kursdialogs im ersten Live-Test war anschliessend eindeutig gefunden:
  `markettrackingsheet` fehlte in der zentralen `closeAllSheetsSoft()`-Liste. Dadurch liefen X,
  Hintergrund und Speichern zwar in `closeSheet()`, entfernten aber genau dieses Sheet nicht.
  Das Sheet ist jetzt zentral registriert; alle vorhandenen Schliesswege greifen damit wirklich.
- Der erste reale Xetra-Test (`AUM5`, EUR) zeigte ausserdem, dass HTTP 404 vom Kursanbieter noch
  faelschlich als Provider-Ausfall erschien. 400/404/422 werden jetzt korrekt als unbekannte
  Notierung gemeldet. Der Dialog schreibt nach jedem Versuch eindeutig `Nicht gespeichert` und
  empfiehlt bei deutschen Mehrfach-Listings das Twelve-Format mit Boersenplatz, z. B. `AUM5:XETR`.
- Der Kursjob bleibt vorerst im laufenden `clarity-bot`-Service um 22:30 Uhr. Vor dem spaeteren
  Abschalten dieses Services muss der Job in einen eigenen systemd-Timer verschoben werden.
- Geaenderte Dateien: `work/Calrity_Main/rove_market_data.py`, `bot.py`, `rove_app_api.py`,
  `rove_app_state.py`, `report_engine.py` und `work/rove-app/index.html`.
- Verifiziert: Python- und JavaScript-Syntax, steigender und fallender Tageskurs ohne
  Doppelzaehlung sowie echter API-Endpunkttest mit einer bestehenden Aktienposition.
- Naechster Produktblock: Screenshot-Import fuer Kontoumsaetze mit editierbarer Vorschau,
  ausdruecklicher Bestaetigung und ohne automatische Buchung.

## App: Europaeischer Kurs-Fallback fuer Xetra-ETF (05.08., noch zu deployen)

- Der reale Amundi-Test hat die Produktgrenze von Twelve Data sichtbar gemacht: `AUM5` ist dort
  im kostenlosen Tarif nicht freigeschaltet. Das ist kein falsches Tickerformat und kein Fehler
  in Furkans Position.
- Rov.E nutzt deshalb zwei klar getrennte Kursquellen: Twelve Data bleibt fuer US-Aktien und
  unterstuetzte Titel bestehen; Leeway ist der automatische Fallback fuer europaeische/Xetra-
  Positionen. Das App-Kuerzel `AUM5:XETR` wird intern in Leeways Format `AUM5.XETRA` uebersetzt.
- Eine Position wird weiterhin erst nach einem erfolgreichen echten Kursabruf aktiviert. Fehlen
  Leeway-Token oder Freigabe, bleibt der bisherige manuelle Wert unveraendert und die App meldet
  klar, dass der Xetra-Kursdienst noch nicht verbunden ist.
- Der verwendete Anbieter wird pro Position in `market_data_provider` gespeichert und im
  Investment-Detail sichtbar. `/health` meldet getrennt `marketDataConfigured` und
  `europeMarketDataConfigured`, ohne einen Key preiszugeben.
- Rechenweg isoliert verifiziert: `66,127763` Anteile zu `154,25 EUR` ergeben `10.200,21 EUR`.
  Nur die Differenz zum bisherigen Positionswert veraendert das Gesamtvermoegen; das Ereignis
  bleibt `market_valuation` und wird nicht als Sparrate oder Einzahlung gewertet.
- Geaenderte Dateien dieses Nachtrags: `work/Calrity_Main/rove_market_data.py`,
  `rove_app_api.py`, `rove_app_state.py` und `work/rove-app/index.html`. Nach dem Git-Deploy muessen
  `rove-app-api` und wegen des taeglichen Kursjobs auch `clarity-bot` neu gestartet werden.

## App: Investment-Ansicht auf Positionen fokussiert (05.08., noch zu deployen)

- In `ETF & Investments` und `Krypto` sind die einzelnen Positionen jetzt die klare visuelle
  Hauptsache: Name und Wert erscheinen kontrastreich; nur die technische Meta-Zeile (Stueckzahl,
  Kuerzel, Kursquelle) bleibt zurueckhaltend.
- `Position hinzufuegen` ist kein dauerhaftes Formular mehr. Es ist eine dezente Zeile mit kleinem
  Plus, die die Eingabe erst auf ausdruecklichen Tipp aufklappt. Das reduziert visuelle Unruhe,
  ohne den Weg zum Nachtragen zu verstecken.
- Das aufklappende Formular speichert auch den Bearbeitungszustand einer bestehenden Position.
  Dadurch ist die bisher lose Referenz beim Speichern behoben: Ein geoeffneter Bearbeitungsvorgang
  weiss sicher, welche Position er aktualisiert.
- Der ETF-Sparplan steht nicht mehr oberhalb der Wertpapiere als dominante Karte. Er folgt ganz
  unten als schmale `ETF-Sparplan · Betrag ›`-Zeile und fuehrt von dort in dieselbe bestehende
  Einstellungsseite fuer Rate, Termin, Quellkonto und Pause.
- Es ist ausschliesslich `work/rove-app/index.html` geaendert. Daher nur per `scp` hochladen,
  kein Git-Push und kein Service-Neustart. Nach dem Upload die App komplett schliessen und wieder
  oeffnen; nicht loeschen oder neu installieren.
# 05.08.2026 - Investment-Ansicht: Position statt globaler ETF-Kachel

- Die ETF-Sparplan-Einstellung steht nicht mehr unter allen Investment-Positionen, sondern nur noch beim Bearbeiten eines konkreten ETF-Holdings.
- Die Positionsliste hat nur noch den Stift als Einstieg. Kursdaten sind aus dem zusaetzlichen Badge entfernt und erscheinen zusammen mit Wert, Sparplan und Loeschen im aufgeklappten Bearbeitungsbereich.
- iOS-Fix: Der aufgeklappte Positionseditor war durch `max-height:190px` abgeschnitten. Die Grenze ist erweitert und das gesamte Vermoegenswert-Sheet scrollt nun selbst, damit Kursdaten und Sparplan erreichbar bleiben.
- Manuell in der App angelegte Aktien/Krypto-Positionen haben jetzt einen echten, zweistufigen Loeschweg. Das Loeschen geht an die API, entfernt nur App-eigene Korrekturen und passt `current_investments` an; Bot-Historie wird nie still geloescht.
- Offener Ausbau: Fuer mehrere ETFs braucht Rov.E separate Sparplan-Datensaetze pro Holding (Rate, Ausfuehrungstag, Quelle). Der derzeitige Buchungsplan ist bewusst noch ein Nutzer-Plan und wird nicht irrefuehrend als mehrere eigenstaendige Automatiken verkauft.

## App: Eigener Sparplan pro ETF-Position, Phase 1 (05.08., noch zu deployen)

- Jeder ETF in `portfolio_holdings` kann jetzt einen eigenen Plan mit Monatsrate,
  Ausfuehrungstag, Quellkonto, Modus und Aktivstatus erhalten. Die Zuordnung erfolgt ueber die
  serverseitige Holding-ID; gleich benannte oder spaeter hinzukommende ETFs ueberschreiben sich
  dadurch nicht gegenseitig.
- Die App zeigt und bearbeitet den Sparplan direkt im Stift-Menue der konkreten ETF-Position.
  Bei genau einem bestehenden ETF wird die bisherige globale ETF-Rate einmalig als Vorschlag
  angezeigt. Bei mehreren ETFs erfindet Rov.E keine Verteilung; jede Rate wird bewusst gesetzt.
- Neuer API-Endpunkt: `POST /v1/etf-position-plan`. Er prueft Token, Besitz der Position,
  ETF-Typ, Betrag, Tag, Quellkonto und Modus. Eine fremde Holding wird abgewiesen.
- Diese Phase speichert nur die Verteilung. Sie veraendert weder Giro noch Tagesgeld noch den
  Depotwert und ersetzt den laufenden globalen ETF-Buchungsplan noch nicht. So kann die
  Mehrfach-ETF-Struktur zuerst ohne finanzielles Risiko getestet werden.
- Verifiziert: Python- und JavaScript-Syntax, Diff-Whitespace, zwei getrennte ETF-Plaene
  (`200 EUR` vom Tagesgeld und `100 EUR` vom Girokonto), Fremdzugriff abgewiesen und beide
  Kontostaende nach dem Speichern unveraendert.
- Naechster Schritt nach dem Handytest: Summe der Positionsraten gegen die globale ETF-Sparrate
  pruefen und anschliessend die atomare, idempotente Buchung pro ETF bauen. Erst dann wird der
  alte globale Ausfuehrungspfad abgeloest.
- Nach dem ersten Bediencheck wurde die fehlende Typwahl geschlossen: Beim Anlegen einer Position
  in `ETF & Investments` waehlt der Nutzer jetzt eindeutig `ETF` oder `Aktie`. Nur ETFs werden als
  `portfolio_holdings` gespeichert und erhalten die Sparplan-Einstellungen; Aktien bleiben ohne
  Sparplan.
- Eine zuvor in der App versehentlich als Aktie angelegte manuelle Position kann im Stift-Menue
  auf ETF umgestellt werden. Dabei wird nur die App-eigene Aktienkorrektur umgewandelt und der
  bestehende Gesamtwert als unzugeordneter Bestand verrechnet. Test: 500-EUR-Aktie wurde zum ETF,
  waehrend `current_investments` exakt 500 EUR blieb; ein neuer zweiter ETF mit 300 EUR erhoehte
  die Summe anschliessend korrekt auf 800 EUR.

## App: Kontrollierter Screenshot-Import fuer Kontoumsaetze (08.08., noch zu deployen)

- In der Plus-Leiste gibt es jetzt `Umsaetze aus Screenshot`. Die App verkleinert das Bild noch
  auf dem Geraet, sendet es einmal zur Analyse und speichert das Bild selbst nicht dauerhaft.
- Die Analyse ist bewusst keine automatische Buchung: Rov.E zeigt jede erkannte Ausgabe mit
  Haendler, Betrag, Datum und Kategorie in einer editierbaren Vorschau. Erst ausdruecklich
  ausgewaehlte Zeilen werden gemeinsam gespeichert.
- Wahrscheinliche Dubletten werden markiert und standardmaessig nicht ausgewaehlt. Ein stabiler
  Import-Schluessel verhindert ausserdem, dass dieselbe bestaetigte Zeile durch einen erneuten
  Request doppelt gebucht und doppelt vom Giro abgezogen wird.
- Screenshot-Eingaenge werden vorerst nur gezaehlt und nicht automatisch als Einkommen verbucht.
  Damit kann ein Kontostand, eine Gutschrift oder eine missverstandene Zeile keine Finanzlogik
  ungefragt veraendern.
- Der Server akzeptiert nur JPEG, PNG und WebP bis 5 MB, maximal 20 Zeilen und hoechstens zehn
  echte Analyseversuche pro Nutzer und Tag. Ungueltige Dateien zaehlen nicht gegen dieses Limit.
- Der Sammelimport laeuft in einer Datenbanktransaktion. Jede bestaetigte Ausgabe landet in der
  gemeinsamen `expenses`-Tabelle, senkt das Giro, erhaelt Rov.E Points und erscheint damit auch
  in Budget, Bot-Abfragen und Report. Gelernte Haendlerkategorien werden weiterverwendet.
- Das allgemeine LLM fuer den Mentor bleibt bewusst noch unangetastet. OpenAI wird in dieser
  Phase ausschliesslich fuer die eng begrenzte Screenshot-Erkennung verwendet.
- Verifiziert: Python- und JavaScript-Syntax, Diff-Whitespace sowie ein isolierter API-Test auf
  einer temporaeren Datenbankkopie. Der Test erkannte eine Ausgabe, ignorierte eine Einnahme,
  senkte Giro exakt um 12,34 EUR und verhinderte beim zweiten Commit den Doppelabzug.
- Geaenderte Dateien: `work/Calrity_Main/rove_app_api.py` und `work/rove-app/index.html`.
- Fuer live muss der bereits fuer den Bot vorhandene `OPENAI_API_KEY` sicher in die Environment
  von `rove-app-api` uebernommen werden. Danach Python ueber Git deployen, App per `scp`
  aktualisieren und nur `rove-app-api` neu starten.
- Bedienkorrektur nach erstem Live-Test: Nach einer erfolgreichen Uebernahme wird die komplette
  Screenshot-Vorschau sofort zurueckgesetzt. Beim naechsten Oeffnen steht wieder nur
  `Noch kein Screenshot ausgewählt.` Dort bleiben weder importierte noch bewusst abgewählte
  Zeilen sichtbar, weil sie zu keinem weiteren Importlauf gehoeren.

## App: Sprache und leere Zustaende (09.08.)

- Der Mentor-Fallback spricht jetzt menschlicher: Statt einer knappen Fehlermeldung fragt Rov.E,
  ob es um eine Ausgabe, einen Vertrag, ein Ziel oder den Monatsstand geht, und gibt ein natuerliches
  Beispiel fuer die naechste Eingabe.
- Leere Cashflow-, Vermoegens- und Suchansichten erklaeren jetzt kurz, was fehlt und welcher naechste
  Schritt moeglich ist. Die bestehende Begruessung beim Oeffnen des Mentors bleibt unveraendert.
- Screenshot-, Budget- und Analysefehler wurden sprachlich beruhigt und konkreter formuliert. Die
  Hinweise sagen jetzt, was passiert ist und was der Nutzer als Naechstes tun kann.
- Keine Finanzlogik, Datenquelle oder Berechnung geaendert. Nur sichtbare Texte in
  `work/rove-app/index.html` wurden angepasst.

## Rov.E Score: echte Lage statt Zeitdeckel (09.08., noch zu deployen)

- Die bisherige zeitliche Score-Grenze (`59` in den ersten 30 Tagen, danach stufenweise mehr)
  ist entfernt. Sie hielt gute wie schlechte Veraenderungen kuenstlich fest und war der Grund,
  warum der Score lange exakt bei `59 · Controller` stehen konnte.
- Die Datentiefe bleibt sichtbar, begrenzt den Score aber nicht mehr. App-State und Score-Seite
  zeigen getrennt `Datengrundlage`, Plattformtage und aktive Tracking-Tage.
- Budget-Kontrolle rechnet jetzt mit dem echten Ausgabenrahmen `Einkommen - Fixkosten - geplante
  Sparrate`. Im laufenden Monat bewertet Rov.E zusaetzlich das Ausgabentempo bis zum heutigen Tag;
  fuer abgeschlossene Report-Monate gilt der volle Monatsrahmen.
- Ein Nutzer ohne echte Buchung erhaelt keine Budgetpunkte. Liegen die Konsumausgaben ueber dem
  Rahmen nach Fixkosten und Sparrate, kann der Gesamtwert hoechstens `64` erreichen. Tracking und
  Cash-Puffer koennen einen ueberzogenen Monat dadurch nicht als Manager-Monat darstellen.
- Sparrate bleibt ein eigener Faktor: Eine geplante Rate zaehlt teilweise, die vollstaendige
  Punktzahl gibt es erst nach echter Ausfuehrung/Bestaetigung. ETF- und Cash-Anteil nutzen weiter
  die gemeinsame App-/Report-Datenbank.
- Tracking-Konstanz verlangt nicht mehr unrealistische 90 aktive Tage. Das Ziel waechst mit der
  Datengrundlage und liegt langfristig bei 30 aktiven Tagen innerhalb des 90-Tage-Fensters.
- Finanzielle Struktur bewertet Cash-Puffer, vollstaendige Basisdaten und einen positiven Rahmen
  nach Fixkosten und Sparrate. Die Sparquote wird dort nicht mehr ein zweites Mal bepunktet.
- Veraltete, ungenutzte Frontend-Faktoren (`Notgroschen`, `Vermoegensaufbau`, `Sparquote`) wurden
  entfernt. App, API, Bot und Report beziehen die vier Faktoren weiterhin aus `rove_score.py`.
- Geprueft mit festen Szenarien: keine Daten `35`, im Tempo `85`, 507 EUR ueber Rahmen mit offener
  Sparrate `60`, mit bestaetigter Sparrate durch Schutzgrenze `64`, abgeschlossener perfekter Monat
  `100`. Python- und JavaScript-Syntax sowie Diff-Whitespace sind sauber.
- Nach dem ersten Live-Check wurde Tracking-Konstanz bewusst strenger kalibriert: Volle `25/25`
  sind erst ab 90 Tagen Datengrundlage und mindestens 30 aktiven Tracking-Tagen moeglich. Vorher
  steigt die maximale Faktorwertung stufenweise auf `8` (unter 30 Tage), `16` (unter 60 Tage) und
  `22` (unter 90 Tage). Das ist kein allgemeiner Score-Zeitdeckel; nur die behauptete Konstanz muss
  sich tatsaechlich ueber mehrere Monate beweisen.
- Verifiziert: bei 10/40/70 Tagen lag die Konstanz trotz guter Aktivitaet hoechstens bei 8/16/22.
  `100/100` war im Test erst bei 90 Tagen, bestaetigter Sparrate, perfektem Budget/Strukturwert und
  30 aktiven Tagen erreichbar; mit 29 aktiven Tagen blieb der Score bei `99`.
- Score-Screen anschliessend visuell reduziert: Unter Rang und Ring steht nur noch die kompakte
  Zeile `44 Tage aktiv bei Rov.E · Trackingtage 33/44`, danach unveraendert RP und der naechste
  Fortschrittsrang. Der erklaerende Datengrundlagen-Absatz, der offensichtliche Tipp-Hinweis und
  die Pfeile an jedem Faktor wurden entfernt. Die komplette Faktorzeile bleibt antippbar und
  klappt Begruendung und Hebel weiterhin auf.

## App: Ruhiger Home-Header und Score-Hierarchie (09.08., noch zu deployen)

- Die Plattformtage und Trackingquote auf der Score-Seite bleiben eine ruhige Zeile, ihre beiden
  Werte werden jetzt jedoch minimal in der primaeren Textfarbe hervorgehoben. Das funktioniert
  adaptiv in Dunkel- und Hellmodus, ohne eine neue Signalfarbe einzufuehren.
- Das Datum wurde auf Wunsch nach dem gemeinsamen Designcheck vollstaendig von der Startseite
  entfernt. Die Kopfzeile besteht wieder nur aus Glocke links und Profil rechts.
- Keine Daten-, Score- oder Finanzlogik geaendert. JavaScript-Syntax und Diff-Whitespace sind sauber.
- Geaenderte Datei: `work/rove-app/index.html`.

## App: Rov.E HUD-Glas und Score-Reaktor (09.08., noch zu deployen)

- Der dunkle Modus bekommt eine kontrollierte futuristische Ebene nach der gelieferten
  Jarvis-/HUD-Referenz. Rov.E-Blau, Typografie, Abstaende und Navigation bleiben erhalten.
- Zentrale Inhaltskarten (`card`, Monats-/Fixkosten-Summen, Mentor und Statuskarten) verwenden
  jetzt ein tieferes halbtransparentes Glas und eine feinere Cyan-Kante. Die zuerst getesteten
  technischen Eckmarken wurden nach dem Handy-Check vollstaendig entfernt, weil sie auf dem
  kleinen Format billig und abgeschnitten wirkten.
- Der Rov.E Score wurde visuell zu einem mehrschichtigen Reaktor umgebaut: technischer Aussenring,
  langsame Skala, echter Score-Fortschrittsbogen, gegenlaeufiger Innenring und ruhiger Lichtkern.
  Die Berechnung und die Darstellung `von 100` bleiben unveraendert.
- Auch die vier Score-Faktoren folgen jetzt dem Reaktor-Stil. Jede Faktorfarbe steuert dezent
  Rahmen, Zahlenleuchten und Fortschrittsbalken; die aufgeklappte Begruendung bleibt als ruhige,
  leicht getoente Glasflaeche lesbar. Bedienung und Faktorwerte wurden nicht veraendert.
- Die Vermoegenskurve nutzt jetzt eine schmale Rov.E-Verlaufslinie mit dunkler Unterkante und
  sehr feinem hellem Glas-Highlight. Nach dem Handy-Feedback wurden die blaue Flaechenfuellung,
  der breite Neon-Glow und die grossen leuchtenden Endkreise entfernt. Auch beim Abfahren der
  Kurve bleibt diese klare Linienoptik erhalten.
- Die Ringbewegung ist bewusst langsam und nicht interaktiv. Bei `Bewegung reduzieren` werden alle
  Reaktor-Animationen deaktiviert. Der Hellmodus behaelt seine klare helle Kartenwirkung.
- Keine Finanz-, Daten- oder Scorelogik geaendert. JavaScript-Syntax und Diff-Whitespace sind sauber.
- Geaenderte Datei: `work/rove-app/index.html`.

## App: moderne schwebende Navigation (09.08., noch zu deployen)

- Die untere Navigation verwendet jetzt eine schwebende, breite Glas-Kapsel nach der gelieferten
  Referenz. Rov.E-Icons, Bezeichnungen, Reihenfolge und das zentrale Schnell-Erfassen bleiben.
- Der aktive Bereich wird als eigene weiche Pill-Flaeche hervorgehoben. Das aktive Icon behaelt
  Rov.E-Blau und einen kleinen Lichtsaum; das Plus sitzt in einer kraeftigeren blauen Glaskachel.
- Die Aenderung ist bewusst rein visuell innerhalb des vorhandenen Containers umgesetzt. Die
  kritischen Werte fuer `position:fixed`, Padding, Safe Area, gemessene Hoehe und der
  `LEISTEN-ANKER` wurden nicht veraendert. Damit soll der behobene iOS-Sprung nicht zurueckkehren.
- Hellmodus besitzt eine eigene helle Kapsel ohne dunklen Safe-Area-Saum.
- Nach dem ersten iPhone-Test wurde der Live-Blur der inneren Kapsel entfernt, weil iOS diese
  Ebene beim Scrollen sichtbar mit dem Inhalt verschoben hat. Die Navigation liegt jetzt auf
  einer eigenen festen Compositor-Ebene (`translate3d`, ausgeblendete Rueckseite, Containment),
  waehrend die nahezu identische Glasoptik ueber den halbtransparenten Verlauf erhalten bleibt.
- JavaScript-Syntax und Diff-Whitespace sind sauber; alle vier Anker-Sicherungen wurden nach der
  Aenderung nochmals statisch geprueft.

## App: Rov.E Coach als Vollbild-Chat (10.08., noch zu deployen)

- Ein Tipp auf die Mentor-Karte oeffnet Rov.E jetzt als eigene bildschirmfuellende Unterhaltung
  statt als halbhohes Standard-Sheet. Quick-Add und alle kurzen Eingabedialoge bleiben kompakt.
- Der Chat besitzt einen festen Kopf mit Rov.E und einem eigenen Schliessen-Button, einen separat
  scrollbaren Nachrichtenbereich sowie Vorschlaege und Eingabe am unteren sicheren Bildschirmrand.
- Die Hoehe nutzt den dynamischen mobilen Viewport und beruecksichtigt obere und untere iPhone-
  Safe-Areas. Dadurch bleibt die Eingabe auch als vollwertige Chatoberflaeche erreichbar.
- Antwortlogik, Vorschlaege, Datenzugriff und Finanzlogik wurden nicht veraendert.
- Der statische Vorschlag `Warum bin ich Controller?` wurde durch die fuer jeden Score-Status
  passende Frage `Wie steht es um meinen Rov.E Score?` ersetzt.
- Die Coach-Vorschlaege wurden gestrafft und staerker auf echte Nutzerfragen ausgerichtet:
  Monatsstand, Tagesrahmen, Leistbarkeit, groesste Ausgaben, Budgets, ETF, Kuendigung und
  Sparquote. Die Rueckfrage bei nicht erkannten Eingaben ist jetzt kuerzer und fuehrender:
  Rov.E fragt direkt, ob es um Ausgabe, Vertrag, Ziel oder Monatsstand geht.
- Der erste Vorschlag heisst jetzt `Wie laeuft mein Monat?` statt `Wie steht mein Monat?`.
  Dafuer gibt es eine eigene Antwort `ans_month_status()`: Rov.E zeigt Monatslage, geplanten
  Ausgabenrahmen, bisherige Ausgaben, verbleibenden Betrag, Tagesorientierung, groesste Buchung
  und bei Bedarf den auffaelligen Budgettopf. Vor bestaetigtem Gehalt bleibt die Antwort bewusst
  kurz: geplanter Ausgabenrahmen, bisherige Ausgaben, fehlende Gehaltsbestaetigung und nur bei
  Bedarf eine konkrete Budgetwarnung. Keine Formulierung wie "Der Monat ist vorbereitet".
- Der aktuelle Chatverlauf lebt nur im DOM der laufenden App-Sitzung: Sheet schliessen/oeffnen
  behaelt ihn, ein Reload, Prozess-Neustart oder iOS-Speicherrauswurf startet einen neuen Chat.
  Es gibt bewusst noch keine dauerhafte Chat-Historie in Datenbank oder Local Storage.
- Produktentscheidung: Normale Rov.E-Coach-Gespraeche bleiben bewusst fluechtig. Persistiert
  werden nur echte Finanzobjekte und Nutzerentscheidungen wie Budgets, Ziele, Reports,
  Buchungen, Kontostaende und Plaene. Falls spaeter Coaching, Finanz-Reset oder Begleitung
  gebucht wird, sollen Dokumente, Aufgaben, Analysen oder persoenliche Dateien separat in einem
  Dokumenten-/Archivbereich der App landen, nicht als komplette Chat-Historie.
- Zukunftsplan fuer Rov.E Begleitung: Kunden mit gebuchter Begleitung bekommen spaeter einen
  separaten Premium-/Begleitungsbereich in der App. Dort sollen Unterlagen, Finanzplan,
  Haushaltsbuch/Monatsuebersicht, Coach-Budgetvorschlaege, Aufgaben und persoenliche Analysen
  auffindbar sein - aehnlich wie Reports im Archiv, aber fuer 1:1-Coaching-Unterlagen. WhatsApp
  oder E-Mail koennen fuer Kommunikation bleiben; die App wird der saubere Ablageort fuer alles,
  was dauerhaft wichtig ist.
- JavaScript-Syntax und Diff-Whitespace sind sauber; nur `talksheet` traegt die Vollbildklasse.

## Datenschutz: vollstaendiger App-Datenexport (10.08., noch zu deployen)

- In den Einstellungen verbundener App-Konten erscheint unter `Daten` jetzt
  `Meine Daten herunterladen`. Ein Tipp erzeugt einen ZIP-Export und laedt ihn direkt auf das
  Geraet. Lokale Demo-/Profilmodi werden dadurch nicht veraendert.
- Neuer API-Endpunkt `GET /v1/data-export`. Der Export verlangt gleichzeitig den gueltigen
  App-State-Token und die gueltige HttpOnly-E-Mail-Sitzung desselben Nutzers. Ein geleakter
  State-Link allein reicht deshalb nicht fuer den Download aller Finanzdaten.
- Das ZIP entsteht nur im Arbeitsspeicher und wird nicht als weitere Exportdatei auf dem Server
  gespeichert. Es enthaelt `daten.json`, getrennte UTF-8-CSV-Dateien fuer Buchungen, Konten,
  Budgets, Vertraege, Ziele, Immobilien, Investments, Score/RP, Monatsplaene und Reportstatus
  sowie alle vorhandenen statischen PDF-Reports. Alte gzip-Reportarchive werden beim Export
  transparent entpackt.
- Ablaufende Weblinks und Sicherheitsdaten wie Login-Codes, Session-Hashes, App-State-Tokens und
  Push-Abos sind bewusst ausgeschlossen. CSV-Texte werden gegen Tabellenformeln abgesichert.
- Produktentscheidung: Abo-Kuendigung und Datenloeschung bleiben getrennt. Kuendigung beendet
  spaeter nur den bezahlten Zugang zum Laufzeitende; sie darf die Finanzhistorie nicht automatisch
  vernichten. `Konto und Daten loeschen` wird im naechsten separaten Schritt mit erneuter
  Bestaetigung, Sitzungswiderruf und vollstaendiger Tabellen-/Dateibereinigung gebaut.
- Geprueft: Python kompiliert, alle vier JavaScript-Bloecke sind syntaktisch sauber, Diff-Whitespace
  ist sauber. Flask-Test gegen Wegwerf-DB prueft fehlende Sitzung (401), falsches Token (401),
  gueltigen Export (200), CSV-Inhalt, PDF im ZIP und Ausschluss der Sessiontabelle.

## Datenschutz: sichere Kontoloeschung (10.08., noch zu deployen)

- In den Einstellungen verbundener App-Konten steht unter `Daten` jetzt getrennt vom Export
  `Konto und Daten loeschen`. Eine spaetere Abo-Kuendigung bleibt davon bewusst getrennt und
  beendet nur den Zugang zum Laufzeitende; sie loescht keine Finanzhistorie.
- Der Ablauf ist absichtlich zweistufig: Rov.E empfiehlt zuerst den vollstaendigen Datenexport,
  sendet danach einen separaten sechsstelligen Loeschcode an die verifizierte Login-E-Mail und
  verlangt zusaetzlich die Eingabe `LOESCHEN`. Der Code gilt zehn Minuten, alte Codes werden
  ungueltig und Fehlversuche sowie neue Code-Anfragen sind begrenzt.
- Die beiden neuen API-Endpunkte verlangen gleichzeitig den gueltigen App-State-Token und die
  gueltige HttpOnly-E-Mail-Sitzung desselben Nutzers. Ein State-Link oder eine offene App allein
  reicht deshalb nicht fuer die irreversible Loeschung.
- Bei erfolgreicher Bestaetigung entfernt Rov.E das Nutzerprofil, Buchungen, Konten, Budgets,
  Vertraege, Ziele, Immobilien, Investments, Score/RP, Monatsplaene, Report-Jobs, Push-Daten,
  App-Zugangslinks, Login-Sitzungen und alle weiteren Tabellenzeilen mit derselben `user_id`.
  Zugehoerige App-State-Dateien, statische Webreports, PDFs und gzip-Reportarchive werden danach
  ebenfalls entfernt. Die App leert ihre lokalen Verbindungsdaten und kehrt zum Start zurueck.
- Bestehende rotierende Systembackups werden nicht nachtraeglich manipuliert. Geloeschte Daten
  koennen dort bis zum Ende der festgelegten Backup-Aufbewahrungsfrist enthalten sein und duerfen
  nur fuer eine notwendige Wiederherstellung genutzt werden. Diese Frist muss vor Launch in der
  Datenschutzerklaerung konkret benannt werden.
- Geprueft: Python kompiliert, alle vier JavaScript-Bloecke sind syntaktisch sauber und
  Diff-Whitespace ist sauber. Ein isolierter Flask-Test beweist: falscher Code loescht nichts;
  der richtige Code entfernt Daten und Dateien nur des Zielnutzers; ein zweiter Nutzer bleibt
  vollstaendig unveraendert.

## Datenschutz: interne Datenschutzakte angelegt (10.08.)

- Neue interne Arbeitsakte unter `work/Calrity_Main/docs/DATENSCHUTZAKTE.md`. Sie ist bewusst
  nicht oeffentlich und ersetzt keine anwaltliche Pruefung.
- Dokumentiert sind Produktabgrenzung, Verantwortlicher, Betroffenengruppen, Systemdatenfluss,
  Verarbeitungstaetigkeiten, Datenkategorien, Dienstleister, Fristen, Loeschkonzept,
  Betroffenenrechte, bestehende und offene TOMs, Datenpannenprozess, DSFA-Vorpruefung,
  Anwaltsfragen und eine priorisierte Arbeitsliste.
- Nur belegbare Ist-Funktionen sind als umgesetzt markiert. AV-Vertraege, Serverregionen,
  Providerfristen, Drittlandgrundlagen und juristische Bewertungen bleiben offen, bis dafuer
  Dokumente oder fachliche Freigaben vorliegen.
- Erste konkrete Luecken fuer die weitere Arbeit: OpenAI/Screenshot-Import fehlt im Webtext,
  BayLfD ist fuer das private Unternehmen voraussichtlich durch BayLDA zu ersetzen, die
  30-taegige Backup-Restfrist muss transparent werden und externe Google Fonts sollen lokal
  gehostet werden.
- Reine Dokumentationsaenderung: kein App-Upload, Git-Push oder Service-Neustart erforderlich.

## Datenschutz: Webtext-Korrektur lokal umgesetzt (10.08., noch nicht live)

- `rove-landing/datenschutz.html` wurde lokal auf die aktuelle App-Verarbeitung angepasst. Der
  zusaetzliche Patch `work/patches/datenschutz-app-2026-08-10.patch` bleibt nur als nachvollziehbare
  Arbeitskopie bestehen und darf nicht erneut interaktiv angewendet oder rueckwaerts ausgefuehrt
  werden.
- Der Patch dokumentiert den freiwilligen Screenshot-Import, die Uebermittlung an die OpenAI API,
  die fehlende lokale Bildspeicherung, die Bestaetigung vor Buchungsuebernahme und die von OpenAI
  veroeffentlichten Standardangaben zu Training und Missbrauchsprotokollen.
- Speicherfristen werden an die echte Technik angeglichen: Codes zehn Minuten, App-Sitzung bis
  180 Tage, State-/Webreport-Link 30 Tage, taegliche Backups maximal 30 Tage sowie sofortige
  Loeschung aus den aktiven Systemen. Kuendigung und Kontoloeschung bleiben getrennt.
- Die Aufsichtsbehoerde wird fuer das private bayerische Unternehmen von BayLfD auf BayLDA
  korrigiert. Die pauschale Zehn-Jahres-Aussage fuer steuerrelevante Daten wird nicht durch eine
  neue Fantasiefrist ersetzt, sondern auf die jeweils geltenden gesetzlichen Pflichten begrenzt.
- Gegen eine Kopie der echten HTML-Datei getestet: Patch passt ohne Versatz, Ergebnis ist als HTML
  lesbar, alle Pflichttexte sind enthalten und der Patch ist sauber reversibel.

## Datenschutz: Zukunftsfunktionen modular vorbereitet (10.08.)

- Die interne Datenschutzakte trennt ab jetzt verbindlich zwischen aktuell aktiven Datenfluesse und
  vorbereiteten Zukunftsmodulen. Oeffentliche Texte duerfen eine geplante Funktion nicht als bereits
  aktive Verarbeitung darstellen.
- Eigene Freigabemodule sind fuer Stripe/Abos, Bankanbindung, Broker/Krypto,
  Vertragskuendigungsservice, Rov.E Begleitung mit Dokumentenarchiv und native Apps angelegt.
- Jedes Modul enthaelt die vor Aktivierung zu klaerenden Daten, Anbieter, Rollen, Fristen,
  Sicherheitsmassnahmen, regulatorischen Fragen und erforderlichen Textaenderungen. Dadurch bleiben
  die Kerntexte stabil; beim spaeteren Ausbau wird nur das betroffene Modul konkretisiert und nach
  dokumentierter Technik- und Rechtsfreigabe aktiviert.

## App: Rechtliches direkt erreichbar (10.08., noch zu deployen)

- In den App-Einstellungen steht am Ende ein bewusst dezenter Bereich `Rechtliches` mit den drei
  Eintraegen `Datenschutz`, `AGB` und `Impressum`.
- Die Eintraege oeffnen die jeweiligen Seiten auf `getrove.de` separat. Dadurch bleibt die App mit
  dem aktuellen Finanzstand im Hintergrund erhalten.
- Es wurden ausschliesslich diese Verweise ergaenzt. Login, App-State, Buchungen und Finanzlogik
  bleiben unveraendert.

## Betrieb: VPS-Sicherheitscheck 1/3 begonnen (10.08.)

- Read-only-Ausgabe geprueft; sie enthielt keine Passwoerter, Tokens oder `.env`-Inhalte.
- Positiv: Ubuntu 24.04 LTS, automatische Updates aktiv, App API 5057 und Cockpit/API 5055 nur an
  localhost gebunden.
- Offen und relevant: UFW inaktiv, Root- und Passwort-SSH erlaubt, kein Fail2ban, `.env` und
  `clarity.db` mit Modus 644, ausstehende Updates und erforderlicher Neustart.
- Vor einer Firewall-Aenderung muessen die extern veroeffentlichten Docker-Ports 5678, 8000, 8888
  und 9443 sowie der Nginx-Port 5056 eindeutig ihren Diensten zugeordnet werden. Nichts davon blind
  schliessen, weil dort weitere Hostinger-/Docker-Dienste laufen koennen.
- Sichere Reihenfolge: Dienste identifizieren; SSH-Key und getrennten Admin testen; Dateirechte
  pruefen/haerten; Firewall und Fail2ban kontrolliert aktivieren; danach Updates mit Backup und
  Funktionstest.
- Zweite Read-only-Pruefung: Die offenen Docker-Ports gehoeren zu n8n (`5678`), Dozzle (`8888`) und
  Portainer (`8000`/`9443`). Port `5056` ist ein eigener Nginx-Serverblock vor dem lokalen
  Cockpit-Dienst `5055`; dessen Authentifizierung muss noch im vollstaendigen Block geprueft werden.
- `/root` steht sicher auf 700 und `authorized_keys` auf 600. `.env` und `clarity.db` stehen dennoch
  auf 644 und sollen auf 600 reduziert werden. Rov.E API und Bot laufen aktuell als Root; spaeter
  auf getrennte unprivilegierte Service-Nutzer umstellen.
- Der vorhandene Root-SSH-Key muss in einem zweiten Terminal explizit ohne Passwort getestet werden,
  bevor Root- oder Passwort-Login deaktiviert wird.
- Dateirechte am 10.08. erfolgreich gehaertet: `/root/clarity/.env` und `clarity.db` stehen jetzt
  auf 600; `rove-app-api` und `clarity-bot` blieben aktiv.
- Der erzwungene Public-Key-Test vom Mac scheiterte. Deshalb keinesfalls Passwort- oder Root-Login
  deaktivieren. Naechster Schritt ist ein neuer dedizierter Mac-Schluessel, dessen Anmeldung zuerst
  in einer zweiten Sitzung getestet wird.
- Nach Bereinigung der `authorized_keys` wurde der neue ED25519-Schluessel
  `rove-hostinger-2026` erfolgreich per Fingerabdruck abgeglichen. Der anschliessende Login mit
  `PasswordAuthentication=no` war erfolgreich. Als naechstes getrennten Admin-Nutzer anlegen und
  testen; erst danach Root-/Passwort-Login haerten.
- `roveadmin` wurde als getrennter sudo-Admin angelegt. Login mit dem neuen SSH-Schluessel sowie
  `sudo whoami` mit separatem Admin-Passwort wurden erfolgreich getestet. Der alte Root-Zugang ist
  weiterhin unveraendert als Rueckfallebene offen; SSH-Haertung folgt erst kontrolliert danach.
- Bedienregel fuer Furkan: Bei jeder kuenftigen Serveranweisung immer explizit dazuschreiben, ob sie
  ins normale Mac-Terminal oder nach dem Einstieg `ssh rove` in den SSH-Server gehoert. Lange
  Benutzer-/IP-/Schluesselbefehle nicht voraussetzen. Nach SSH-Haertung auch Deploy-Befehle auf den
  `roveadmin`-/sudo-Ablauf umstellen, statt weiterhin Root-Passwortbefehle zu nennen.
- Der lokale SSH-Kurzname `rove` ist eingerichtet und getestet. Furkan erreicht den Server kuenftig
  im normalen Mac-Terminal mit `ssh rove`; der Mac nutzt dabei `roveadmin`, den dedizierten
  ED25519-Schluessel und die Keychain. Fuer Admin-Befehle folgt auf dem Server weiterhin `sudo`.
- SSH-Haertung am 11.08. abgeschlossen: Die wirksame Konfiguration setzt `PermitRootLogin no`,
  `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`,
  `MaxAuthTries 3` und `LoginGraceTime 30`. `sshd -t` lief fehlerfrei, der Dienst blieb aktiv,
  ein frischer Login ueber `ssh rove` samt `sudo whoami` war erfolgreich und der direkte
  Root-Login wurde mit `Permission denied (publickey)` nachweislich abgewiesen.
- Direkte Root-/Passwort-Deploybefehle sind damit veraltet. Kuenftige Serverarbeiten beginnen im
  normalen Mac-Terminal mit `ssh rove`; privilegierte Schritte laufen anschliessend gezielt ueber
  `sudo`. Bestehende Deploy-Anweisungen muessen vor der naechsten Nutzung darauf angepasst werden.
- Oeffentliche Alt-/Admin-Dienste am 11.08. gehaertet: n8n, dessen PostgreSQL-Container, Dozzle und
  Portainer wurden nach einer rootgeschuetzten Konfigurationssicherung kontrolliert gestoppt und
  ihr automatischer Neustart deaktiviert. Es wurden keine Container oder Volumes geloescht.
- Der ungeschuetzte direkte Cockpit-Nginx-Zugang auf Port 5056 wurde deaktiviert; der separat
  geschuetzte Zugang ueber die Rov.E-Domain bleibt davon getrennt. Die zuvor oeffentlichen Ports
  5056, 5678, 8000, 8888 und 9443 lauschen nicht mehr.
- Nach der Aenderung sind extern nur noch SSH 22 sowie Nginx 80/443 offen. `rove-app-api`,
  `clarity-bot` und `nginx` sind aktiv; der oeffentliche App-API-Healthcheck antwortet mit
  `ok: true`. Naechster Sicherheitsblock: UFW kontrolliert aktivieren, danach Fail2ban.
- UFW am 11.08. kontrolliert aktiviert und fuer den Systemstart eingeschaltet. Eingehend sind
  ausschliesslich SSH 22, HTTP 80 und HTTPS 443 fuer IPv4 und IPv6 erlaubt; alte Regeln fuer 5056
  und 5678 wurden entfernt. Neue SSH-Anmeldung ueber `ssh rove` sowie beide Rov.E-Seiten wurden
  danach erfolgreich getestet. Naechster Schritt: Fail2ban fuer SSH.
- Fail2ban am 11.08. installiert, fuer den Systemstart aktiviert und mit einem eigenen `sshd`-Jail
  getestet. Die Regel erlaubt maximal fuenf Fehlversuche in zehn Minuten, sperrt danach zunaechst
  eine Stunde und verlaengert Wiederholungssperren. Dienst und Jail sind aktiv; beim Kontrolltest
  standen fehlgeschlagene und gesperrte IPs jeweils auf null.
- Backup-/Restore-Test am 11.08. bestanden: Der manuell gestartete automatische Backup-Job endete
  mit `Result=success` und `ExecMainStatus=0`; die neue 332-KB-Sicherung bestand
  `PRAGMA integrity_check` mit `ok`. Eine isolierte Wiederherstellung nach `/tmp` enthielt 215
  Ausgaben und 18 Report-Jobs, hatte keine Fremdschluessel-Fehler und wurde danach entfernt. Die
  Live-Datenbank wurde dabei weder ersetzt noch veraendert. Vor Betriebssystemupdates folgt noch
  ein VPS-Snapshot im Hostinger-hPanel.
- Vollstaendiger Hostinger-VPS-Snapshot am 11.08. um 09:07 erstellt; Wiederherstellung war laut
  hPanel bis 12.08. verfuegbar. Anschliessend wurden die Ubuntu-Updates unter Beibehaltung der
  vorhandenen Konfiguration installiert und der VPS kontrolliert neu gestartet.
- Neustart-Abnahme bestanden: Kernel `6.8.0-137-generic`, null fehlgeschlagene systemd-Dienste,
  `rove-app-api`, `clarity-bot`, `nginx`, `ssh` und `fail2ban` aktiv. UFW erlaubt weiterhin nur
  22/80/443 fuer IPv4 und IPv6; 5056/5678/8000/8888/9443 blieben geschlossen. Der oeffentliche
  Rov.E-App-API-Healthcheck antwortete nach dem Neustart erneut mit `ok: true`.
- Meilenstein `Rov.E VPS Security v1` damit erreicht. Spaetere Ausbaustufen bleiben: Dienste auf
  unprivilegierte Nutzer umstellen, verschluesseltes Offsite-Backup, Monitoring/Alarmierung und
  vor groesserem Launch eine erneute Sicherheitspruefung.
- Abschlusskontrolle: Kein weiterer Neustart erforderlich; taeglicher DB-Backup-Timer fuer 03:20
  UTC und Monats-Erinnerungstimer fuer 07:05 UTC sind eingeplant. n8n, dessen Datenbank, Dozzle und
  Portainer stehen dauerhaft auf `restart=no` und `exited`. `cloud-init` und `fwupd` werden von
  Ubuntu weiterhin als einzelne ausstehende Aktualisierungen angeboten; sie werden nicht gegen
  die Paketverwaltung erzwungen, da alle sicherheitsrelevanten Rov.E-Dienste stabil laufen.
- App-Core-Feinschliff am 11.08.: Schnell-Erfassung und Screenshot-Import sprachlich gekuerzt.
  Quick-Entry spricht nicht mehr im lockeren Bot-Slang, der Screenshot-Import vermeidet doppelte
  Erklaersaetze und zeigt vor dem Upload nur noch einen klaren leeren Zustand.
- Zweiter App-Core-Sprachpass am 11.08.: sichtbare Begriffe wie `Topf`, `Töpfe`, `Tippe` und
  `Schreib einfach` wurden in den Hauptscreens und Mentor-Antworten durch ruhigere Produkt-Sprache
  ersetzt: Ziele, Kategorien, Rahmen und klare Beispiel-Fragen.
- Ziel-Detailkarte am 11.08. ehrlicher und schlanker gemacht: Sehr grosse Ziele werden nicht mehr
  nur als `Langfrist-Ziel` versteckt, sondern zeigen die grobe Dauer auf Basis der hinterlegten
  Sparrate (`~ X Jahre`). In Vertragsdetails wurde der erklaerende Basisvertrag-Hinweis entfernt,
  weil Kuendigbarkeit bereits ueber Status/Schere sichtbar ist.
- Ziel-Detail am 11.08. weiter geglaettet: Zweckbindung auf einen kurzen Satz reduziert
  (`Nur zugeordnet — nicht zusätzlich gebucht.`) und Detailwerte rechtsbuendig gesetzt, damit lange
  Ziel-Dauern optisch wie die anderen Werte stehen.
- Mentor-Sprache am 11.08. weiter in Richtung ruhiger Finanzassistent geglaettet: Antworten zu
  Monatsstand, frei verfuegbarem Betrag, Score, Vertraegen, Vermoegen, Budgets, Ausgaben,
  Sparrate/ETF und Fallback sind kuerzer, sachlicher und weniger bot-/chatty-formuliert. Leitbild:
  klare Lageeinschaetzung zuerst, dann knappe Einordnung.
- Assistenten-Feinschliff am 11.08.: Der unklare Vorschlags-Chip `Was ist heute noch drin?` wurde
  durch `Wie viel kann ich heute noch ausgeben?` ersetzt, damit die Frei-Budget-Logik sicher greift.
  Im Chat steht Rov.E nun sichtbar als `Finanzassistent`; der Fallback ist direkt:
  `Ich habe das nicht verstanden. Bitte beschreibe deine Anfrage genauer.`
- Chat-Unterzeile am 11.08. nachgeschaerft: Statt technisch `schaut in deine Zahlen` steht nun
  `behält den Überblick für dich`, damit Rov.E weniger wie ein Datenleser und mehr wie ein ruhiger
  Finanzassistent wirkt.
- Tagesbudget-Fix am 11.08.: Die Chip-Frage `Wie viel kann ich heute noch ausgeben?` landet nun
  wieder explizit in der Tages-/Wochenbudget-Logik statt in der allgemeinen Frei-Antwort. Rov.E
  verteilt den verbleibenden Monatsrahmen auf die restlichen Tage und warnt klar, wenn der Monat
  bereits ueberzogen ist.
# App-only Registrierung fuer eingeladene Beta-Nutzer (13.08.2026)

- Neue Tester brauchen kein Telegram mehr. Die geschlossene Beta bleibt trotzdem kontrolliert:
  Eine E-Mail-Adresse wird vorab mit `invite_app_user.py` fuer standardmaessig 14 Tage eingeladen.
- `Jetzt starten` fuehrt nun ueber E-Mail und einen sechsstelligen Brevo-Code. Erst nach erfolgreicher
  Verifizierung entsteht ein zentrales Konto mit eigener, JavaScript-sicherer App-Nutzer-ID.
- Der bekannte Onboarding-Wizard bleibt die einzige Dateneingabe. Beim Abschluss schreibt
  `POST /v1/onboarding` Name, Einkommen, Zahltag, Fixkosten, Giro/Tagesgeld/Bargeld,
  ETF/Krypto-Startwerte, Sparraten, ETF-Plan, Immobilie und Ziel atomar in dieselbe produktive
  `clarity.db`, die App, Score, Push, Backups und Reports bereits verwenden.
- Ein abgebrochenes Onboarding bleibt nach erneutem E-Mail-Login offen. Ein bereits abgeschlossenes
  Onboarding kann nicht ein zweites Mal gesendet und dadurch doppelt gebucht werden.
- App-only Nutzer tragen in `user_access` den Status `app_only`. Der Report-Job nimmt diesen Status
  explizit mit, der weiterlaufende Telegram-Bot dagegen nicht. PDF/Web-Report werden erzeugt und im
  App-Archiv abgelegt; die Benachrichtigung kommt per App-Push, nicht ueber eine erfundene Telegram-ID.
- Bestehende Telegram-Beta-Nutzer bleiben unveraendert: Beim allerersten E-Mail-Login brauchen sie
  weiterhin den `/app`-Code; danach reicht auch fuer sie die E-Mail.
- Getestet gegen eine isolierte Kopie der Datenbank: Einladung, Code-Verifizierung, Kontoanlage,
  kompletter Onboarding-Write, State-Reload, erneuter E-Mail-Login, Doppelabschluss-Sperre sowie
  Einkommen/Fixkosten/Sparrate/Konten/Ziel/ETF-Plan waren konsistent.
- Erstnutzer-Feinschliff am 13.08.: Die Registrierung verweist bei einem bestehenden Konto nicht
  mehr auf einen veralteten `Beta-Login`, sondern klar auf die normale Anmeldung. Ein leerer
  Vertragsbereich zeigt nun, warum Fixkosten dort erfasst werden, statt unter dem Hinzufuegen-Button
  einfach leer zu bleiben. Der Onboarding-Abschluss bestaetigt ruhig die Speicherung und den
  finanziellen Ueberblick, ohne die unklare Formulierung `mit zwei Fingern aktuell`.
- Kritische Erstnutzer-Luecke am 13.08. geschlossen: Die zweistufige Mentor-Fuehrung nach dem
  Onboarding galt zuvor nur fuer lokale Profile und damit nicht sicher fuer die neuen zentralen
  App-only Konten. Sie greift nun fuer alle geladenen echten Konten: zuerst eine antippbare erste
  Konsumbuchung, danach einmalig der Budget-Einstieg. Automatisch erzeugte Fixkosten gelten dabei
  bewusst nicht als erste aktive Buchung.

# Admin-Kontrollzentrum und Unternehmens-Cockpit (Zielbild, 13.08.2026)

- Furkans persoenliches Rov.E-Konto soll spaeter serverseitig die Rolle `admin` tragen. Nur diese
  Rolle darf einen nicht oeffentlich verlinkten Admin-Bereich laden. Verstecken im Frontend allein
  ist keine Zugriffskontrolle; jeder Admin-Endpunkt muss die Rolle auf dem Server pruefen und
  sicherheitsrelevante Aktionen protokollieren.
- Die Admin-Ansicht in der Rov.E-App ist das mobile Kontrollzentrum, kein komplettes Backoffice:
  Systemzustand, neue kritische Ereignisse, offene Aufgaben, fehlgeschlagene Reports/Backups,
  dringende Supportfaelle und wichtige Kennzahlen. Kritisch bedeutet z. B. API oder Website nicht
  erreichbar, Datenbank-/Backupfehler, wiederholt fehlgeschlagener Report oder ungewoehnliche
  Fehlerhaeufung. Normale Erfolge erzeugen keinen Push.
- Ein vollstaendiger VPS-Ausfall kann nicht verlaesslich durch denselben VPS gemeldet werden.
  Deshalb muss ein externer Uptime-Waechter die oeffentlichen Healthchecks pruefen und einen
  unabhaengigen Alarmkanal ausloesen. Der Admin-Bereich sammelt und zeigt den Vorfall anschliessend.
- Das Web-Cockpit wird spaeter zur operativen Unternehmenszentrale mit getrennten Bereichen:
  Uebersicht/System, Nutzer und Status (Test, aktiv, zahlend, pausiert, gekuendigt, geloescht),
  Abrechnung/Zahlungen, Support und Beschwerden, Feedback/Kommentare/Wuensche, Reports und Jobs,
  Datenschutzanfragen/Loeschungen sowie ein nachvollziehbares Ereignisprotokoll.
- Datenmodell spaeter ereignisbasiert statt nur lose Benachrichtigungen: zentrale Admin-Ereignisse
  mit Typ, Prioritaet, Nutzerbezug, Status, Zeitstempel, Verantwortlichem und Erledigt-Zeitpunkt.
  Dadurch koennen App und Cockpit dieselbe Wahrheit anzeigen, ohne doppelte Sonderlogik.
- Umsetzung bewusst stufenweise: Monitoring v1 und kritische Admin-Meldungen zuerst; Nutzerstatus
  und Reportkontrolle waehrend der Beta; Abrechnung erst mit Stripe; Support-/Feedback-Workflow vor
  der groesseren oeffentlichen Beta. Kein App- oder Server-Upload fuer diese reine Produktentscheidung.
- Admin-Grundgeruest am 13.08. umgesetzt: `ROVE_ADMIN_USER_IDS` ist die einzige Rollenquelle.
  Nur ein dort freigegebener, gleichzeitig per App-Token und E-Mail-Session authentifizierter Nutzer
  darf `GET /v1/admin/overview` laden; ein isolierter Zugriffstest ergab Admin `200`, normaler Nutzer
  `403`. Die App rendert den Profil-Eintrag `Admin` ebenfalls nur nach der serverseitig gelieferten
  Rolle. Das mobile Kontrollzentrum zeigt aktuell API/DB/Backup/Speicher, App-Konten, neue Konten,
  aktive Zugaenge, Push-Geraete, Buchungsaktivitaet und Reportstatus. Kritische Datenbank-, Backup-,
  Report- und Speicherprobleme werden hervorgehoben. Externe Ausfallalarmierung ist noch offen.
- Beta-Zugangsverwaltung am 13.08. direkt in das Kontrollzentrum integriert. Der Admin kann dort
  eine E-Mail fuer 14 Tage zur App-only Registrierung einladen, offene Einladungen zurueckziehen
  sowie vorhandene Telegram- und App-Zugaenge freigeben oder sperren. Das ersetzt fuer die Beta
  `invite_app_user.py`, `/pending`, `/approve` und `/revoke` im normalen Alltag, verwendet intern
  aber weiterhin dieselben Tabellen `app_invitations` und `user_access`. Jede Admin-Aktion landet
  nachvollziehbar in `app_admin_events`. Eine Sperre ist bewusst keine reine Anzeige: bestehende
  App-Sessions und State-Links werden widerrufen, Push-Abos entfernt und alle Token-Endpunkte
  lehnen den gesperrten Zugang ab. Das eigene Admin-Konto kann nicht gesperrt werden. Kritische
  Aktionen verlangen einen zweiten bewussten Tipp. Getestet wurden Einladung, Widerruf, Sperre,
  sofortiger Zugriffsentzug, erneute Freigabe und Ereignisprotokoll gegen eine DB-Kopie.
- Mentoring-Angebote am 13.08. visuell und sprachlich neu hierarchisiert. Finanz-Reset und Rov.E
  Begleitung erscheinen weiterhin als serioese persoenliche Beratung, jetzt aber mit warmer
  Goldkante, sichtbarem Preis, klarem Ergebnisversprechen und eigener Handlungsflaeche. Keine
  erfundene Beliebtheit oder kuenstliche Verknappung; bei der Begleitung bleibt nur die reale
  Begrenzung auf zehn Plaetze. Die Detail-CTAs enden nicht mehr in einem Platzhalter-Toast,
  sondern oeffnen eine vorbereitete unverbindliche E-Mail an `info@getrove.de`. Stripe und eine
  Zahlung innerhalb der App bleiben bewusst Zukunft. Dekorative Kreise in den Karten wurden
  entfernt; nur die goldenen Handlungsflaechen atmen sehr dezent und bleiben bei reduzierter
  Bewegung vollstaendig statisch. Die E-Mail-Vorlage spricht neutral das `Rov.E-Team` an statt
  eine einzelne Person zu nennen, damit der Kontakt auch bei wachsendem Team professionell bleibt.
  Die Karten selbst nennen jetzt ohne Wiederholungen zuerst die konkrete Leistung und danach das
  Ergebnis; beide fuehren ueber den neutralen CTA `Details ansehen` in die vollstaendige Leistungsliste.
- Leeway-Aufloesung am 13.08. nach echtem Providertest erweitert: Ein einfacher EUR-Ticker wird
  zuerst auf Xetra und bei einem echten `market_symbol_not_found` danach auf Frankfurt (`.F`)
  geprueft. Explizite Handelsplaetze werden nie ueberschrieben; Auth-, Limit-, Netzwerk- und
  Waehrungsfehler loesen keinen zweiten Request aus. Damit wird z. B. `U9R` automatisch korrekt als
  `U9R.F` erkannt, waehrend `AUM5` weiterhin ueber `AUM5.XETRA` laeuft.
- Die taegliche Marktwert-Aktualisierung ist am 13.08. aus dem Telegram-Scheduler herausgeloest.
  `refresh_market_positions.py` wird ueber `rove-market-refresh.timer` taeglich um 22:30 Uhr
  `Europe/Berlin` ausgefuehrt und nutzt weiterhin dieselbe produktive `clarity.db`. Der Timer ist
  persistent und holt einen waehrend eines Serverausfalls verpassten Lauf nach. Einzelne falsche
  Ticker werden protokolliert, blockieren aber keine anderen Positionen; ein vollstaendiger Ausfall
  aller vorhandenen Positionen markiert den systemd-Lauf als fehlgeschlagen. Der alte APScheduler-
  Termin wurde aus `bot.py` entfernt, damit waehrend der Bot-Uebergangsphase keine Doppelbewertung
  erfolgt. Fuer die spaetere komplette Bot-Abschaltung muessen die uebrigen Report-/Cleanup-Jobs
  separat auditiert und ebenfalls entkoppelt werden.
- Bot-Abschalt-Audit am 13.08. abgeschlossen. Noch botgebunden sind: Erzeugung der monatlichen
  Report-Queue am 1. um 07:55 Uhr, der Queue-Worker, das Safety-Net am 1./2., Webreport-Cleanup
  um 03:10 Uhr und PDF-Komprimierung um 03:20 Uhr. Diese fuenf Aufgaben sind App-Infrastruktur
  und muessen vor dem Stoppen von `clarity-bot` in einen eigenen Report-Worker mit systemd-Timern.
  Der Abend-Recap um 20:30 Uhr ist dagegen reine Telegram-Kommunikation und darf mit dem Bot
  verschwinden. Zweite gefundene Leitplanke: `report_engine.send_report_to_user()` behandelt
  derzeit nur `app_accounts.source == 'app'` als App-only. Vor der Migration muss jedes bestehende
  verifizierte `app_accounts`-Konto als App-Zustellung gelten, damit migrierte Beta-Nutzer weder
  Telegram voraussetzen noch doppelte Reports bekommen. Report-PDF, Webreport, App-Archiv und
  interner Push sind bereits technisch vorhanden und koennen vom neuen Worker weitergenutzt werden.
- Der App-native Report-Block wurde danach umgesetzt: `rove_report_worker.py` uebernimmt Queue-
  Erzeugung, Verarbeitung mit Retry und Crash-Recovery sowie Web/PDF-Archivpflege. Drei eigene
  systemd-Timer steuern Monatserzeugung (1. um 07:55 plus Safety-Run am 2. um 08:05), Verarbeitung
  im Minutentakt und taegliche Pflege um 03:10 Uhr. `bot.py` plant nur noch den Telegram-Abend-
  Recap; Report-Aufgaben werden dort nicht mehr gestartet. `report_engine.py` erkennt jedes
  verifizierte App-Konto als App-Zustellung, auch wenn es urspruenglich aus Telegram migriert wurde.
  Ein Worker ohne Telegram-Client ueberspringt bewusst Nutzer ohne verifiziertes App-Konto. Damit
  bleiben PDF, Webreport, App-Archiv und Push erhalten, waehrend die Report-Infrastruktur nicht
  mehr vom laufenden Telegram-Polling abhaengt.
- Fuer die kontrollierte Telegram-Migration gibt es jetzt den Admin-Befehl
  `/announce_app_migration`. Ohne Zusatz zeigt er zuerst Empfaenger und Nachricht als Vorschau;
  erst `/announce_app_migration send` versendet. Die Zielgruppe wird direkt aus der gemeinsamen
  Datenbank bestimmt: nur freigegebene Nutzer mit abgeschlossenem Onboarding, die noch kein
  verifiziertes App-Konto besitzen. Bereits migrierte, gesperrte oder unfertige Nutzer werden nicht
  angeschrieben. Die Nachricht fuehrt ueber `/app` in den bestehenden E-Mail-Login, waehrend der
  Bot in der kurzen Uebergangsphase weiterhin funktioniert. Damit ist die Nutzerkommunikation
  vorbereitet, ohne Telegram vorzeitig abzuschalten oder bestehende App-Nutzer zu stoeren.
- Routing-Nachtrag: `announce_app_migration` ist auch explizit im Telegram-Command-Handler
  registriert. Ohne diesen Eintrag fiel der Text trotz vorhandener Admin-Logik in den allgemeinen
  Finanz-Handler und wurde als themenfremde Anfrage beantwortet.
- Der Routing-Schutz ist jetzt doppelt: Befehle werden inklusive optionalem Telegram-Suffix
  `@Botname` normalisiert, und auch der allgemeine Catch-all prueft Admin-Kommandos vor jeder
  Finanz-/KI-Logik. Damit kann die Migrationsvorschau nicht mehr als normale Geldfrage beantwortet
  werden, selbst wenn ein Telegram-Client den neuen Befehl noch nicht als Command klassifiziert.
- Fuer den praktischen Einsatz heisst der Migrationsbefehl kurz `/appwechsel`; der lange technische
  Name bleibt nur als kompatibler Alias erhalten. Vorschau und Versand laufen entsprechend ueber
  `/appwechsel` beziehungsweise `/appwechsel send`.
- Informationsarchitektur am 13.08. nachgezogen: Der Profilpunkt heisst nun `Mentoring/Zugang`
  und zeigt den aktiven Rov.E Zugang (6,99 EUR als spaeterer Preisanker) vor Finanz-Reset und
  Begleitung. Dadurch sind Grundprodukt und persoenliche Zusatzangebote gemeinsam auffindbar,
  waehrend `Rov.E Zugang` zusaetzlich in den Einstellungen bleibt, weil Kunden dort spaeter Abo,
  Rechnungen und Kuendigung erwarten. Der Beta-Hinweis wurde auf die klare Zusage gekuerzt:
  aktuell keine Gebuehren und kein Abo ohne ausdrueckliche Zustimmung.
- Vermoegensreihenfolge am 14.08. zentral ergaenzt. App-Nutzer koennen unter
  `Einstellungen > Konten verwalten` die sichtbaren Konten und Vermoegensarten per Touch-Griff
  oder Hoch-/Runter-Tasten sortieren. Gespeichert wird ausschliesslich eine stabile Key-Liste in
  `app_asset_order`; Kontostaende, Buchungen, Reports und Bot-Daten bleiben unberuehrt. `/v1/state`
  liefert die Praeferenz an jedes angemeldete Geraet. Ohne gespeicherten Eintrag gilt weiterhin
  exakt die bisherige Standardreihenfolge; neue Vermoegensarten werden am Ende ergaenzt. Die
  Nutzerreihenfolge gilt fuer die Startseiten-/Vermoegensliste und Cash-Umbuchungsziele, nicht fuer
  analytisch sortierte Reports oder einzelne ETF-/Aktienpositionen. Python-Syntax, JavaScript-
  Syntax, API-Authentifizierung, ungueltige Eingaben, Ein-Konto-/Mehrkonto-Faelle, Reload-Simulation,
  neue und entfernte Positionen sowie Alt-Nutzer ohne Praeferenz wurden lokal getestet. Der letzte
  visuelle Touch-Test erfolgt nach dem statischen Upload in der installierten iPhone-PWA.
- Cashflow-Suche am 14.08. vervollstaendigt. Die bereits vorhandene kompakte Suchleiste mit
  Lupen-Icon filtert weiterhin direkt im jeweils ausgewaehlten Monat und durchsucht jetzt neben
  Haendler und Kategorie auch die serverseitige Beschreibung sowie Betraege in Schreibweisen wie
  `42,99`, `42.99` und `1.234,56`. Gross-/Kleinschreibung, Teilbegriffe und deutsche Umlaute sind
  tolerant; ein leeres Feld zeigt sofort wieder alle Buchungen. App-, Bot- und Screenshot-Ausgaben
  bleiben in derselben `expenses`-Tabelle und werden nur ueber das neue unsichtbare Feld `desc` im
  bestehenden `/v1/state` auffindbar. Kein neuer Endpunkt, keine Migration und keine Aenderung an
  Buchungs-, Konto-, Report-, Score- oder Importlogik. Getestet: vollstaendiger/teilweiser Haendler,
  Gross-/Kleinschreibung, Kategorie, Beschreibung, drei Betragsformate, keine Treffer, Zuruecksetzen,
  wenige Eintraege und 10.000 Eintraege (ca. 50 ms reine Filterlogik). Die Suche bleibt bewusst auf
  den geladenen aktuellen bzw. ausgewaehlten historischen Monat begrenzt; eine serverweite
  Archivsuche wird erst bei einem spaeteren vollstaendigen Langzeitarchiv relevant. Offener separater
  UI-Nachtrag: Icons im neuen Konten-Sortiermodus noch visuell vereinheitlichen.
- Suchleiste danach bewusst aus der dauerhaften Cashflow-Hierarchie genommen. Oben rechts neben
  der Cashflow-Ueberschrift sitzt jetzt nur eine kompakte, eindeutig beschriftete Lupe; ein Tipp klappt das
  bestehende Feld darunter auf und fokussiert es. Das kleine `x` leert nur den Begriff und laesst
  die Suche offen, ein erneuter Tipp auf die Lupe schliesst und leert sie vollstaendig. Dadurch kann
  kein unsichtbarer Filter aktiv bleiben. In leeren Monaten und im Budget-Reiter ist die Lupe nicht
  sichtbar. Monatswechsel, Ergebnisdarstellung und Suchlogik bleiben unveraendert. Die Monatsnavigation
  nutzt wieder exakt ihre vorherige zentrierte Geometrie und ist vollstaendig von der Lupe entkoppelt;
  reduzierte Bewegung deaktiviert die kurze Aufklappanimation.
- Erster Cashflow-Analysebereich am 14.08. umgesetzt. Der dezente Chip `Analyse` oeffnet die eigene
  mobile Ansicht `Wohin fliesst dein Geld?`. Sie verwendet ausschliesslich die bereits von `/v1/state`
  geladenen Buchungen aus `DATA.tx` beziehungsweise `DATA.txHistory`, berechnet Summen live und
  speichert keine parallelen Analysewerte. Einnahmen und interne Transfers bleiben draussen; sichtbare
  echte Geldabfluesse inklusive Fixkosten werden nach ihren vorhandenen Rov.E-Kategorien aggregiert.
  Der SVG-Donut zeigt den Gesamtbetrag, laesst Kategorien antippen und hebt die passende sortierte
  Listenzeile mit Betrag und Anteil hervor. Monatswechsel verwendet unveraendert die vorhandene
  Drei-Monats-Historie. Ein Multi-Kontofilter reagiert sofort und bietet nur tatsaechlich in den
  geladenen Ausgaben vorkommende Zahlungskonten an. Dafuer liefert `_build_tx()` additiv `account`:
  bekannte Barzahlungen als `bargeld`, alle uebrigen Ausgaben und Fixkosten als `giro`. Historische
  Bot-Buchungen hatten keine Kontospalte und bleiben deshalb abwaertskompatibel dem Giro zugeordnet.
  Keine DB-Migration, kein neuer Endpunkt und keine Aenderung an Kontostaenden, Budgets, Reports,
  Score, Suche oder Buchungslogik. Geprueft wurden Python-/JavaScript-Syntax, Giro/Bargeld-Zuordnung,
  Einnahmen-/Transfer-Ausschluss, viele/wenige/keine Buchungen, mehrere/eine Kategorie, Kontofilter,
  Monatswechsel, neue Buchung und 10.000 Eintraege (ca. 81 ms Aggregation). Dark-/Light- und kleine
  Mobile-Breakpoints sind im CSS enthalten; der finale visuelle iPhone-PWA-Test erfolgt nach Upload.
- Analyse-Visual nach dem ersten echten iPhone-Test korrigiert: Safari platzierte die farbigen
  `stroke-dasharray`-Kreise wegen der SVG-Rotation als abgeschnittenen Halbkreis am Kartenboden,
  waehrend oben nur der graue Track sichtbar blieb. Die Segmente werden deshalb jetzt als echte,
  geometrisch berechnete SVG-Boegen ohne Rotation gezeichnet; eine einzelne Kategorie nutzt einen
  normalen Vollkreis. Die kuenstlichen HUD-Ecklinien wurden entfernt, Kartenradien und Glasflaechen
  beruhigt und die vorhandenen Rov.E-Kategorien fuer die Analyse mit einer klareren, konsistenten
  Farbpalette versehen. Finanzsummen, Kontofilter und Ausschlusslogik bleiben unveraendert.
- Zweiter visueller iPhone-Pass am 14.08.: Runde SVG-Linienenden liessen besonders kleine
  Kategorien wie aufgemalte, sich ueberdeckende Farbpunkte wirken. Der Donut nutzt deshalb nun
  praezise gerade Segmentkanten mit schmalen Zwischenraeumen, einen deutlich schlankeren Ring und
  eine ruhigere Palette mit Rov.E-Blau fuer Fixkosten. Karte, Innenflaeche und Schatten wurden
  ebenfalls reduziert, damit die Analyse naeher an einer klaren Finanzvisualisierung und nicht an
  einem dekorativen HUD wirkt. Ausschliesslich CSS und SVG-Darstellung wurden angepasst; Summen,
  Kategorien, Kontofilter und Auswahlverhalten bleiben unveraendert.
- Cashflow-Analyse am 14.08. strukturell zu einer kompakten V1 ausgebaut. Monat und Konten bleiben
  genau eine gemeinsame Filterebene; darunter wechseln die Analysearten `Uebersicht`, `Kategorien`
  und `Haendler`, ohne Filterzustand zu verlieren. Die Uebersicht behaelt den einen zentralen Donut,
  zeigt aber nur noch Top 3 Kategorien und Top 3 Haendler mit direkten Spruengen in die vollstaendigen
  Listen. Kategorien und Haendler werden nach Betrag sortiert und neutral mit absoluter sowie
  prozentualer Veraenderung zum Vormonat angezeigt. 0 EUR im Vormonat ergibt `Neu diesen Monat`,
  identische Werte `unveraendert`, fehlende Historie keine Fantasie-Prozentzahl. Beim laufenden Monat
  gilt derselbe Kalendertag als Stichtag fuer den Vergleichsmonat. Dafuer liefert `_build_tx()` additiv
  das maschinenlesbare Buchungsdatum und `txHistory` einen vierten, in der normalen Cashflow-Navigation
  weiterhin unsichtbaren Vergleichsmonat; keine Migration und kein neuer Endpunkt. Die bestehenden
  Konto-, Bargeld-, Budget-, Report- und Buchungswege bleiben unveraendert. Bekannte Markenvarianten
  werden konservativ ueber die bereits vorhandene Domain-/Logo-Liste gruppiert (z. B. drei Lidl-
  Schreibweisen); unbekannte Namen nur bei gleicher normalisierter Schreibweise. Die generische
  Monatscheck-Zeile `Fixkosten` erscheint nicht als erfundener Haendler, konkrete Empfaenger bleiben
  sichtbar. Spaetere Kategorie-/Haendler-Deep-Dives sind ueber stabile Data-Keys vorbereitet, aber
  bewusst noch nicht gebaut. Geprueft: Python-/JS-Syntax, mehrere/eine/keine Kategorie, Top-Haendler,
  Tab- und Filterzustand, Giro/Bargeld, hoeher/niedriger/gleich/neu/ohne Vergleich, vierter Monat und
  aehnliche Lidl-Namen. Offene ehrliche Grenze: unbekannte Filial-/Standortzusatznamen werden nicht
  aggressiv zusammengefuehrt, bis dafuer eine belastbare Haendlerquelle existiert.
- Visueller Produktpass fuer die Cashflow-Analyse am 14.08.: Filter, Tabs, Analyseflaeche und
  Ergebnislisten verwenden jetzt die ruhige Rov.E-Glasoptik mit klarerer Typografie und weniger
  technischer Backend-Anmutung. Top-Kategorien und die vollstaendige Kategorienliste erhalten
  einheitliche, farbcodierte Linien-Icons statt Rangnummern oder einfacher Farbbalken. Die
  vollstaendige Haendlerliste zeigt wie die Vorschau vorhandene echte Markenlogos; unbekannte
  Haendler behalten den bestehenden Buchstaben-Fallback. Betraege sind primaer, Anteile und
  Buchungsanzahl sekundaer, Vormonatsveraenderungen kompakte Status-Chips. Es wurden ausschliesslich
  CSS und bestehende HTML-Templates angepasst; Aggregation, Summen, Kontofilter, Vergleichslogik,
  Buchungen und Backend bleiben unveraendert. JavaScript-Syntax, ungenutzte Altklassen,
  Dark-/Light-Kontraste im Stylesystem und die erzeugten Kategorie-/Haendlerstrukturen wurden lokal
  geprueft; der abschliessende visuelle Test erfolgt nach dem statischen Upload in der iPhone-PWA.
- Rov.E-Glaschips danach appweit auf die vorhandenen interaktiven Auswahlkomponenten uebertragen:
  Cashflow-Filter, Schnellwerte, Kategorieauswahl, Onboarding-Auswahl, Analyse-Konten und segmentierte
  Umschalter verwenden dieselbe ruhige Glasflaeche, helle Schrift und eine klar erkennbare, aber nicht
  aggressive aktive Stufe. Fachliche Status-Badges fuer Warnung, Fehler oder Vertragsstatus bleiben
  bewusst semantisch gefaerbt. Im Analysebereich sind auch inaktive Tabs besser lesbar. Der
  Vormonatsvergleich zeigt nur noch Richtung und Prozentwert; sinkende Ausgaben werden nicht mehr
  blau als vermeintliche Aktion codiert. Bei fehlendem Vormonatswert steht neutral `Kein Wert im
  Vormonat` statt `Neu diesen Monat`. Light Mode besitzt eigene dunkle Grundschrift und weisse aktive
  Schrift. Keine Finanz-, Filter- oder Speicherlogik wurde veraendert.
- Vermoegensanalyse am 14.08. als zweiter, fachlich getrennter Analysezweig ergaenzt. Oberhalb der
  unveraenderten Cashflow-Filter trennt `Cashflow | Vermoegen` jetzt klar Monatsausgaben von der
  aktuellen Vermoegensstruktur. `Wo steckt dein Vermoegen?` verdichtet ausschliesslich die bereits
  final zusammengefuehrten `DATA.assets`: Giro und Bargeld werden als Cash gebuendelt, Tagesgeld
  bleibt separat, `portfolio_holdings` werden anhand des bestehenden `assetType` in ETFs und Aktien
  getrennt, Krypto bleibt eine eigene Klasse, Immobilien verwenden unveraendert das schon berechnete
  Eigenkapital aus Marktwert minus Restschuld, Sachwerte und nicht zugeordnete Investmentreste landen
  ehrlich unter Sonstige. Live getrackte ETF-/Aktienpositionen zeigen ihren vorhandenen Kursstand;
  nicht live bewertete Positionen werden als manuell gepflegt markiert und nie zu 0 EUR erfunden.
  Der zentrale SVG-Donut, die nach Wert sortierte Klassenliste und die konkreten Positionen nutzen
  keine Schatten-DB, keinen neuen Endpunkt und keine neue Finanzlogik. Sichtbare Prozentwerte werden
  per Restwertverteilung auf Zehntel so gerundet, dass sie zusammen exakt 100,0 Prozent ergeben.
  Negative bereits vorhandene Assetwerte werden weiterhin vom Gesamtvermoegen abgezogen, aber nicht
  als negativer Donutbogen gezeichnet; ein Hinweis erklaert diesen Sonderfall. Allgemeine Kredite
  bleiben unangetastet, weil Rov.E sie bisher nicht als Vermoegensschuld modelliert. Geprueft wurden
  JavaScript-Syntax, ein und mehrere Konten, Cash/Tagesgeld, ETF/Aktien-Split, Krypto, Immobilien-
  Eigenkapital, Sachwerte, keine Werte, eine 100-Prozent-Klasse, nicht zugeordnete Investments und
  negatives Immobilien-Eigenkapital. Backend, API, Datenbank, Reports, Kontostaende und Cashflow-
  Analyse wurden nicht geaendert; der visuelle iPhone-PWA-Test folgt nach dem statischen Upload.
- Konten-sortieren-UI am 14.08. visuell vereinheitlicht. Die Konten verwenden im Verwaltungsfenster
  jetzt exakt dieselben Rov.E-Vermoegens-SVGs, Glasrahmen, Tints und Strichstaerken wie auf der
  Startseite; zuvor fehlte der gemeinsame `.gicon`-Kontext, weshalb dieselben Roh-SVGs dort sichtbar
  anders und teilweise unsauber gerendert wurden. Die textbasierten Hoch-/Runter-Pfeile wurden durch
  ruhige SVG-Chevrons ersetzt, der Sechs-Punkt-Touchgriff verkleinert und die Zeilen als dezente
  Glasliste ausbalanciert. Dark und Light Mode besitzen passende Kontraste. Sortierreihenfolge,
  Drag-and-drop, Hoch-/Runter-Handler, API-Speicherung und Kontowerte wurden nicht veraendert.
- Dezente taegliche Tracking-Erinnerung am 14.08. vollstaendig an die bestehende Web-Push-Strecke
  angebunden; es gibt bewusst keine zweite Push-Infrastruktur. Die PWA speichert beim vorhandenen
  Geraete-Abo additiv die IANA-Zeitzone und eine abschaltbare Praeferenz in
  `app_push_preferences`. Bestehende Push-Nutzer starten aus Vorsicht mit ausgeschalteter
  Tracking-Erinnerung, neue Nutzer aktivieren sie zusammen mit ihrer ausdruecklichen Push-Freigabe
  und koennen sie unter Einstellungen jederzeit separat ausschalten. Der neue systemd-Timer prueft
  alle zehn Minuten, versendet aber nur waehrend der lokalen 20-Uhr-Stunde und nur, wenn an diesem
  lokalen Kalendertag kein Eintrag in der zentralen `expenses`-Tabelle existiert. Damit zaehlen App-,
  Bot- und Screenshot-Ausgaben gleich, Einnahmen, Umbuchungen und Kontostandsaenderungen hingegen
  nicht. `app_push_delivery_log` reserviert `tracking_reminder + lokales Datum` vor dem externen
  Versand und verhindert so Doppelmeldungen auch nach Neustarts oder parallelen Joblaeufen. Der
  bestehende Service Worker oeffnet beim Antippen direkt `#add`; sein bewusst cachefreier Aufbau
  bleibt erhalten. Die neue Praeferenz ist im Datenexport enthalten und wird von der bestehenden
  dynamischen Kontoloeschung mit entfernt. Geprueft: Python- und JavaScript-Syntax, API-Praeferenzen,
  bestehendes Subscribe/Unsubscribe, App-/Bot-/Screenshot-Ausgabe, nur Einnahme, fehlendes Abo,
  deaktivierter Reminder, Mehrfachlauf, persistente Idempotenz, Titel mit/ohne Namen, mehrere
  Zeitzonen, Berlin-Fallback, lokale Tagesgrenze und Deep-Link. Keine Finanz-, Report-, Score-,
  Screenshot-, Login- oder Bot-Logik wurde veraendert. Ehrliche Grenze: War der Server die komplette
  lokale 20-Uhr-Stunde offline, wird die Erinnerung nicht am spaeten Abend nachgeholt; das vermeidet
  unpassende Nacht-Pushs.
- Push-Einstellungen im selben Sprint visuell eindeutig gemacht: Der missverstaendliche Statussatz
  `An — wichtige Hinweise` und der Navigationspfeil wurden durch einen echten Hauptschalter ersetzt.
  Aktiv ist er gruen, inaktiv grau. Die `Taegliche Tracking-Erinnerung` bleibt darunter als eigener
  Schalter sichtbar, sobald Push grundsaetzlich erlaubt ist. Versand-, Abo- und Reminder-Logik
  wurden dabei nicht veraendert.
- Kategorie-Deep-Dive am 14.08. als letzter Baustein des aktuellen Analyse-Sprints ergaenzt. In der
  vollstaendigen Kategorienliste oeffnet ein Tipp auf eine Kategorie eine mobile Vollbild-Ansicht
  mit Kategorie, gewaehltem Monat, Gesamtsumme, Anteil an den gefilterten Gesamtausgaben und dem
  vorhandenen Vormonatsvergleich. Darunter werden ausschliesslich die Haendler und Buchungen dieser
  Kategorie angezeigt: Haendler nach Betrag, mit Anteil und Buchungsanzahl; Buchungen mit der
  neuesten zuerst. Die bereits vorhandene konservative Haendlernormalisierung wurde in einen
  gemeinsamen Helper gezogen und wird unveraendert von Hauptanalyse und Deep-Dive verwendet. Nicht
  belastbare Sammelnamen wie `Unbekannt`, `Sonstiges` oder der reine Kategoriename werden nur in
  diesem Detailbereich als Haendler ausgeblendet; die Buchungen bleiben sichtbar. Monat und
  Kontofilter stammen exakt aus der uebergeordneten Analyse, erhalten keine zweite Steuerung und
  bleiben beim Zurueckgehen bestehen. Das Fullscreen-Sheet nutzt bestehende Rov.E-Farben, Karten,
  Radien, Logos, Kategorie-Icons und Navigationsmuster. Keine API-, Backend- oder DB-Aenderung und
  keine neue Finanzlogik. Geprueft wurden Summen, Vormonat, ein und mehrere Haendler, gleiche
  Haendlernamen, fehlende brauchbare Haendlerdaten, Haendleranteile, Datumssortierung, Konto- und
  Monatsfilter sowie App-, Screenshot- und fruehere Bot-Buchungen; JavaScript-Syntax und Diff sind
  sauber. Der abschliessende visuelle Check auf iPhone/PWA und verschiedenen realen Bildschirmhoehen
  erfolgt nach dem statischen Frontend-Upload.
- App-only-Investment-Persistenz am 14.08. nach echtem Neunutzer-Feedback korrigiert. Ursache war
  nicht die zentrale Investment-Datenbank: Ein neuer Nutzer ohne bisherigen ETF erhielt in der UI
  eine lokale Platzhalter-Kachel mit `manual:true`. Dadurch lief `ETF & Investments` durch den alten
  lokalen Profilpfad, obwohl App-only-Sitzungen im Bridge-Modus ausschliesslich den Server als
  Wahrheit verwenden. `saveBridgeLocal()` schliesst zentrale ETF-/Krypto-Kacheln absichtlich aus;
  beim naechsten `/v1/state`-Refresh verschwand die nur lokal hinzugefuegte ETF-Position deshalb
  wieder. Im Bridge-Modus werden ETF- und Krypto-Platzhalter jetzt immer ueber `/v1/investments`
  gespeichert; der lokale Weg bleibt nur fuer echte lokale Sachwerte und den Profilmodus bestehen.
  Der isolierte CRUD-Test mit einer frischen App-only-Identitaet deckte zusaetzlich einen fehlenden
  `user_id`-SQL-Parameter bei der Aenderung eines bestehenden ETFs auf. Dieser Parameter ist
  korrigiert. Manuell von der App angelegte, nicht live getrackte ETFs koennen nun auch dauerhaft
  geloescht werden. Dabei wird nur der durch App-Ereignisse tatsaechlich zur Gesamtanlage addierte
  Nettobetrag abgezogen; ein lediglich benannter Alt-/Restbestand wird wieder unzugeordnet, nie
  vernichtet. Live getrackte ETFs und alte Bot-Historie bleiben vor diesem Loeschweg geschuetzt.
  Geprueft: frischer App-only-Nutzer, ETF/Aktie/Krypto anlegen, aendern, `/v1/state`, loeschen,
  unveraendertes Giro und Nettovermoegen sowie Schutz einer bestehenden Live-ETF-Position.
- Bank-Anbindung im selben Feedback-Sprint eindeutig als nicht aktiv markiert. Der Onboarding-Weg
  heisst nun `Bank-Anbindung`, erklaert direkt `In der aktuellen Beta nicht verfuegbar` und traegt
  ein kompaktes rotes `Noch nicht aktiv`-Badge. Ein Tipp startet weiterhin keinen Verbindungsflow,
  sondern zeigt nur den ehrlichen Info-State ohne Terminversprechen. Der leere Vermoegenszustand
  sagt nicht mehr `Konto verbinden`, sondern `Werte selbst eintragen`; sein Hinweis nennt ebenfalls
  klar, dass Bankverbindungen in der Beta nicht aktiv sind.
- Dynamische Mehrfachkonten am 14.08. bewusst noch nicht hinter einer UI vorgetaeuscht. Der Audit
  zeigt: `app_account_balances` besitzt derzeit genau einen Datensatz je `user_id + account_key`
  (`giro`, `tagesgeld`, `bargeld`), Buchungen kennen nur diese Typ-Schluessel statt einer konkreten
  Konto-ID, `app_asset_order` akzeptiert sieben feste Keys, Immobilien sind ein Einzel-Datensatz pro
  Nutzer und der Client fuehrt Assets beim Laden nach Anzeigename zusammen. Reports und Score nutzen
  dagegen bewusst nur die aggregierten Summen `current_cash` und `current_investments`. Eine sichere
  V2 braucht daher additive stabile Asset-/Konto-IDs, Namen und Typen, optionale Konto-IDs an neuen
  Bewegungen, dynamische Sortier-Keys sowie eine rueckwaertskompatible Aggregation in die bestehenden
  Summen. Erst danach duerfen leere Platzhalter verschwinden und mehrere Giro-/Tagesgeldkonten oder
  Immobilien angelegt werden. Diese Abhaengigkeit wurde gemaess Produktbriefing gemeldet; keine
  riskante Migration und keine Aenderung an Cashflow, Reports, Score oder Bestandsdaten vorgenommen.
### 14.08.2026 - Investment-Loeschen robust und sichtbar

- Manuelle, nicht kursverfolgte ETF-Positionen werden beim Loeschen nun ueber ihre stabile Holding-ID angesprochen; der Name bleibt nur der abwaertskompatible Fallback.
- Der API-Endpunkt prueft weiterhin strikt Nutzer-ID, `app_etf_`-Herkunft und deaktivierte Kursverfolgung. Fremde oder live getrackte Positionen bleiben geschuetzt.
- Der bisher unscheinbare Loeschtext ist ein roter Rov.E-Glas-Chip. Der erste Tipp schaltet sichtbar auf die rote Bestaetigung, erst der zweite Tipp loescht.
- Keine Aenderung an Kontostaenden, Sparplan-, Marktwert- oder Reportlogik.

### 15.08.2026 - Dynamische Mehrfachkonten Sprint 1

- Das unsichtbare Cash-Kontenfundament liegt lokal fertig und getestet vor. Neue Module:
  `rove_financial_accounts.py` und `migrate_financial_accounts.py`; vollständiger Bericht:
  `work/Calrity_Main/docs/DYNAMIC_ACCOUNTS_SPRINT1_2026-08-15.md`.
- Neue additive Tabellen: `app_financial_accounts`, `app_financial_account_roles` und
  `app_user_features`. Feature-Key `multi_cash_accounts_v1` bleibt fuer alle Nutzer aus.
- Migration ist standardmaessig read-only. Nur `--apply` erzeugt nach automatischem SQLite-Backup
  Tabellen und migriert jeden Nutzer in einer eigenen Transaktion. Drift, unbekannte Legacy-Keys
  oder ungueltige Rollen blockieren den einzelnen Nutzer ohne automatische Reparatur.
- Bestehende Geldpfade, UI, State, Cashflow, Monatsplan, Screenshot, ETF, Reports, Score und Bot
  wurden nicht umgeschaltet. `/v1/state` war auf einer echten lokalen DB-Kopie vor/nach Apply
  bytegleich; Apply zweimal erzeugte keine doppelten Konten oder Rollen.
- Datenexport enthaelt nach Schema-Apply die neuen Konten/Rollen; Kontoloeschung entfernt Konten,
  Rollen und Flags nutzergebunden. 9 isolierte Testfaelle, Integritaetscheck und Foreign-Key-Check
  sind gruen.
- Produktiver Abschluss am 15.08.2026: frischer Dry-Run `14 ready / 0 blocked`, danach zwei
  kontrollierte Apply-Laeufe. Ergebnis blieb bei exakt 14 Nutzern, 26 Financial Accounts und
  56 Rollen; alle nutzerweisen Finanzinvarianten waren in beiden Laeufen gruen.
- Zwei automatische SQLite-Backups wurden unter `backups/financial_accounts/` erzeugt. Der zweite
  Apply war idempotent: keine zusaetzlichen Konten/Rollen und keine Betrags- oder Flag-Aenderung.
- Produktive Abschlusschecks: keine doppelten Legacy-Konten, keine falschen Konto- oder
  Legacy-Summen, jeder Nutzer exakt vier Rollen, keine nutzerfremden Rollenzuordnungen,
  `integrity_check = ok`, `foreign_key_check = 0`, API aktiv und Healthcheck gruen.
- `multi_cash_accounts_v1` bleibt produktiv fuer alle Nutzer aus. Sprint 2, UI und aktive
  Geldpfade wurden ausdruecklich nicht gestartet bzw. nicht umgestellt.

### 20.08.2026 - Vermoegenspositionen einheitlich bearbeiten und entfernen

- Die im Girokonto sichtbaren technischen Rollen wurden als kuenftige Verwendung erklaert und
  sprachlich von `Standard` auf `Aktiv` / `Hier verwenden` beruhigt. Geldkonten werden beim
  Entfernen weiterhin archiviert, damit fruehere Buchungen nachvollziehbar bleiben.
- Sachwerte besitzen jetzt einen echten Bearbeiten-Modus mit dauerhaftem Speichern, sichtbarem
  rotem Positions-Loeschweg und einer separaten Aktion zum Entfernen des gesamten Sachwert-Bereichs.
- ETF- und Aktienpositionen koennen auch mit aktiver Kursverfolgung entfernt werden. Dabei werden
  Holding und Sparplan entfernt und der aktuelle Positionswert genau einmal aus dem aktiven
  Investment-Gesamtwert genommen; historische Marktbewegungen bleiben fuer Reports erhalten.
- Frontend-JavaScript und Python-Syntax sind geprueft. Der vollstaendige Flask-Testlauf erfolgt
  nach Git-Deploy im vorhandenen Server-Venv, weil lokal kein Flask-Paket installiert ist.
- Nachtest Krypto: Auch aus der frueheren Bot-Zeit stammende Coins lassen sich entfernen. Rov.E
  schreibt dafuer eine ausgleichende Gegenbewegung statt alte Ereignisse zu loeschen; der aktive
  Coin-Bestand und `current_investments` sinken korrekt, historische Reports bleiben erklaerbar.
- Der direkte Loesch-Chip an jeder Krypto-Position wurde wieder entfernt. Stattdessen besitzt die
  Krypto-Detailseite wie die anderen variablen Vermoegensbereiche eine zweistufige Aktion
  `Krypto entfernen`. Sie bucht alle aktiven Coins atomar auf null gegen, senkt
  `current_investments` um deren exakte Summe und entfernt danach die komplette Krypto-Kachel;
  einzelne Positionen bleiben weiterhin nur ueber ihren Bearbeitungsmodus loeschbar.

### 20.08.2026 - ETF-Sparrate stabil einer Position zugeordnet

- `investment_events.holding_id` verknuepft neue ETF-Sparplanzahlungen additiv und
  nutzergebunden mit der konkreten `portfolio_holdings`-Position. Der globale ETF-Plan bleibt
  nur Fallback, wenn die aktiven Positionsplaene nicht eindeutig zur Gesamtrate passen. Eine
  explizite, idempotente Schema-Migration erzeugt vor der ersten Aenderung ein SQLite-Backup.
- Live getrackte ETFs behalten echte Stueckzahl, Kurs und Marktwert unveraendert. Die Zahlung wird
  als offene Contribution derselben Holding angezeigt und nicht mehr global als Restbetrag.
  Manuelle ETF-Holdings erhoehen ihren bereits als aktuellen manuellen Wert verwendeten
  `total_invested`-Stand.
- Eine spaetere echte Stueckzahlerhoehung kann die offene Contribution gegen den neuen Marktwert
  aufloesen. Der taegliche reine Kursrefresh tut das nicht und erzeugt keine zweite Sparleistung.
- Ein separates, standardmaessig read-only laufendes Repair-Skript ordnet alte generische
  `app_etf_plan`-Events nur bei exakt einem belegbaren Live-ETF-Positionsplan zu. Mehrdeutige und
  manuelle Faelle bleiben unangetastet; produktiv wurde noch kein Repair ausgefuehrt.
- 49 Investment-/Mehrfachkonten-Regressionstests, Python-Kompilierung und Frontend-JavaScript-
  Syntaxcheck sind lokal gruen.

### 20.08.2026 - Mischportfolio-Regression nach ETF-Zuordnung geschlossen

- Die gemeinsame Investment-Schublade enthaelt ETFs und Aktien. Der neue Contribution-Helfer
  akzeptiert absichtlich nur ETFs, wurde im State-Aufbau aber auch fuer eine Aktie aufgerufen.
  Bei Nutzern mit z. B. S&P 500 und Under Armour brach dadurch `/v1/state` ab; die PWA blieb auf
  einem alten Zugangssnapshot mit alter ETF-Zahl, leerer Profilinitiale und alter Chartreihe.
- Der State liest Sparplan-Contributions jetzt ausschliesslich fuer Positionen vom Typ `etf`.
  Aktien bleiben unveraendert sichtbar und koennen den Live-State nicht mehr blockieren.
- Ein neuer Mischportfolio-Test belegt: 8.400 EUR ETF-Marktwert plus 300 EUR zugeordnete Sparrate
  werden als 8.700 EUR angezeigt, die Aktie bleibt separat unveraendert und der Investment-
  Gesamtwert wird nicht doppelt veraendert. Insgesamt sind 50 Regressionstests gruen.
- Eine exakt flache Chartreihe wird visuell mittig dargestellt statt am unteren Rand. Das aendert
  keine historischen Werte, sondern nur die Darstellung eines Zeitraums ohne Bewegung.
- Der produktive Altfall hatte noch keinen `app_etf_position_plans`-Datensatz, obwohl S&P 500,
  globaler 300-EUR-Plan und `portfolio_holdings.monthly_contribution` eindeutig uebereinstimmten.
  Das Repair erkennt diese Konstellation nun nur bei exakt einer passenden Live-ETF-Holding und
  ohne bereits aktive Positionsplaene. Beim Apply uebernimmt es Tag, Quellkonto, Modus und Startmonat
  in einen stabilen Holding-Plan und ordnet das alte Event zu; Cash, Stueckzahl, Marktwert und
  `current_investments` bleiben unveraendert. Mehrdeutige Faelle bleiben blockiert. 51 Tests gruen.

### 20.08.2026 - Finanzprofil statt Onboarding-Neustart

- Audit-Ergebnis: `users.onboarding_step >= 10` ist weiterhin die einzige harte Fertig-Markierung.
  Skip und Complete sind historisch nicht getrennt gespeichert; der Live-State liefert deshalb
  additiv `onboarding` mit `required`, `status`, `step` und `completed`, ohne alte Nutzer zu
  migrieren.
- `/v1/profile` kann nun neben Name, Zahltag, Sparrate und ETF-Plan auch das Basis-Einkommen
  (`income`, `other_income`) aktualisieren. Das schreibt nur die Planungswerte fuer kuenftige
  Monatsplaene, Coach und Reports; bestaetigte Income-Movements und historische Reports bleiben
  unveraendert.
- In den App-Einstellungen gibt es fuer gekoppelte Nutzer einen kleinen Bereich `Finanzprofil`
  mit Einkommen, Zahltag und nur bei unvollstaendiger Einrichtung `Einrichtung fortsetzen`.
  Vollstaendig abgeschlossene Nutzer bekommen keinen Button zum kompletten Onboarding-Neustart.
- Die Fortsetzung fuellt vorhandene Serverdaten vor. Der Onboarding-Endpunkt ist dafuer additiv
  abgesichert: bestehende Vertraege, Ziele, Immobilienwerte, Investment-Startwerte und ETF-Plan
  werden nicht mehr blind geloescht und neu angelegt.
- Checks: `py_compile rove_app_api.py rove_app_state.py`, extrahiertes Frontend-JavaScript mit
  `node --check` und 51 bestehende Regressionstests sind gruen.

### 20.08.2026 - Sachwerte-Loeschung bleibt nach Reload bestehen

- Ursache: `Sachwerte` sind in der App-Bruecke aktuell ein lokaler Geraetewert. Die Detail-Loeschung
  entfernte die sichtbare Kachel zwar aus `DATA.assets`, aber `ensureAssetPlaceholders()` erzeugte
  danach automatisch wieder eine leere Sachwerte-Kachel. Beim anschliessenden lokalen Speichern konnte
  dieser Platzhalter den geloeschten Zustand ueberleben bzw. nach einem Reload wieder sichtbar werden.
- Fix: `Sachwerte` wird nicht mehr automatisch als Standard-Platzhalter erzeugt. Der Nutzer kann die
  Kachel weiterhin bewusst ueber `Konto oder Wert hinzufuegen -> Sachwert` neu anlegen. Wenn die letzte
  Position oder der ganze Sachwerte-Bereich geloescht wird, bleibt dieser Zustand lokal gespeichert.
- Geldlogik bleibt unveraendert: Beim Entfernen wird der Wert aus `DATA.assets` genommen und
  `recalcNetWorth()` berechnet das Gesamtvermoegen neu. Historische Buchungen, Reports, Kontostand,
  Investments und Mehrfachkonten-Logik werden nicht veraendert.

### 20.08.2026 - Multi-Account Rollout-Readiness vorbereitet

- Fuer die Pilot-Abnahme wurde ein read-only Audit-Skript `audit_multi_account_rollout.py` ergaenzt.
  Es prueft Feature-Flag-Zustand, Pilot-Invarianten, Drift zwischen Financial Accounts und Legacy-
  Aggregaten, Account-/Movement-/ETF-/Investment-Referenzen, Asset-Sortierung, Kandidaten fuer zwei
  weitere Piloten sowie SQLite-Integritaet.
- Das Skript gibt bewusst keine echten Finanzwerte aus, sondern nur OK/Abweichung, Counts und
  konkrete technische Blocker. Es aktiviert keine Nutzer, erstellt keine Konten und veraendert die
  Datenbank nicht.
- Empfehlung aus dem Skript ist maximal `GO_FOR_TWO_MORE_PILOTS` oder `NO_GO`; ein globaler Rollout
  bleibt in diesem Sprint ausdruecklich ausgeschlossen.
- Checks lokal: Python-Kompilierung, Smoke-Test auf isolierter SQLite-Testdatenbank und 51 bestehende
  Mehrfachkonten-/ETF-Regressionstests sind gruen.
- Nach Produktionsaudit war der bestehende Pilot sauber, aber mehrere Flag-off-Nutzer hatten Drift:
  Der Legacy-Pfad war weitergelaufen, waehrend ihre in Sprint 1 angelegten Financial Accounts nicht
  mitgeschrieben wurden. Das ist fuer Flag-off-Nutzer unsichtbar/safe, aber ein NO-GO fuer globales
  Blind-Aktivieren.
- Fuer die restlichen aktiven Tester wurde `prepare_multi_account_active_testers.py` ergaenzt. Das
  Skript erkennt aktive App-Tester, blockt inaktive/gesperrte Nutzer, prueft Legacy als aktuelle
  Wahrheit, erzeugt vor jeder produktiven Nutzerumstellung ein SQLite-Backup, synchronisiert exakt
  Legacy -> Financial Accounts, validiert Rollen/Summen/State und aktiviert danach sofort nur diesen
  Nutzer. Bei der ersten Abweichung stoppt es. Globaler Rollout bleibt aus.
- Nach erstem Dry-Run wurde die aktive Zugangsliste von der echten Rollout-Kandidatenliste getrennt:
  aktive Zugänge ohne fertige Legacy-Kontostaende werden als `skipped` dokumentiert, aber nicht
  vorbereitet. Der State-Check nutzt nun direkt `build_live_app_data(conn, user_id)` und gibt den
  konkreten Fehler aus, statt pauschal `false` zu melden.
- Checks lokal: Python-Kompilierung und 51 bestehende Regressionstests sind gruen.

### 20.08.2026 - UI-Polish Sprint 2: Konto- und Vermoegensdetails

- Konto- und Vermoegens-Detailansichten verwenden jetzt eine gemeinsame, ruhigere Hierarchie:
  kompakter Header mit Icon, Name, Untertitel und Wert, danach klar getrennte Bereiche fuer
  Schnellaktionen, Positionen, Einstellungen und destruktive Aktionen.
- Konten zeigen Umbuchen und Kontostand-Anpassung zuerst; Name, Standards und Archivieren stehen
  gesammelt im unteren Einstellungsbereich. Die bisherigen IDs und Handler bleiben erhalten.
- ETF-/Aktien-/Krypto-Positionen haben eine einheitliche Positionsliste, einen klar getrennten
  Bearbeitungsbereich und eine eigene Sparplan-Zeile. Krypto kann weiterhin einzeln oder komplett
  entfernt werden; die bestehende Geldlogik bleibt unveraendert.
- Sachwerte und Immobilien verwenden dieselbe Detailstruktur. Immobilienbearbeitung und Entfernen
  sind jetzt als ruhige Einstellungs-/Gefahrenzone dargestellt.
- Keine API-, Datenbank-, Navigations-, Kontostands-, Buchungs-, Report- oder Feature-Flag-Aenderung.
  Nur `work/rove-app/index.html` wurde fuer diesen Sprint angepasst.
- Validierung: `git diff --check` und extrahierte Frontend-JavaScript-Syntaxpruefung mit `node --check`
  sind gruen. Ein Server-Deploy ist noch nicht ausgefuehrt.

### 20.08.2026 - UI-Polish Sprint 2.5: Investment- und Konto-Detail-Final-Polish

- Die Investment-Positionsliste nutzt jetzt progressive Disclosure: Instrumentname und Wert stehen
  im Vordergrund, Typ, Kursdaten, Trackingstatus und Sparplan bleiben als ruhige Meta-Zeile darunter.
- ETF, Aktie und Krypto verwenden dieselbe Positionsgrammatik; die fachlichen Unterschiede sowie
  bestehenden Bearbeitungs-, Sparplan- und Loeschaktionen bleiben erhalten.
- Die Investmentliste hat weniger Containerwirkung, mehr kontrollierten Weissraum und groessere,
  besser tappbare Positionszeilen. Die Aktion `Position hinzufuegen` bleibt bewusst sekundar.
- Das Konto-Detail verwendet fuer den seltenen Einstellungsbereich die ruhigere Bezeichnung `Konto`.
  Keine Aktion, Rolle, Berechnung, API, Datenbank, Navigation oder Finanzlogik wurde geaendert.
- Validierung: Alle eingebetteten Frontend-Skripte mit `node --check` sowie `git diff --check` sind gruen.

### 20.08.2026 - Home Mentor-Abstand korrigiert

- Der Mentor-Bereich ist auf dem Home-Screen leicht nach innen gesetzt und hat mehr Abstand
  zwischen Icon und Text. Dadurch beruehrt er die umgebende Asset-Kachel nicht mehr optisch.
- Keine Struktur-, Logik- oder Navigationsaenderung; Frontend-Syntax und Diff-Pruefung bleiben gruen.

### 20.08.2026 - UI-Polish Sprint 3.1: gemeinsames Icon-System

- Das bestehende Settings-/Profil-Iconmuster wurde als gemeinsame Rov.E-Sprache abgeleitet:
  klare Outline, kontrollierte Glasflaeche, dezente Kontur und reduzierter Glow.
- Neue wiederverwendbare Klassen `rov-icon`, `rov-icon--regular` und `rov-icon--compact` werden
  fuer Profil/Settings, Home-Assets, Ziele und Vertraege eingesetzt. Regular bleibt bei 40 px;
  Compact nutzt 36 px, damit kleine Karten nicht hoeher oder voller werden.
- Die vorhandenen fachlichen Icons, Klickziele, Chevrons und Aktionen bleiben unveraendert.
  Keine Kachel wurde strukturell vergroessert; Dark- und Light-Mode sind separat beruecksichtigt.
- Keine Finanzlogik, API, Datenbank, Navigation oder Bottom-Navigation geaendert.
- Validierung: Alle eingebetteten Frontend-Skripte mit `node --check` sowie `git diff --check` sind gruen.

### 21.08.2026 - UI-Polish Sprint 3.2: Home- und Ziele-Icon-Feinschliff

- Die zu kleine Compact-Wirkung wurde beendet. Das gemeinsame System verwendet jetzt nur noch
  `Regular` (40 px) und `Medium` (38 px); die visuelle Praesenz bleibt damit nahe an den Settings-
  Icons, ohne kleine Karten zu sprengen.
- Home-Asset-Icons nutzen wieder Regular-Groesse mit proportionalem Icon; Layout und Home-Hierarchie
  bleiben unveraendert.
- Zielkarten verwenden Medium-Icons, etwas mehr kontrollierten Innenraum, einen klareren Zielnamen,
  besser lesbaren Betrag und eine leicht staerkere Progressbar. Empty States sind ebenfalls etwas
  praesenter, ohne Illustration oder neue Aktion.
- Vertraege wurden nur technisch auf die gemeinsame Medium-Klasse umgestellt; deren Informations-
  hierarchie und Funktion bleiben unveraendert. Cashflow, Budgets, Analyse und Settings bleiben stabil.
- Keine Finanzlogik, API, Datenbank, Navigation oder Bottom-Navigation geaendert.
- Validierung: Alle eingebetteten Frontend-Skripte mit `node --check` sowie `git diff --check` sind gruen.

### 20.08.2026 - UI-Polish Sprint 2.6: Investmentbereich sichtbar neu strukturiert

- Der ETF-/Aktien-/Krypto-Detailbereich hat jetzt eine deutlich erkennbare Investment-Heroebene
  mit dem Gesamtwert als Hauptinformation. Der ETF-Gruppentitel wird dabei nutzerfreundlich als
  `Investments` dargestellt; gespeicherte Assetnamen und Daten bleiben unveraendert.
- Die Positionen stehen darunter in einer eigenstaendigen, flachen Sektion `Deine Positionen`.
  Name und Wert sind dominant, ETF/Aktie/Krypto sowie Tracking- und Sparplanhinweise bleiben
  sekundar. Es wurden keine neuen Werte berechnet.
- Die Positionsliste nutzt weiterhin dieselben Taps, Edit-, Sparplan-, Add- und Delete-Handler.
  Konto-Detail, Analyse, Navigation, APIs, Datenbank und Finanzlogik wurden nicht veraendert.
- Validierung: Alle eingebetteten Frontend-Skripte mit `node --check` sowie `git diff --check` sind gruen.

### 20.08.2026 - UI-Polish Sprint 3: Home, Hauptwert und Icon-Ruhe

- Der Home-Screen hat jetzt eine klarere Hierarchie: Gesamtvermoegen zuerst, Chart als zweite
  Ebene, Mentor-Hinweis als ruhiger Reflexionsmoment und Vermoegenswerte danach.
- Die Gesamtvermoegen-Zahl wurde sachlicher gesetzt: weniger Gewicht und Dekoration, engeres
  Letterspacing, tabellarische Zahlendarstellung und kein Textschatten. Der Wert bleibt die
  visuelle Hauptinformation.
- Chart-Abstand und Zeitraum-Navigation wurden innerhalb des Home-Screens beruhigt, ohne Chart-
  Berechnung oder Interaktion zu veraendern.
- Home-Assetliste und Icons verwenden weniger Glow, dezentere Rahmen, kleinere einheitliche
  Iconformen und flachere Container. Die bestehende Icon-Logik bleibt unveraendert.
- Mentor-Karte und Asset-Bereich konkurrieren nicht mehr mit dem Hauptwert; Bottom-Navigation,
  Navigation, API, Datenbank, State- und Finanzlogik blieben unveraendert.
- Validierung: Alle eingebetteten Frontend-Skripte mit `node --check` sowie `git diff --check` sind gruen.

### 21.08.2026 - Report V2 Sprint 2: additive Truth Layer

- `report_snapshots_v2` ergänzt das bestehende `monthly_snapshots`-System additiv. Ein finaler
  Snapshot wird für `user_id + report_month + schema_version` nur einmal erzeugt und bei Retries
  wiederverwendet; bestehende Reports und Archivdateien bleiben unangetastet.
- Webreport und PDF erhalten im Worker denselben eingefrorenen `report_data`-Datensatz inklusive
  Snapshot-Version und Hash. Nach Finalisierung wird nicht erneut aus Live-Profilwerten berechnet.
- Report-Ausgaben werden zentral klassifiziert. Verknüpfte Transfers, Abhebungen, Einnahmen,
  Fixkosten und Investmentbewegungen werden nicht als Konsum gezählt. Kategorie- und Budgetwerte
  verwenden dieselbe reportfähige Konsumlogik.
- Multi-Account-Cash wird vor Finalisierung gegen `users.current_cash` geprüft. Bei Drift wird
  der Snapshot abgebrochen; der Report-Worker repariert keine Kontostände automatisch. Legacy-
  Nutzer bleiben ohne Multi-Account-Flag kompatibel.
- Snapshot enthält zusätzlich reportfähige Kategorie-/Merchant-Aggregate, Budgetstand, Goal-Stand,
  Score, Cash-Quelle, Investmentbeiträge/Holdings soweit verfügbar sowie bewusst keine erfundene
  Market-Movement-Zahl.
- Neue Tests: Transferausschluss, Cash-Invariante, Snapshot-Immutability. Gesamtvalidierung:
  54 Tests erfolgreich; `py_compile` und `git diff --check` gruen.
- Geänderte Dateien: `report_engine.py`, `rove_report_worker.py`, `migrate_report_snapshots_v2.py`,
  `test_report_snapshot_v2.py`. Für die Server-Abnahme gibt es zusätzlich den fail-closed Lauf
  `accept_report_snapshot_v2.py`; er markiert nur bei vollständiger Produktionsprüfung `GO`.
- Produktionsabnahme deckte eine falsche Typannahme bei `app_goals.goal_id` auf: App-Ziel-IDs sind
  stabile Text-IDs und bleiben jetzt auch im Report-Snapshot unverändert als Text erhalten. Ein
  Regressionstest mit einer produktionsnahen `g_...`-ID hebt die Gesamtvalidierung auf 55 Tests.
- Produktive Abnahme fuer Nutzer `653187414`, Monat `2026-07`: GO. Snapshot V2 wurde als
  `finalized` mit Snapshot-ID `1` erzeugt; zwei Acceptance-Laeufe lieferten denselben Daten-Hash
  `82ebab76981a803006eb02369faa7797508225dff0b4a30b6da5f917cbd80e36`. Cash-Invariante,
  SQLite-Integritaet, Foreign Keys, Dienste, Web/PDF-Datenidentitaet und Retry-Immutability sind
  gruen. Alte Reports und Reportdesign wurden nicht veraendert.

### 21.08.2026 - Report V2 Sprint 3: technische Abnahmeroutine geschaerft

- Die deterministische Story V2 ist implementiert und lokal mit 20 Story-/Snapshot-Tests
  validiert. Sie erzeugt exakt zehn Seiten aus dem eingefrorenen Snapshot-Payload.
- `accept_report_snapshot_v2.py` prueft fuer die produktive Abnahme jetzt zusaetzlich den aktiven
  Worker-Timer, den letzten erfolgreichen oneshot-Worker-Lauf, relevante Worker-Fehlerlogs,
  Story-Version/Seitenzahl, zentrale Report-Fakten, AI-Text-Unveraendertheit und SQLite erneut
  nach dem Retry.
- Webreport und PDF erhalten weiterhin exakt denselben unveraenderten Snapshot-Payload. Es gibt
  keine zweite Live-Finanzaggregation im Renderer.
- Dieser Eintrag dokumentiert die vorbereitete Abnahmelogik, nicht deren produktive Ausfuehrung.
  Der finale GO/NO-GO-Status fuer Sprint 4 wird erst nach dem Serverlauf festgelegt.

### 21.08.2026 - Report V2 Sprint 4: Web/PDF Storytelling und UX

- Web und PDF folgen jetzt derselben klaren 10-Seiten-Reise: Monatsauftakt, Gesamtbild,
  Money Map, Haendler, Hebel, Vermoegen, Aufbau, Ziele, Recap und naechster Monat.
- Beide Renderer verwenden ausschliesslich dieselbe Story-V2-Praesentationsschicht aus dem
  finalisierten Snapshot. Finanzlogik, Datenbank, API, Worker und Snapshot bleiben unveraendert.
- Leere Investments, Ziele, Kategorien und Haendler werden ohne erfundene Werte sauber abgefangen.
  Erklaertexte und Betraege sind typografisch fuer deutsche Nutzer vereinheitlicht.
- Validierung: 24 Reporttests erfolgreich, Web ohne horizontalen Overflow bei 1280 px und 390 px,
  PDF visuell als exakt 10 A4-Seiten geprueft; `py_compile` und `git diff --check` gruen.

### 21.08.2026 - Report V2 Sprint 4A: Webreport Final Polish

- Die fuer die visuelle Abnahme verwendete Vorschau stammt eindeutig aus dem synthetischen
  `standard_payload()` der Story-V2-Tests: Monat `2026-07`, ohne Nutzer- oder Snapshot-ID.
  Namen und Werte der Vorschau sind daher Testdaten und keine falsch gebundenen Produktivdaten.
- Ausschliesslich der Webreport wurde beruhigt und neu gewichtet: kleinere Monatsheadline,
  klarere Typografie, ausgeglichene Startseite, offene Geldfluss-Zeilen statt Mini-Karten,
  Money-Map-Balken, staerkere Haendlerhierarchie sowie menschlichere Investment-, Ziel- und
  Abschlussformulierungen.
- Entwicklerbegriffe und technische Validierungssprache wurden aus der Nutzeransicht entfernt.
  Story V2, Snapshot, Finanzlogik, API, Datenbank und Worker bleiben unveraendert.
- PDF-Template und PDF-Renderer wurden nicht angefasst. Ihre SHA-256-Pruefsummen sind gegen den
  Stand vor Sprint 4A identisch.
- Mobile-Abnahme: synthetischer Stresstest bei 320 px und 390 px mit langen Haendlernamen,
  grossen Betraegen und fuenf Kategorien ohne horizontalen Overflow oder abgeschnittene Sektionen.
  Desktop-Abnahme bei 1280 px mit begrenzter Inhaltsbreite von 980 px ebenfalls ohne Overflow.
- Validierung: 24 Reporttests erfolgreich; `py_compile` und `git diff --check` gruen.

### 21.08.2026 - Report V2 Sprint 4B: alter Web-Look mit V2-Story

- Visuelle Referenz ist der vor dem Redesign versionierte Webreport aus Commit `d3a4021`,
  Template `report_templates/rove_web_report.html`. Uebernommen wurden die Kombination aus
  `Hanken Grotesk` und `Newsreader`, die dunkle Dokumentatmosphaere, Glasflaechen, grosse ruhige
  Abschnittsnummern, Scroll-Reveals, wachsende Balken, Fortschrittsleiste und Dokumentbreite.
- Nicht zurueckgerollt wurden Snapshot V2, Truth Layer, Story Engine V2 oder deren Datenfelder.
  Der Webreport folgt weiterhin exakt zehn V2-Seiten: Monat, Geldfluss, Kategorien, Haendler,
  Vormonatsvergleich, Vermoegen, Aufbau, Score/Ziele, Rov.E Insight und naechster Monat.
- Der Hero wurde neu und kompakter aufgebaut. Die alte Glas- und Animationstiefe ist zurueck,
  Hintergrund und Karten sind fuer bessere Lesbarkeit minimal aufgehellt; Neon-/AI-Dekoration
  wurde nicht uebernommen.
- Kategorie- und Haendlerrankings verwenden die V2-Aggregate. Investmentseite zeigt nur echte
  Contributions, Vermoegen nur vorhandene Klassen, Ziele und Score bleiben V2-basiert. Seite 9
  zeigt genau einen zentralen Rov.E Insight.
- Die synthetische Vorschau ist sichtbar mit `Testdaten`, `Kein Snapshot` und `Story V2`
  gekennzeichnet. Monat ist `2026-07`; keine Produktivdaten wurden fuer die Vorschau verwendet.
- PDF-Template und PDF-Renderer wurden nicht angefasst; ihre SHA-256-Pruefsummen sind weiterhin
  identisch zum Stand vor Sprint 4A/4B.
- Validierung: 25 Story-/Snapshot-/Renderer-Tests erfolgreich, Web-JavaScript mit `node --check`,
  beide zehnseitigen Preview-Varianten ohne Jinja-Reste und `git diff --check` gruen. Die direkte
  automatisierte Browsernavigation zur lokalen `file://`-Vorschau wurde von der Browser-Sandbox
  blockiert; die fertige Preview liegt fuer die manuelle visuelle Abnahme bereit.

### 21.08.2026 - Report V2 Sprint 4C: harter Web-Optik-Reset

- Der Webreport wurde optisch bewusst auf die alte, vor den Sprint-4-Experimenten versionierte
  Webreport-DNA aus Commit `d3a4021` zurueckgestellt: dunkle Premium-Dokumentflaeche, Glas-Karten,
  grosse ruhige Sections, `Hanken Grotesk`/`Newsreader`, Reveal-Animationen, wachsende Balken,
  Score-Ring und alte Scroll-/Progress-Wirkung.
- Nicht zurueckgerollt wurden Truth Layer, Snapshot V2, Story V2, Finanzlogik, API, Datenbank,
  Worker, PDF-Template oder PDF-Renderer. Der alte Look wird nur ueber eine neue Mapping-Schicht
  mit den finalisierten V2-Snapshotdaten befuellt.
- Der Hero wurde kompakter und weniger dekorativ gehalten; fuehrende Schmuck-Trennstriche wurden
  entfernt. Kategorie-, Haendler-, Cash-, Investment-, Ziel- und Scorewerte kommen weiterhin aus
  den V2-Aggregaten und erzeugen keine neuen Live-Finanzberechnungen.
- Eine synthetische Sprint-4C-Vorschau wurde erzeugt:
  `work/Calrity_Main/output/web/rove_report_v2_sprint4c_preview.html`.
  Sie enthaelt 10 Sections und keine offenen Jinja-Platzhalter.
- PDF-Dateien wurden in diesem Sprint nicht veraendert. Gepruefte SHA-256-Werte:
  `report_templates/rove_pdf_report.html`
  `7276206b33632be813dcb4f0eee9e6791cf1100ea435ec8edc3cefc6be7d29c4`,
  `rove_pdf_light_renderer.py`
  `0453ad71a654bcce75b5c083462d7d9e6c7d57e3296be01766aa707d31984b8b`,
  `rove_pdf_report_renderer.py`
  `f4a8af4b78da7555b109a0cd4d6b12a837b2eb98f2dfd97e8af919215414f972`.
- Validierung: `py_compile`, 24 Story-/Snapshot-/Renderer-Tests und `git diff --check` sind gruen.
  Ein echter Juli-2026-Webreport aus dem finalisierten Produktionssnapshot muss nach dem Git-Deploy
  auf dem Server erzeugt und visuell abgenommen werden.

### 21.08.2026 - Report V2 Sprint 4D: Original-Opening verbindlich wiederhergestellt

- Der Referenzcommit `d3a4021` wurde gegen die bereitgestellten iPhone-Screenshots abgeglichen.
  Der Einstieg verwendet wieder den originalen Rov.E-Aufbau: `Dein Monatsabschluss`, Serif-Hero,
  Zeitraum, Fortschritt, Entwicklung sowie anschliessend `Ueberblick` und `Dein Monat auf einen Blick.`
- Die vormalige V2-Geldflussseite wurde aus Position 2 entfernt. Kategorien, Haendler, Veraenderungen,
  Vermoegen, Aufbau, Score, Ziel, Insight und naechster Monat bleiben als V2-Vertiefung nach dem
  originalen Opening bestehen.
- Die fruehere Karte `Groesste Ausgabe` wird mit V2-Daten als `Top-Haendler` bezeichnet, weil der
  eingefrorene Snapshot dort ein Haendleraggregat, aber keine einzelne groesste Transaktion liefert.
  Das verhindert eine fachlich falsche Kundenaussage.
- Keine Finanzlogik, Datenbank, API, Worker oder PDF-Dateien wurden veraendert. Vor einem Deploy
  ist eine lokale visuelle Abnahme des echten, finalisierten Juli-Snapshots erforderlich.

### 21.08.2026 - Report V2 Sprint 4F: Snapshot-Kompatibilitaet und Stichtagsvergleich

- Alte finalisierte V2-Snapshots ohne `report_truth.wealth` werden beim Rendern ausschliesslich
  aus ihren bereits eingefrorenen Cash-, Investment- und Immobilienwerten gelesen. Es gibt keinen
  Live-Datenbank-Fallback und keine Mutation historischer Snapshotdaten.
- Laufende Monate erhalten einen eingefrorenen Stichtag. Ausgaben und Investmentbeitraege werden
  nur bis zu diesem Tag mit dem gleichen Kalendertag des Vormonats verglichen; Score-Vergleiche
  werden in diesem Teilmonatsmodus bewusst unterdrueckt.
- Keine Template-, Design-, PDF-, API- oder Datenbankaenderung. Lokale Abnahme: 30 Report-Tests,
  `py_compile` und `git diff --check` erfolgreich. Echte Juli-/August-Servervorschau steht noch aus.

- Nach der ersten Server-Abnahme wurde ein weiterer Legacy-Fall abgedeckt: Vorgaenger-Snapshots
  ohne Truth Layer behalten ihre bereits eingebettete Story unveraendert. Nur Snapshots mit einem
  vorhandenen Truth Layer, dem das neue Vermoegensfeld fehlt, werden lesend normalisiert.

- Fuer noch aeltere Reporte ohne Truth Layer und ohne Story V2 erzeugt der Web-Renderer keine
  neue Finanzstory. Er verwendet eine neutrale Praesentationshuelle, damit die vorhandenen,
  eingefrorenen Legacy-Seiten weiterhin unveraendert rendern koennen.
- Die sehr alte Vorlage liest darin noch `report.pages.page_3.text`; der neutrale Kontext liefert
  diese einzelne Anzeigeinformation ohne eine fachliche Aussage oder neue Berechnung.
## Update 21.08.2026 - Report V2 Legacy-Datenbindung

- Alte, eingefrorene Report-Snapshots liefern ihre vorhandenen Monats-, Vermoegens-, Konsum-, Investment-, Score- und Zielwerte jetzt an den gemeinsamen Webreport-Kontext.
- Es findet dabei kein Zugriff auf aktuelle Live-Finanzdaten und keine Snapshot-Mutation statt.
- Zielstand und Nettovermoegen sind getrennt: Die Zielkarte zeigt den Zieltopf, der Meilenstein das Nettovermoegen.
- Leere oder unvollstaendige Altwerte bleiben sicher als nicht verfuegbar markiert.
- Verifikation: 34 Report-Tests erfolgreich, `py_compile` und `git diff --check` sauber.

## Update 21.08.2026 - Report V2 finaler Story-/Truth-Pass

- Der Webreport trennt nun Monatsaggregate sauber von Einzelbuchungen: Top-Haendler basiert auf
  dem aggregierten Haendlerwert, die groesste Einzelausgabe bleibt ein eigener Truth-Layer-Fakt.
- Kategoriebezeichnungen werden nicht mehr als Haendlerersatz ausgegeben. Unbrauchbare oder
  generische Haendlerwerte werden neutral behandelt.
- Vormonatsvergleich, Budgethinweis, Score-Erklaerung, Ziel, Meilenstein und naechste Schritte
  verwenden nur vorhandene Snapshotwerte. Nullbeitraege bleiben neutral; es gibt keine erfundenen
  Monats-/Jahreshochrechnungen, Score-Versprechen oder moralische Budgettexte.
- Die lokale Juli-Pruefvorschau bildet unter anderem Shopping 733 EUR bei 200 EUR Budget,
  533 EUR Ueberschreitung, Breuninger als aggregierten Top-Haendler, 0 EUR neuen Beitrag,
  1.000 EUR geplante Sparrate und 1.009 EUR bis zum naechsten Vermoegensmeilenstein ab.
- Webdesign, CSS, Seitenstruktur, PDF, API, Datenbank und Worker wurden nicht veraendert. Es wurde
  nichts deployed.
- Verifikation: 37 Story-/Snapshot-/Renderer-Tests erfolgreich, `py_compile` und
  `git diff --check` sauber. Die lokale Vorschau liegt unter
  `work/Calrity_Main/output/web/rove_report_v2_final_truth_july_review.html`.
- Offener Abnahmepunkt: Im lokalen Datenbestand liegt kein finalisierter Produktionssnapshot fuer
  Nutzer 653187414 und Juli 2026. Snapshot-ID und reale Produktionsdatenbindung koennen deshalb
  erst in einem spaeteren, kontrollierten Read-only-Produktionscheck bestaetigt werden.

## Update 21.08.2026 - Report V2 Produktionssnapshot und Top-3-Haendler abgenommen

- Der echte finalisierte Produktionssnapshot fuer Juli 2026 wurde isoliert und read-only geprueft:
  Snapshot-ID `1`, Schema/Story Version `2`, Status `finalized`. Weder Snapshot noch Datenbank,
  Produktionscode, Services oder PDF wurden dabei veraendert.
- Die bestehende Haendler-Vertiefung zeigt nun bis zu drei reale, normalisierte Merchant-Aggregate
  nach Gesamtbetrag. Pro Zeile werden Rang, Name, Gesamtbetrag, Buchungsanzahl, Durchschnitt und
  Kategorie angezeigt. Nicht vorhandene Haendler erzeugen keine Dummy-Zeilen; Kategorien koennen
  weiterhin nicht als Merchant-Fallback erscheinen. Die einzelne Top-Haendler-KPI im Ueberblick
  bleibt unveraendert.
- Die isolierte Real-Snapshot-Vorschau liegt unter
  `work/Calrity_Main/output/web/rove_report_v2_real_july_final_review.html`. Sie traegt eingebettete
  Source-/Monats-/Snapshot-/Finalized-/Story-Metadaten, enthaelt zehn Seiten, drei Haendlerzeilen,
  keine offenen Template-Platzhalter und keine verbotenen Legacy-Formulierungen.
- Verifikation: 37 Story-/Snapshot-/Renderer-Tests erfolgreich, `py_compile` und
  `git diff --check` sauber. Kein Deploy wurde ausgefuehrt.

## Update 22.08.2026 - Report V2 finaler Microcopy-Cleanup

- Die Erklaerung zu Zieltoepfen auf der Vermoegensseite wurde vereinfacht:
  `Zieltoepfe zeigen nur, wofuer Geld reserviert ist. Sie erhoehen dein Vermoegen nicht zusaetzlich.`
- Keine Finanzlogik, Datenbank, API, PDF, Seitenstruktur oder Designsystem-Aenderung.
- Verifikation: 39 Story-/Snapshot-/Renderer-Tests erfolgreich, `py_compile` und `git diff --check` sauber.

## Update 22.08.2026 - Report V2 finaler PDF-Sprint

- Aktiver produktiver PDF-Pfad bestaetigt: `report_engine.build_pdf()` nutzt zuerst
  `rove_pdf_light_renderer.py` mit `report_templates/rove_pdf_report.html`; der alte
  ReportLab-Renderer bleibt nur Fallback.
- Das bestehende helle A4-PDF-Design bleibt erhalten. Geaendert wurden nur V2-Inhaltsbindung
  und sichtbare PDF-Copy: Money Map zeigt bis zu sechs Kategorien mit Betrag und Prozent,
  echte Budgetzeilen werden kompakt angezeigt, Top-3-Haendler bleiben zusaetzlich erhalten,
  Vormonatsveraenderungen zeigen Betrag und Prozent.
- Technische Kundentexte wie `Dokumentierte Investmentbeitraege` und `keine erfundene Marktperformance`
  wurden aus dem PDF entfernt. 0-EUR-Aufbau bleibt neutral.
- Die Vermoegensseite wiederholt das Nettovermoegen nicht erneut als Hero-Zahl, sondern zeigt
  die eingefrorene Asset-Aufteilung. Falls ein Snapshot keine Allocation-Liste enthaelt, wird
  diese nur aus den eingefrorenen Wealth-Werten Cash, Investments und Immobilie praesentiert.
- Die Score-Karte zeigt nun Level und Teilbereiche kompakt aus den V2-Subscores.
- Lokale PDF-Review-Datei: `work/Calrity_Main/output/pdf/rove_report_v2_pdf_final_fixture_review.pdf`.
  Render-Check: WeasyPrint, A4, exakt 10 Seiten.
- Fuer die echte Juli-Produktionsabnahme liegt ein read-only Script bereit:
  `report-pdf-review-final.sh`. Es erzeugt aus Snapshot-ID/Monat auf dem Server eine Review-PDF,
  ohne Snapshot, DB, Services oder Deploy zu veraendern.
- Verifikation: 40 Story-/Snapshot-/Renderer-Tests erfolgreich, `py_compile`, `pdfinfo` und
  `git diff --check` sauber. Kein Deploy wurde ausgefuehrt.

## Update 22.08.2026 - PDF-Design auf originale helle Referenz korrigiert

- Der zuvor erzeugte grau-gruene Ersatzstil wurde verworfen. Der aktive HTML/PDF-Renderer nutzt
  jetzt die originale helle Rov.E-Referenz `Rov.E PDF NEU hell` als Designquelle: Hellblau,
  Creme, blaue Akzente, Newsreader-Typografie und die bestehende Kartenwirkung.
- Die zehn Seiten werden ausschliesslich mit eingefrorenen V2-Snapshotwerten befuellt. Money Map,
  Vormonatsvergleich, Top-3-Haendler, Vermoegensstruktur, Score, Zieltopf, Meilenstein, Recap und
  naechste Schritte bleiben fachlich getrennt.
- Haendler-Kategorien stammen aus den Merchant-Aggregaten; Kategorie-Fallbacks und Dummy-Haendler
  bleiben ausgeschlossen. Zielstand und Nettovermoegen werden nicht vermischt.
- Eine Prognose aus der allgemeinen Sparrate wurde bewusst aus der Zielseite entfernt, weil ohne
  zielbezogene Rate keine belastbare Zielzeit behauptet werden darf. Die wortgleiche Budget-
  Wiederholung im Recap wurde ebenfalls entfernt.
- Die Betrags- und Prozentbloecke im Vormonatsvergleich sind innerhalb der hohen Karten nach unten
  ausgerichtet; der freie Raum liegt nun bewusst zwischen Kartenlabel und Kennzahl.
- Lokale visuelle Kontroll-PDF:
  `work/Calrity_Main/output/pdf/rove_report_v2_hell_design_check.pdf` (615 x 810 pt, 10 Seiten).
- Verifikation: 40 Story-/Snapshot-/Renderer-Tests erfolgreich, `py_compile`, `pdfinfo` und
  `git diff --check` sauber. Noch kein Deploy ausgefuehrt.

### Cover-Fusszeile

- Die Fusszeile auf Seite 1 ist jetzt am unteren Seitenrand verankert; die Cover-Karten behalten
  ihre bestehende Hoehe und das bestehende Design.
- Die Kontroll-PDF bleibt bei 10 Seiten; 40 Story-/Snapshot-/Renderer-Tests weiterhin erfolgreich.
- Die Money-Map-Fusszeile auf Seite 5 folgt wieder direkt dem Inhaltsblock, damit der grosse freie
  Bereich darunter nicht wie ein verrutschtes Element wirkt.

## Update 23.08.2026 - Stability Fix 6 Contract migration guard

- `gesamt` ist in Legacy-Fixkosten immer ein Abschnittsaggregate und wird unabhaengig von seinem
  gespeicherten Betrag weder als Vertrag migriert noch in die operative Fixkostensumme eingerechnet.
- `hausgeld` bleibt davon bewusst ausgenommen: nur ein leerer/Null-Slot bleibt Legacy, ein positiver
  Hausgeldvertrag ist weiterhin ein regulaerer, migrierbarer Vertrag.
- Die Grenzfaelle `gesamt=0`, `gesamt=500`, `hausgeld=0` sowie normale positive Legacy-Vertraege
  sind in `test_stability_sprint6.py` abgedeckt. Vor einem Apply sind weiterhin Production-Baseline
  und der reine Migration-Dry-Run erforderlich.

## Update 24.08.2026 - Stability Fix 7 Delete-Cleanup Retry execution

- Der vorhandene taegliche Report-Wartungslauf `rove_report_worker.py maintain` verarbeitet nun
  zusaetzlich offene Account-Delete-Dateicleanups. Es wurde kein neuer Service, Timer, Cronjob
  oder API-Request-Polling eingefuehrt.
- Die bestehende sichere Retry-Logik bleibt die einzige Cleanup-Wahrheit. Jeder Wartungslauf
  verarbeitet hoechstens 20 offene Eintraege; fehlgeschlagene Eintraege bleiben mit erhoehtem
  Versuchszähler offen und blockieren nachfolgende Eintraege nicht.
- Tests decken automatischen Wartungslauf, erfolgreiche und fehlende Dateien, fehlgeschlagene
  Retries neben erfolgreichen, abgeschlossene Eintraege, Batch-Grenze und Pfad-Guards ab.
- Der Worker importiert Report-Renderer und Report-Engine erst innerhalb des tatsaechlichen
  Prozess- beziehungsweise Wartungslaufs. Damit kann der API-Venv-Test den Cleanup-Pfad pruefen,
  ohne beim Modulimport eine optional getrennte Report-Abhaengigkeit vorauszusetzen; die
  produktive Report-Ausfuehrung selbst verwendet unveraendert dieselben Module.
- API und Worker verwenden fuer den Account-Delete-Cleanup jetzt dieselbe Flask-freie
  Standardbibliotheks-Schicht. Das verhindert eine Abhaengigkeit auf Flask im bestehenden
  System-Python-Wartungsdienst und auf `dotenv` in der API-Venv, ohne die Pfadregeln,
  Retry-Semantik oder Datenbanktransaktion zu duplizieren.

## Update 24.08.2026 - Stability Fix 8 Web-Logout and PWA verification

- Die Web-App nutzt fuer das Abmelden den bestehenden serverseitigen `/v1/auth/logout`-Endpoint.
  Erst nach erfolgreicher Session-Invalidierung werden der gespeicherte State-Link, lokale
  Brueckendaten und die Nutzer-spezifischen Kategorien/Erinnerungen entfernt; globale
  Darstellungs-Praeferenzen bleiben auf dem Geraet erhalten.
- Die Produktion liefert Manifest, Icon, Logo und Service Worker bereits aus. Der Worker bleibt
  bewusst ohne Fetch-Cache. Drei Telegram-only Altaccounts ohne verifizierte Web-Identitaet
  bleiben unveraendert; es gibt bewusst keinen Claim-, Merge- oder Telegram-Login-Pfad. Aktive
  Webkonten melden sich ausschliesslich mit ihrer verifizierten E-Mail an.

## Update 24.08.2026 - Home avatar and chart spacing

- Der Home-Avatar verwendet bei einem noch leeren Anzeigenamen die verifizierte E-Mail als
  Initialenquelle. Damit zeigt eine aktive Web-Identitaet nie mehr den neutralen Punkt statt
  eines persoenlichen Buchstabens.
- Die Vermoegenskurve behaelt ihre echte Zeitreihe unveraendert, bekommt unterhalb ihres
  niedrigsten Punktes aber mehr SVG-Abstand. Dadurch liegt eine fallende Kurve nicht optisch
  am unteren Rand.

## Update 24.08.2026 - Auth Sprint 9 Phase 1 local implementation

- Passwortzugang ist additiv ueber `app_credentials` umgesetzt. Passwoerter werden ausschliesslich
  als Argon2id-Hash gespeichert; Zugangsdaten, Sessions, Login-Codes und Reset-Codes bleiben von
  der positiven Finanzexportliste ausgeschlossen und werden beim Kontoloeschen entfernt.
- Passwort-Setup fuer eine verifizierte bestehende Sitzung, Passwort-Login, Passwortwechsel und
  ein einmaliger HMAC-geschuetzter Passwort-Reset sind vorhanden. Reset und Passwortwechsel
  widerrufen alte Sessions und stellen fuer das aktuelle Geraet eine neue sichere Sitzung aus.
- `ROVE_APP_AUTH_SECRET` ist fuer alle Auth-Hashes verpflichtend und hat keinen Fallback auf
  Mail- oder Drittanbieter-Schluessel. Rate-Limits sind getrennt fuer Code-Anforderung,
  Code-Pruefung, Passwort-Login sowie Reset-Anforderung und Reset-Pruefung.
- Die Web-Oberflaeche verwendet Passwort-Login als Standard und behaelt E-Mail-Code als
  kompatiblen Fallback. Neue Konten setzen nach Code-Verifikation zuerst ihr Passwort; der
  automatische Monatsplan wird nur direkt nach dem erfolgreichen Onboarding einmal ausgesetzt.
- Lokale Verifikation mit der isolierten Argon2-Testumgebung: 136 Tests erfolgreich,
  Python-Compile, JavaScript-Syntax und `git diff --check` sauber. Noch kein Production Gate,
  kein Deploy und keine Produktionsdaten-Aenderung.

## Update 24.08.2026 - Security Fix 9.1 local state-link removal

- Die App bootstrapped Finanzdaten nicht mehr aus `/app-state/<token>.json`, sondern nur noch
  ueber den vorhandenen Cookie-authentifizierten `/v1/state`-Endpoint. Der Browser speichert
  keinen State-Link und keinen API-Bearer mehr; bei fehlender Session erscheint der Login statt
  eines alten Finanz-Snapshots.
- `user_from_token()` ist als zentrale Kompatibilitaetsgrenze erhalten, ignoriert alte Bearer
  aber absichtlich und leitet jede Finanzanfrage ausschliesslich aus der HttpOnly-Session ab.
  Dadurch kann ein spaeterer Device-Lock an genau einer Stelle ergaenzt werden. Aktive
  `app_state_links` werden beim Schema-Setup widerrufen, ohne normale Web-Sessions anzutasten.
- Der alte State-Writer ist side-effect-free und der Telegram-Befehl `/app` erzeugt keinen
  persoenlichen Link mehr. Die vorbereitete Nginx-Location `deploy/nginx/rove-app-state-disabled.conf`
  sperrt den alten oeffentlichen Pfad beim kontrollierten Production Cutover; die alte
  Dateibereinigung bleibt weiter ueber den bestehenden Account-Delete-Mechanismus moeglich.
  `retire_legacy_app_state.py` liefert davor ein read-only Inventar und fuehrt nur mit `--apply`
  ein timestamped Backup, die Token-Revocation und die idempotente Entfernung bekannter Dateien aus.
- Neue P0-Tests pruefen fehlende Session, alten Bearer, Nutzertrennung, Logout, no-store und
  das Fehlen eines State-/Bearer-Bootstraps im Client. Vollstaendige lokale Regression: 141 Tests
  erfolgreich, Python-Compile, JavaScript-Syntax und `git diff --check` sauber. Kein Deploy.

## Update 24.08.2026 - Auth Sprint 9 Phase 2 local App-PIN

- Die verpflichtende vierstellige App-PIN ist serverseitig an genau eine `app_sessions.id`
  gebunden. Der Verifier besteht aus einer mit `ROVE_APP_AUTH_SECRET` gebundenen PIN-Ableitung
  und einem Argon2id-Hash; PIN, Unlock-State und Fehlversuche werden weder exportiert noch im
  Browser gespeichert. Beim Account-Delete werden PIN-Zeilen vor den Sessions explizit entfernt.
- Ein zentraler Request-Guard sperrt alle authentifizierten `/v1`-Finanzpfade mit HTTP 423 und
  `pin_locked`, solange die Session keine PIN hat, gesperrt oder nach fuenf Minuten inaktiv ist.
  Nur Auth-Status, Setup, Unlock, Lock, Recovery, Logout und interne Server-Push-Aufrufe bleiben
  offen. Der einmalige Onboarding-POST ist nur fuer neue, noch unvollstaendige Konten vor PIN
  erlaubt und liefert danach keine Finanzdaten, sondern fuehrt direkt zum PIN-Setup.
- Nach fuenf falschen PINs bleibt auch die richtige PIN blockiert. Nur eine vollstaendige
  Re-Authentifizierung mit E-Mail und Passwort kann fuer diese Session eine neue PIN setzen;
  andere Geraete und Sessions bleiben unabhaengig. Passwort-Reset und Passwortwechsel erzeugen
  wie bisher neue Sessions, die danach erneut eine eigene PIN benoetigen.
- Die App prueft vor `/v1/state` zuerst den PIN-Status und haelt `#app` bis zum Unlock verborgen.
  Aktive Nutzung sendet hoechstens einmal pro Minute einen datenfreien Aktivitaets-Ping; nach
  fuenf Minuten ohne Interaktion erscheint der minimale PIN-Screen ohne Navigation oder
  Finanzhintergrund. PIN-Aenderung ist unter Einstellungen -> Sicherheit angebunden.
- Lokale Verifikation: bisherige 149 Tests plus 20 neue PIN-/Frontend-Tests, insgesamt 169 Tests
  erfolgreich. Python-Compile, vier JavaScript-Bloecke und `git diff --check` sind sauber. Die
  Browser-Sichtpruefung einer lokalen `file://`-Datei wurde von der Browser-Sicherheitsrichtlinie
  blockiert und nicht umgangen. Kein Commit, kein Production Gate und kein Deploy.

## Update 25.08.2026 - App-PIN exit lock and visual lock screen

- Beim Verlassen der Web-App wird der serverseitige PIN-Lock ueber `sendBeacon` beziehungsweise
  `fetch(..., keepalive)` angefordert. Beim Zurueckkehren wird der PIN-Status erneut geladen;
  damit bleibt eine kurzfristig geschlossene App nicht innerhalb des fuenfminuetigen
  Inaktivitaetsfensters entsperrt.
- Der PIN-Screen ist jetzt ein echter Vollbild-Lock ohne sichtbaren Finanz-App-Streifen. Er nutzt
  eine ruhige dunkle Ansicht mit PIN-Punkten, numerischem Tastenfeld, Loesch-Taste und Recovery-Link;
  das Setup verwendet dieselbe visuelle Sprache.
- Lokale Verifikation: 22 PIN-/Frontend-Tests, JavaScript-Syntax und `git diff --check` sauber.
  Noch nicht deployed.

## Update 25.08.2026 - App-PIN keypad confirmation

- Das visuelle Tastenfeld bestaetigt die Entsperr-PIN automatisch nach der vierten Ziffer.
  Beim erstmaligen Setup wechselt die Eingabe nach vier Ziffern zur Bestaetigung; nach der
  zweiten Eingabe wird das bestehende Setup verwendet.
- Das Tastenfeld sitzt leicht hoeher, ohne die Vollbild-Lock-Ansicht oder ihre Farben zu veraendern.
- Lokale Verifikation: 22 PIN-/Frontend-Tests, JavaScript-Syntax und `git diff --check` sauber.
  Noch nicht deployed.

## Update 25.08.2026 - Investment total assignment fix

- Die sichtbare Investment-Aufschluesselung ergab 11.474,85 EUR, waehrend
  `users.current_investments` nur 10.849 EUR enthielt. Ursache war kein verlorener XPeng-Datensatz:
  Der manuelle Aktienpfad zog fuer die Restbetragserkennung den ETF-Einstandswert statt des
  sichtbaren Live-Marktwerts inklusive noch nicht eingepreister Sparplanbeitrag heran. Dadurch
  wurden 622 EUR Markt-/Pending-Differenz beim ersten Aktien-Eintrag faelschlich als bereits
  vorhandener unzugeordneter Bestand behandelt.
- Die Zuordnungsrechnung verwendet jetzt dieselbe Wahrheit wie die Positionsanzeige: effektiver
  Marktwert beziehungsweise manueller Holding-Wert, Pending-ETF-Beitraege und nur Aktienevents,
  die nicht bereits als Holding dargestellt werden. Das verhindert sowohl Unterzaehlung als auch
  Doppelzaehlung bei ETF plus mehreren Aktien.
- Regression mit live bewertetem ETF, Pending-Sparrate, Under Armour und XPeng: 50 relevante
  Investment-/Account-Tests erfolgreich; Python-Compile und `git diff --check` sauber. Bestehende
  Produktionsdaten wurden nicht veraendert; der erklaerte Alt-Delta braucht nach einem Production
  Gate eine separate kontrollierte Korrektur.

## Update 26.08.2026 - PIN input without Safari password heuristics

- Die PIN-Eingabe verwendet weiterhin ein neutrales Textfeld mit numerischem Inputmode, vier
  Zeichen, neutralem Namen und deaktivierter Autovervollstaendigung. Die browserseitige
  `-webkit-text-security`-Maskierung wurde entfernt, weil Safari sie trotz `autocomplete=off`
  als Passwortsignal behandeln kann.
- Die Maskierung bleibt vollstaendig erhalten: Jede PIN-Eingabe, auch Aenderung, Setup und
  Recovery, liegt nun unsichtbar hinter vier eigenen Punkten. Damit sieht der Nutzer nie die
  Ziffern, waehrend iOS kein CSS-Passwortfeld mehr erkennen muss. Server-PIN-Guard, Hashing,
  Versuchsgrenze und Re-Auth bleiben unveraendert.
- Lokale Verifikation: 11 gezielte PIN-Frontend-Tests, JavaScript-Syntax und `git diff --check`
  erfolgreich. Nur `rove-app/index.html` ist geaendert; kein API-Neustart erforderlich.

## Update 26.08.2026 - PIN keypad avoids native keyboard

- Der Lock- und Setup-Screen verwendet bereits ein eigenes Rov.E-Tastenfeld. Das unsichtbare
  Eingabefeld war aber noch fokussierbar; iOS oeffnete deshalb zusaetzlich seine native
  Zifferntastatur ueber dem Rov.E-Keypad.
- Nur die Felder, die vom eigenen Tastenfeld bedient werden, sind nun `readonly`, nicht per Tap
  erreichbar und nicht in der Tab-Reihenfolge. Die PIN wird weiterhin ausschliesslich durch
  `pinPadPress()` als String gesetzt und nach vier Ziffern unveraendert serverseitig validiert.
  Recovery und PIN-Aenderung behalten absichtlich ihre normale native Zahleneingabe.
- Lokale Verifikation: 11 gezielte PIN-Frontend-Tests, JavaScript-Syntax und `git diff --check`
  erfolgreich. Nur `rove-app/index.html` ist geaendert; kein API-Neustart erforderlich.

## Update 26.08.2026 - Crypto V1 local implementation

- Crypto-Positionen nutzen jetzt `portfolio_holdings` mit stabiler CoinMarketCap-ID, Symbol,
  Menge, optionaler Cost Basis, EUR-Marktwert, Kurszeitpunkt und Quelle. Pro Nutzer und Coin-ID
  ist nur eine aktive Position erlaubt; Screenshot-Importe besitzen zusaetzlich einen
  usergebundenen Idempotency-Key.
- Coin-Suche und gebuendelte EUR-Kursabfragen laufen ausschliesslich serverseitig. Der taegliche
  bestehende Marktwert-Worker aktualisiert Crypto-IDs in Batches; ein fehlender Coin behaelt den
  letzten Wert und blockiert andere Positionen nicht. Kursbewegungen veraendern nur
  `current_investments`, niemals Cash, und bleiben von Contributions getrennt.
- Manuelles Hinzufuegen verwendet Coin-Suche, Menge, optionale Cost Basis und eine explizite
  kursbasierte Vorschau vor dem Write. Der separate Crypto-Screenshot-Pfad speichert kein Bild,
  liefert eine editierbare Vorschau, verlangt zwingend eine Menge und schreibt erst nach
  Bestaetigung atomar und idempotent.
- Die zwei bekannten generischen Legacy-Crypto-Bestaende werden weder migriert noch veraendert.
  Sie erscheinen getrennt als `Legacy-Kryptowert`; neue getrackte Holdings werden weder im
  ETF-/Aktienrest noch in Legacy-Events doppelt gezaehlt. Unbekannte Cost Basis bleibt `NULL`,
  weshalb P/L ehrlich als unbekannt angezeigt wird.
- Lokale Verifikation: 12 neue Crypto-/Frontend-Tests plus 72 relevante Financial-Accounts-,
  ETF- und Report-Regressionstests, insgesamt 84 erfolgreich. Die vollstaendige lokale
  Discovery-Suite ist mit 201 Tests ebenfalls gruen. Python-Compile, JavaScript-Syntax und scoped
  `git diff --check` sind sauber. Kein Production Gate und kein Deploy.

## Update 26.08.2026 - Crypto screenshot confirmation stays reachable

- Bei langen Screenshot-Importen lag die Bestaetigung erst nach der letzten erkannten Position
  und damit ausserhalb des sichtbaren iOS-PWA-Sheets. Die Positionen bleiben innerhalb des
  bestehenden scrollbaren Detail-Sheets; die Bestaetigung sitzt nun in dessen sticky
  Safe-Area-Footer und bleibt beim Scrollen dauerhaft erreichbar.
- Die Import-, Validierungs- und Write-Logik ist unveraendert. Die UI nutzt dafuer die bereits
  vorhandenen `scan-actions`-/`scan-confirm`-Komponenten statt eines neuen Sonderpfads.

## Update 26.08.2026 - Crypto search and position management

- Ein-Zeichen-Symbole wie `S` erreichen nun die serverseitige Coin-Suche. Falls CMC einen
  Symbol-/Slug-Filter nicht auswertet, verwendet Rov.E das offizielle aktive CMC-ID-Verzeichnis
  als lokalen exakten Fallback; einzelne Coins oder Aliase werden nicht hardcodiert.
- Getrackte Crypto-Positionen kennzeichnen ihren Bearbeiten-Einstieg jetzt sichtbar. Alte
  Legacy-Gesamtwerte ohne Coin-ID und Menge koennen ueber die bereits vorhandenen usergebundenen
  API-Pfade korrigiert oder per historisch nachvollziehbarer Gegenbuchung entfernt werden. Sie
  werden weiterhin nicht automatisch in Live-Coins umgewandelt.
- Verifikation: 16 gezielte Crypto-Tests und 77 Crypto-/ETF-/Financial-Accounts-Regressionstests
  erfolgreich, Python-Compile, vier JavaScript-Bloecke und scoped `git diff --check` sauber.

## Update 26.08.2026 - Crypto management deploy correction

- Der erste Deploy-Versuch blieb nach dem Git-Pull, aber vor Frontend-Installation und
  API-Neustart stehen. Dadurch konnte die App noch den alten Bearbeiten-/Loeschen-Dialog zeigen,
  obwohl der neue Python-Stand bereits im Checkout lag. Der neue Handoff prueft deshalb den
  kompletten Stand und beendet bei Fehlern nur seine Funktion, nie die SSH-Sitzung.
- Der CMC-Fallback fuer kurze Symbole laedt nun explizit die vollstaendige aktive Map bis 5.000
  Eintraege. So kann `S`/Sonic auch gefunden werden, wenn der Provider den direkten
  Symbol-Filter nicht liefert und Sonic nicht im ersten Standard-Ausschnitt liegt.
- Ein Regressionstest bildet fuenf getrennte Legacy-Crypto-Zeilen ab, entfernt nur eine davon
  und bestaetigt, dass die vier anderen Positionen und Cash unveraendert bleiben.
- Verifikation: 17 gezielte Crypto-Tests und 78 Crypto-/ETF-/Financial-Accounts-Regressionstests
  erfolgreich; Python-Compile, vier JavaScript-Bloecke und scoped `git diff --check` sauber.

## Update 26.08.2026 - Stable crypto deletion references

- Legacy-Crypto-Zeilen besitzen nun eine usergebundene stabile `legacyRef` aus ihrem ersten
  historischen Event. DELETE loest die Position serverseitig ueber diese Referenz auf; sichtbare
  Namen oder Leerzeichen sind nicht mehr Bestandteil der Identitaet einer Position.
- Sowohl getrackte Crypto-Holdings als auch Legacy-Werte liefern nach dem atomaren DELETE nur die
  Loeschbestaetigung. Den aktuellen State laedt das Frontend danach ueber den bestehenden
  `/v1/state`-Pfad. Der zuvor doppelte Komplett-State-Aufbau im DELETE-Request ist entfernt.
- Verifikation: fuenf parallele Legacy-Zeilen, gezieltes Entfernen genau einer Zeile,
  Holding-Delete und User-Isolation getestet. 17 Crypto-Tests und 78 relevante Regressionstests
  erfolgreich; Cash bleibt unveraendert, Python-/JavaScript-Syntax und Diff-Check sauber.

## Update 26.08.2026 - Dedicated legacy delete and Sonic screenshot resolution

- Die produktiv ausgelieferte `index.html` wurde per SHA-256 mit dem lokalen Stand verglichen und
  war bytegenau identisch; Service Worker und PWA-Cache waren nicht die Ursache.
- Legacy-Crypto wird nun wie getrackte Holdings ueber einen eigenen eindeutigen URL-Endpunkt
  `/v1/crypto/legacy/<legacy_ref>` geloescht. Der Request braucht keinen JSON-Body mehr und kann
  deshalb nicht durch unterschiedliche DELETE-Body-Behandlung zwischen Browser/Proxy/API
  unbrauchbar werden.
- Die Screenshot-Aufloesung verwendet bei erkannten Coins zuerst den Namen. `Sonic (S)` sucht
  dadurch nach `Sonic`; das mehrdeutige Symbol `S` mit mehreren CMC-Treffern ist nur noch
  Fallback. Manuelle Symbolsuche zeigt weiterhin alle legitimen `S`-Instrumente zur Auswahl.
- Verifikation: eigener Sonic-Screenshot-Routingtest, dedizierter Legacy-DELETE mit fuenf
  parallelen Altpositionen, User-Isolation und Cash-Invariante. Insgesamt 80 relevante Tests,
  Python-Compile, vier JavaScript-Bloecke und scoped Diff-Check erfolgreich.

## Update 26.08.2026 - Legacy crypto delete empty-name compatibility

- Legacy-Positionen ohne gespeicherten `asset_name` werden beim DELETE jetzt genauso wie in der
  Anzeige als `Krypto` aufgeloest. Dadurch koennen auch alte Telegram-/Legacy-Werte vollstaendig
  ueber die App entfernt werden, ohne andere Gruppen oder Cash zu veraendern.
- Verifikation: 20 Crypto-Tests erfolgreich, inklusive blankem Legacy-Namen; Python-Compile und
  scoped `git diff --check` sauber.

## Update 26.08.2026 - Legacy crypto delete transaction correction

- Der produktive DELETE erreichte die korrekte `legacyRef`, scheiterte aber vor jeder Mutation:
  die Cookie-Session aktualisiert `last_seen_at` und oeffnet eine implizite SQLite-Transaktion;
  der Legacy-Endpunkt startete danach ein zweites `BEGIN IMMEDIATE`. Der Session-Touch wird nun
  abgeschlossen, bevor die atomare Loeschtransaktion beginnt.
- Verifikation: 21 Crypto-Tests erfolgreich, inklusive Cookie-Session-Touch vor Legacy-DELETE;
  Python-Compile und scoped `git diff --check` sauber.

## Update 26.08.2026 - Crypto delete motion and exact Sonic lookup

- Nach dem Entfernen einer Crypto-Position behaelt das offene Detail-Sheet seine Scrollposition.
  Der Editor schliesst weiterhin, die Liste wird aber nicht mehr sichtbar an eine andere Stelle
  aufgebaut.
- Die CoinMarketCap-Suche prueft Provider-Antworten nun lokal gegen die eingegebene Bezeichnung.
  Ignoriert ein Provider Symbol-/Slug-Filter, kann ein fremder `S`-Treffer Sonic nicht mehr
  verdraengen; der aktive Map-Fallback findet den exakten Namen `Sonic`.
- Verifikation: 23 Crypto-Tests, vier JavaScript-Bloecke, Python-Compile und scoped Diff-Check
  erfolgreich.

## Update 26.08.2026 - Canonical Sonic selection

- CoinMarketCap kann mehrere gleichnamige `Sonic`-Token liefern. Bei einer Suche nach Sonic wird
  jetzt der exakte kanonische Slug `sonic` bevorzugt: CoinMarketCap-ID `32684`, Symbol `S`.
  Gleichnamige `SONIC`-Tokens bleiben aus der Auswahl heraus.
- Verifikation: 24 Crypto-Tests erfolgreich, inklusive mehrfacher Sonic-Namen mit unterschied-
  lichen Slugs; Python-Compile und scoped Diff-Check sauber.

## Update 27.08.2026 - PDF Report V2 logic binding

- Der helle Rov.E-Report verwendet die unveraenderte visuelle Referenz mit zehn festen Seiten.
  Ausschliesslich die Datenbindungen greifen auf den finalisierten V2-Snapshot zurueck.
- Der PDF-Renderer nutzt die festen Seitenumbrueche der Referenz direkt und benoetigt deshalb
  kein optionales PDF-Merge-Paket. Damit faellt ein finalisierter Report nicht auf den
  gestalterisch abweichenden Legacy-Renderer zurueck.

## Update 27.08.2026 - PDF Report V2 visual polish

- Die Cover-Kacheln nutzen ihren vertikalen Raum jetzt ausgewogener: Zeitraum, Aufbau und
  Entwicklung sind mittig lesbar gewichtet.
- Die Money Map bleibt mit sechs Kategorien vollstaendig oberhalb der festen Fusszeile, und die
  Score-Seite zeigt den Wert wieder im sichtbaren Kreis.

## Update 27.08.2026 - Monthly Check-in V1 (deployed)

- Eine additive Monatsabschluss-Tabelle speichert pro Nutzer und abgeschlossenem Monat genau
  einen vom Nutzer bestätigten tatsächlichen Sparbetrag. Sie erzeugt keine Cash- oder
  Investment-Buchungen und interpretiert keine historischen Events neu.
- Die serverseitige Check-in-Liste ist die gemeinsame Fälligkeitswahrheit für Einkommen,
  ETF-Positionen und Monatsabschluss. Zukünftige Aktionen erscheinen weder im Badge noch im
  Mentor; Tag 31 wird weiterhin auf das Monatsende begrenzt.
- Der Score akzeptiert die Sparrate nur noch über diesen expliziten Monatsabschluss. Beliebige
  Investment-Events oder alte Badges gelten nicht mehr als Bestätigung.
- Die App nutzt Mentor, dezenten reduzierbaren Pulse, Monatsplan-Badge und die bestehende
  Benachrichtigungsinfrastruktur. Es gibt bewusst kein hartes Popup; Monatsabschlüsse erscheinen
  beim ersten Start im Folgemonat.
- Verifikation: 79 neue und bestehende Monatsplan-/ETF-/Financial-Accounts-/Report-Story-
  Regressionstests erfolgreich, Python-Compile, JavaScript-Syntax und `git diff --check` sauber.
  Produktions-Gate mit acht aktiven Nutzern: keine Zukunftsaktion offen, Score 0-100,
  keine History-Rewrites, Integrity ok, Foreign Keys und Cash Drift jeweils 0. Commit
  `1682f44` ist mit API, Bot und Frontend ausgerollt.

## Update 27.08.2026 - PIN grace period (deployed)

- Ein erfolgreich entsperrtes Geraet behaelt den serverseitigen PIN-Unlock jetzt fuer genau
  zwei Minuten ohne geschuetzte Aktivitaet. Aktive Nutzung aktualisiert den bestehenden
  sessiongebundenen Server-State weiter; ein Browser-Zeitstempel kann keinen Finanz-Endpunkt
  entsperren.
- Beim Wechsel in den Hintergrund sendet die PWA keinen Sofort-Lock mehr. Sie verdeckt die
  Finanzansicht sofort und fragt beim Zurueckkehren zuerst den Server. Innerhalb von zwei
  Minuten wird der State erneut geladen; danach erscheint der PIN-Screen ohne Finance-Flash.
- Logout, Passwort-Reset, Session-Widerruf, PIN-Lockout und Device-Isolation bleiben unveraendert.
- Verifikation: 30 PIN-/Frontend-/Monthly-Check-in-Tests erfolgreich, Python-Compile,
  JavaScript-Syntax und `git diff --check` sauber. Commit `69c0c80` ist mit API und Frontend
  ausgerollt; API Healthcheck, Integrity und Foreign Keys sind gruen.

## Update 27.08.2026 - Monthly close due-date and enrollment correction (lokal, noch nicht deployt)

- Ein Monatsabschluss wird ausschliesslich fuer vollstaendig vergangene Monate angeboten; der
  laufende Monat ist auch am Monatsende nie parallel als abgeschlossen markiert. Die bevorzugte
  Semantik ist der erste App-Start im Folgemonat.
- Bei mehreren offenen, juengeren Monaten zeigt Rov.E nur den aeltesten sinnvollen Rueckstand.
  Der API-Endpunkt akzeptiert zudem nur genau den aktuell durch den Server angebotenen Monat;
  alte Tabs oder manipulierte Requests koennen keinen laufenden oder zweiten Monat schliessen.
- Beim ersten Einsatz wird ein Nutzer auf den laufenden Monat eingeschrieben. Dadurch entstehen
  durch die Einfuehrung der Funktion keine kuenstlichen historischen Monatsabschluesse; der
  erste moegliche Abschluss ist erst der eingeschriebene, vollstaendig vergangene Monat.
- Verifikation lokal: 13 gezielte Monthly-Check-in-Tests erfolgreich, Python-Compile und
  `git diff --check` sauber. Kein Deploy erfolgt.

## Update 27.08.2026 - Quick capture close control (lokal, noch nicht deployt)

- Das Schnellerfassen-Sheet hat jetzt oben rechts ein sichtbares X mit einer 40x40-Pixel-
  Touch-Flaeche. Es verwendet dieselbe bestehende Schliessfunktion wie der Hintergrundklick;
  eine Eingabe wird dadurch nicht gespeichert oder veraendert.
- Hintergrundklick bleibt erhalten. Auf Desktop schliesst Escape ausschliesslich das geoeffnete
  Schnellerfassen-Sheet.
- Verifikation lokal: 2 gezielte Schnellerfassen- und 13 Monthly-Check-in-Tests erfolgreich,
  JavaScript-Syntax und `git diff --check` sauber. Kein Deploy erfolgt.

## Update 27.08.2026 - Coach suppresses non-actionable savings reminder (lokal, noch nicht deployt)

- Eine geplante Sparquote ab 20 Prozent bleibt bis zum Monatsabschluss bewusst als unbestaetigte
  Score-Umsetzung bewertet. Sie erscheint aber nicht mehr taeglich als `Naechster Hebel`, solange
  der Nutzer den laufenden Monat noch gar nicht abschliessen kann.
- Niedrige Sparraten und bereits faellige beziehungsweise bestaetigte Sparraten bleiben weiterhin
  normale Coach-Signale. Score, Monatsabschluss, Buchungen und Datenmodell bleiben unveraendert.
- Verifikation lokal: 4 gezielte Coach-/Schnellerfassen-Tests, JavaScript-Syntax und
  `git diff --check` erfolgreich. Kein Deploy erfolgt.

## Update 27.08.2026 - Feature Announcements Sprint 2 (deployed)

- Die bestehende Aktivitaetsglocke kombiniert ihren lokalen Ungelesen-Status jetzt mit der
  serverseitigen Announcement-Wahrheit. Normale Aktivitaeten und Fixkosten bleiben vollstaendig
  erhalten; ein Announcement wird erst beim tatsaechlich geoeffneten und gerenderten Feed als
  gesehen markiert, niemals bereits beim App-Start.
- Ein oder zwei neue Funktionen erscheinen als kompakte Einzelkarten. Ab drei Neuigkeiten zeigt
  die Glocke eine ruhige Sammelkarte, die das neue `Was ist neu?`-Archiv oeffnet. Das Archiv ist
  auch in den Einstellungen erreichbar, zeigt hoechstens zehn Eintraege der letzten 90 Tage und
  sortiert konsequent nach Aktualitaet.
- Announcement-Taps verwenden ausschliesslich eine feste Deep-Link-Liste fuer Rov.E-Ansichten.
  Kurze Beispiele und Schrittfolgen erscheinen in einem eigenen mobilen Detail-Sheet; ungueltige
  Ziele bleiben sicher im Detail. Seen, Opened und Dismissed laufen ueber die vorhandenen
  session- und PIN-geschuetzten Sprint-1-Endpunkte.
- Coach, Push, Score und Finanz-Schreibpfade bleiben unveraendert. Verifikation lokal: 52
  Announcement-/PIN-/Monthly-Check-in-/Schnellerfassen-/Coach-Tests erfolgreich, JavaScript- und
  Python-Syntax sauber. Commit `8b330b6` ist mit Frontend und serverseitigen Tests ausgerollt.

## Update 27.08.2026 - Feature Announcements initialer Release (lokal, noch nicht deployt)

- Die Sprint-1/Sprint-2-Infrastruktur veroeffentlicht absichtlich keine Inhalte beim Schema-Setup.
  Ein expliziter, idempotenter Publisher legt deshalb die vier initialen Release-Definitionen
  (Rov.E AI, Crypto Tracking, Top-Haendler und Monatscheck) genau einmal an.
- Der Publisher erzeugt keine nutzerspezifischen State-Zeilen. Sein Zeitpunkt ist zugleich der
  Release-Zeitpunkt: bestehende Accounts sehen die Neuigkeiten, zukuenftige Accounts werden nicht
  mit alten Releases ueberflutet. Ein wiederholter Lauf behaelt die urspruenglichen Zeitstempel bei.

## Update 27.08.2026 - Feature Announcements isolation bugfix (lokal, noch nicht deployt)

- `seen` bleibt ausschliesslich ein Lesestatus. Gesehene Neuigkeiten bleiben als eigenstaendige
  Karten sichtbar, bis genau diese Funktion geoeffnet, entfernt oder fachlich abgeschlossen ist.
  Der Glockenpunkt folgt offenen, noch nicht geoeffneten Neuigkeiten und bleibt daher fuer eine
  zweite unabhaengige Karte bestehen.
- Crypto- und Monatscheck-Tutorials sind jetzt explizit an ihre jeweilige `feature_id` gebunden.
  Dadurch kann ein Monatscheck weder Crypto-Schritte noch dessen Zielansicht uebernehmen.

## Update 27.08.2026 - Feature Announcements bell auto-close (lokal, noch nicht deployt)

- Ein Announcement markiert weiterhin nur seine eigene `feature_id` als geoeffnet. Vor einem
  erfolgreichen Deep Link werden ausschliesslich Glocke, Neuigkeiten-Archiv und Announcement-
  Detail geschlossen; danach ist die Zielansicht ohne verdeckendes Overlay sichtbar.
- Die einheitliche Zielnavigation gilt fuer alle allowlisted Ziele einschliesslich Top-Haendler,
  Monatscheck, Crypto und Rov.E AI. Sie schreibt keine weiteren Announcement-States und greift
  weder in Finanzdaten noch in den normalen Activity-Feed ein.

## Update 27.08.2026 - Feature Announcements Sprint 3 Coach Integration (lokal, noch nicht deployt)

- Die serverseitige Announcement-State-Tabelle erhaelt additiv und rerunnable `coach_shown_at`.
  Eine transaktionale Auswahl beansprucht pro Nutzer hoechstens eine Security- oder Major-
  Neuigkeit; Reloads und weitere Geraete koennen denselben Hinweis nicht erneut beanspruchen.
- Faellige Income-, ETF- oder Monatsabschluss-Aktionen bleiben vor jeder Neuigkeit. Danach gilt
  Security vor Major, jeweils mit dem neuesten relevanten Eintrag; Minor-Updates bleiben nur in
  Glocke und `Was ist neu?`. Bereits gesehene, geoeffnete, entfernte, abgeschlossene oder durch
  echte Nutzung erledigte Funktionen sind fuer den Coach ausgeschlossen.
- Der Coach zeigt maximal zwei kurze Zeilen und `Ausprobieren`, verwendet fuer Klicks exakt die
  bestehende Sprint-2-Deep-Link-Logik und setzt durch die reine Anzeige niemals `opened_at`.
  Ein einmaliger dezenter Pulse wird bei `prefers-reduced-motion` vollstaendig deaktiviert.
- Verifikation lokal: 116 Announcement-/Monthly-/Coach-/AI-/Crypto-/Report-Regressionstests
  erfolgreich, Python-Compile, JavaScript-Syntax und `git diff --check` sauber. Kein Deploy erfolgt.

## Update 27.08.2026 - Finaler Fix vor Sprint-3-Gate (lokal, noch nicht deployt)

- Der prominente Announcement-Feed verwendet server- und clientseitig dieselbe Wahrheit:
  `seen` bleibt sichtbar, `opened`, `dismissed` und `completed` werden nur noch im Archiv gezeigt.
  Der Zaehler wird direkt aus genau dieser prominenten Liste gebildet.
- Der Crypto-Deep-Link oeffnet mit vorhandenem Bestand die Verwaltung und ohne Bestand den
  bestehenden Hinzufuegen-Flow. Der Monatscheck zeigt ohne faellige Aktion den ruhigen Zustand
  `Aktuell ist nichts faellig.` und fuehrt dabei keine Finanzaktion aus.
- Die Budget-Wahrheit ist serverseitig in Kategorie-Rest und gesamten Monatsplan-Rest getrennt.
  Fixkosten, Sparrate, Transfers und Investments werden nicht doppelt gezaehlt. Der Coach benennt
  beide Werte eindeutig und behauptet bei noch offenen Budgettoepfen nicht mehr, der Nutzer sei
  ueber seinem Budget. Score und History verwenden weiterhin ihren bisherigen Eingang.
- Verifikation lokal: 142 kombinierte Announcement-/Monthly-/Crypto-/Finanzkonto-/ETF-/Coach-Tests
  erfolgreich; Python-Compile, alle JavaScript-Bloecke und Scope-Diff sauber. Kein Deploy erfolgt.

## Update 28.08.2026 - Home Screen Redesign Sprint 1 (lokal, noch nicht deployt)

- Die Home-Uebersicht nutzt jetzt eine ruhigere Material-Hierarchie: Chart und Vermoegensliste
  erhalten getrennte, dezente Glasflaechen mit silbernen Kanten, feiner Innenlichtkante und
  kontextgerechter Tiefe.
- Die Vermoegenswert-Icons sind als eigene, kompakte Material-Badges ausgearbeitet. Werte,
  Reihenfolge, Klickziele und bestehende aktive Kartenlogik bleiben unveraendert.
- Die Bottom-Navigation, Finanzberechnung, API-/State-Pfade, Coach, Ziele, Analyse, Vertraege,
  Score und Reports wurden nicht geaendert. Nur Home-CSS in `rove-app/index.html` wurde angepasst.

## Update 28.08.2026 - Home Icon Reference Polish (lokal, noch nicht deployt)

- Die bestehenden Home-SVGs fuer Girokonto, Tagesgeld, Investments, Krypto und Immobilie bleiben
  die funktionale Quelle, erhalten aber ausschliesslich auf Home ein gemeinsames dunkles Gloss-
  Material mit silberner Kontur, feiner Innenkante und groesserer, klarerer Linienfuehrung.
- `ETF & Investments` wird nur in der Home-Liste als `Investments` angezeigt. Der kanonische
  Name fuer Asset-Schluessel, Details, API und Finanzzuordnung bleibt unveraendert.
- Der Chart-Container hat am unteren Rand mehr Luft. Keine Navigation, Interaktion, Berechnung
  oder sonstige UI ausserhalb der Home-Oberflaeche wurde angepasst.

## Update 28.08.2026 - Home Coach Polish (lokal, noch nicht deployt)

- Die Home-Coaching-Leiste verwendet jetzt dieselbe ruhige dunkle Glas-Hierarchie wie Chart und
  Vermoegensliste: eine dezente Silberkante, weiche Rundung, Innenlicht und kontrollierte Tiefe.
  Das Face-Icon und der bestehende Interaktionshinweis sind als kleine, neutrale Materialelemente
  ausgearbeitet; Blau bleibt auf gezielte Text-Akzente begrenzt.
- Ausschliesslich Home-CSS wurde geaendert. Coach-Prioritaet, Inhalte, Klickverhalten, Deep Links,
  Announcement-State, Animationen und alle Finanz-/API-/State-Pfade bleiben unveraendert.

## Update 28.08.2026 - Home Icon Silhouettes (lokal, noch nicht deployt)

- Die Home-Asset-Liste verwendet jetzt fuer Girokonto, Tagesgeld, Cash, Investments, Krypto und
  Immobilie ein eigenes, konsistentes Linien-SVG-Set nach der freigegebenen dunklen Premium-
  Referenz. Es ist auf Home begrenzt; Details, Onboarding und alle anderen Ansichten behalten ihre
  bestehenden Icon-Quellen.
- Die neue `homeAssetIcon()`-Zuordnung ist ausschliesslich eine Darstellungsschicht. Asset-Namen,
  Schluessel, Werte, Klickziele und alle Finanz-/State-Pfade bleiben unveraendert.
- Investments und Krypto verwenden darin bewusst reduzierte, grosszuegige Linienformen mit mehr
  Innenabstand, damit sie in der mobilen Icon-Kachel nicht gedrungen oder verzogen wirken.
- Das Home-Krypto-Icon verwendet nach visueller Pruefung wieder das zuvor bewaehrte Rov.E-
  Bitcoin-Symbol. Dadurch bleibt die neue Home-Kachel erhalten, ohne eine doppelte Bitcoin-Marke.

## Update 28.08.2026 - Goals Design Sprint V1 (lokal, noch nicht deployt)

- Der Ziele-Bereich ist als eigene, auf `#tab-ziele` begrenzte Materialschicht veredelt: dunkle
  Glass-Karten, silberne Kanten, ruhige Innen-Tiefe, einheitliche Icon-Badges und klarere
  Betrags-/Fortschritts-Hierarchie. Die Progressbar zeigt denselben Wert, nur in einer
  zur Home-Oberflaeche passenden, gedeckten silber-blauen Darstellung.
- CTA und Empty State folgen derselben visuellen Ordnung. Goal-Daten, Berechnung, Fortschritt,
  Sparrate, API, State, Routing, Navigation und alle anderen Screens bleiben unveraendert.

## Update 28.08.2026 - Goals Fix und Cashflow Depth Polish (lokal, noch nicht deployt)

- Goal-Icons haben keine dekorative Animation mehr. Ihre bisherigen individuellen Tints werden im
  Ziele-Screen in ein einheitliches dunkles Icon-Material ueberfuehrt; Progress bleibt der einzige
  kleine, semantische Farbakzent. Damit kann keine einzelne Goal-Card gruenlich aus dem Set fallen.
- Cashflow behaelt alle vorhandenen blauen Statusflaechen, Filter, Plan- und Budgetinhalte. Nur
  deren Oberflaechen erhalten lokal auf `#tab-tx` feinere Kanten, Innenlicht und dunkle Tiefe.
  Merchant-Logos, Layout, Daten, Filter und Interaktionen bleiben unveraendert.

## Update 28.08.2026 - Analyse Polish (lokal, noch nicht deployt)

- Bekannte Haendler behalten in der Analyse weiterhin ihr vorhandenes Markenlogo. Fuer freie oder
  generische Buchungen verwendet die reine Darstellungshilfe `analysisMerchantLogo()` stattdessen
  das vorhandene Kategorien-SVG in einem neutralen Rov.E-Glass-Badge. Daten, Haendlerzuordnung,
  Kategorien und Aggregation bleiben unveraendert.
- Analyse-Tiles, Kategorien, Listen, Filter und Badges sind ausschliesslich innerhalb von
  `#tab-analysis` an die dunkle Premium-Materialitaet angeglichen. Die Kategorie-Detailansicht
  erhaelt dieselbe Oberflaeche; Home, Cashflow, Navigation und alle funktionalen Pfade bleiben
  unveraendert.

## Update 28.08.2026 - Monatsplan und Cashflow Material Polish (lokal, noch nicht deployt)

- Der Monatsplan verwendet jetzt eigene, auf `#monthlyplansheet` begrenzte Glas-Karten mit klarer
  Hierarchie fuer Status, Betraege und vorhandene Aktionen. Fällige Monatsabschluss- und ETF-
  Aktionen werden nur visuell durch eine dezente kuehl-blaue Materialebene hervorgehoben; ihre
  Fälligkeit, Inhalte und Klickpfade bleiben unveraendert.
- Cashflow-Listen und Filter sind nur innerhalb von `#tab-tx` veredelt. Einnahmen, Ausgaben und
  Budget-Filter erhalten kleine gedeckte Gruen-, Rose- und Amber-Akzente; die grossen Flaechen
  bleiben dunkel und neutral. Buchungen, Budgets, Fixkosten, Suche, Filter und Berechnungen sind
  unveraendert.

## Update 28.08.2026 - Cashflow und Ausgaben Final Polish (lokal, noch nicht deployt)

- Cashflow-Belege behalten die semantische Gruen-/Rose-Orientierung ausschliesslich bei den
  Betraegen. Die Hauptkarten und Budget-Zusammenfassungen bleiben dunkle, neutrale Glass-Flächen
  mit feinen silbernen Kanten.
- Echte Händlerlogos in der Buchungsliste bleiben unverändert. Buchungen ohne Markenlogo erhalten
  dagegen eine einheitliche neutrale Rov.E-Glass-Huelle, ohne Daten, Kategorien oder den
  Buchungsablauf zu verändern.

## Update 28.08.2026 - Vertrags-Polish (lokal, noch nicht deployt)

- Der Verträge-Screen verwendet jetzt dieselbe dunkle Premium-Materialität wie Home und Goals:
  neutrale Fixkosten-Zusammenfassung, Glass-Karten, bessere Zeilenhierarchie und ruhige
  Statuschips. Die kleine Rosé-Markierung bleibt auf Vertragsbeträge und Warnungen begrenzt.
- Generische Vertragstypen behalten ihre vorhandenen Rov.E-Linien-SVGs, erhalten aber eine
  einheitliche silberne Glass-Hülle. Vorhandene Anbieterlogos werden weder ersetzt noch verändert.
  Vertragslogik, Legacy-Pfade, CRUD, Kündigung, Daten und Navigation bleiben unverändert.

## Update 28.08.2026 - Vertragsbeträge neutralisiert (lokal, noch nicht deployt)

- Normale Vertrags- und Fixkostenbeträge sind im Verträge-Screen jetzt hell silbern statt Rosé.
  Status- und Warnchips bleiben unverändert semantisch eingefärbt; Karten, Logos, Icons und alle
  funktionalen Pfade bleiben unverändert.

## Update 28.08.2026 - Final Design-System Rollout (lokal, noch nicht deployt)

- Aktivitaetscenter, Neuigkeiten, Einstellungen, Reports, Score, Mentor-Chat, Mentoring-Zugang
  und Admin verwenden jetzt jeweils eine lokal begrenzte dunkle Glass-Materialschicht mit feinen
  silbernen Kanten, ruhiger Innentiefe und klaren Statusakzenten.
- Bestehende Markenlogos, Announcement-States, Report-/Score-Inhalte, Chat-Bubbles, Admin-Aktionen
  und alle vorhandenen Klick- und Navigationspfade wurden nicht veraendert. Der Rollout ergaenzt
  ausschliesslich CSS in `rove-app/index.html`; JavaScript, API, State und Finanzlogik bleiben
  unveraendert.

## Update 28.08.2026 - Beta Feedback Crypto-Auswahl und PIN-Texte (lokal, noch nicht deployt)

- Beim manuellen Krypto-Hinzufuegen bleiben Menge, Einstandswert, Vorschau und Speichern jetzt so
  lange verborgen, bis ein konkreter Coin aus der Trefferliste ausgewaehlt wurde. Treffer besitzen
  eine eindeutige Auswahlaktion; der gewaehlte Coin erscheint danach als eigene Card mit
  `Ausgewaehlt`-Status und einer sichtbaren `Aendern`-Aktion.
- Eine neue Suche oder geaenderte Sucheingabe verwirft die vorherige Coin-Auswahl, Vorschau und
  Eingabeentwuerfe. Der bestehende CMC-ID-, Preview- und Portfolio-Speicherpfad wurde nicht
  veraendert; ohne gewaehlten Coin ist der Speicher-CTA nicht vorhanden und der bestehende Guard
  bleibt zusaetzlich aktiv.
- Der vorhandene sichere PIN-Recovery-Pfad bleibt technisch unveraendert. Angepasst wurden nur die
  sichtbaren Texte fuer `PIN vergessen?`, Passwortbestaetigung, den Zustand nach fuenf Fehlversuchen
  und den vorhandenen Passwort-Reset.
- Verifikation lokal: JavaScript-Syntax OK, `git diff --check` OK, 24 Crypto-Tests und 11 PIN-Tests
  erfolgreich. Mobile lokale Vorschau laedt ohne Console-Fehler. Kein Deploy erfolgt.

## Update 28.08.2026 - Crypto-Label Cleanup (lokal, noch nicht deployt)

- Normale Coin-Positionen zeigen innerhalb des Krypto-Bereichs nicht mehr redundant die Kategorie
  `Krypto`; die Meta-Zeile beginnt direkt mit Menge und Symbol. Der Seitentitel, interne Asset- und
  Type-Werte sowie gemischte Investmentbereiche bleiben unveraendert.
- Legacy-Positionen behalten den fachlich notwendigen Hinweis `Legacy · manuell`. Portfolio-, CMC-,
  Speicher-, Marktwert-, Routing- und andere Investmentpfade wurden nicht angepasst.
- Verifikation lokal: JavaScript-Syntax OK, `git diff --check` OK. Kein Deploy erfolgt.

## Update 28.08.2026 - CoinMarketCap Coin-Logos V1 (lokal, noch nicht deployt)

- `rove_market_data.py` reichert stabile CMC-IDs ueber einen einzigen gebuendelten
  `/v2/cryptocurrency/info`-Request mit optionalen Logo-Metadaten an. Ein prozessweiter Cache
  dedupliziert IDs und haelt positive wie fehlende Ergebnisse sieben Tage; Providerfehler liefern
  weiterhin den vollstaendigen Portfolio-State und beeinflussen weder Quotes noch Finanzwerte.
- `rove_app_state.py` gibt fuer getrackte Coin-Positionen optional `logoUrl` aus. Bestehende
  Holdings benoetigen keine Migration; IDs, Mengen, Marktwerte, Einstandswerte und P/L bleiben
  unveraendert.
- Coin-Zeilen zeigen das echte CMC-Logo in einer kompakten runden Flaeche. Fehlende, ungueltige
  oder im Browser nicht ladbare Bilder fallen ohne Broken-Image auf einen dunklen Symbol-Badge
  zurueck. Suche und Add-Flow erzeugen weiterhin keine Metadata-Calls pro Eingabe.
- Verifikation lokal: 29 Crypto-Tests erfolgreich, Python- und JavaScript-Syntax OK,
  `git diff --check` OK. Kein Deploy erfolgt.

## Update 28.08.2026 - Crypto Header-Logo und Edit-Polish (lokal, noch nicht deployt)

- Das Krypto-Summary erhaelt Bitcoin als optionale `headerLogoUrl` aus derselben gecachten
  CoinMarketCap-Metadatenquelle wie die Coin-Positionen. Die CMC-ID `1` wird nicht als Holding
  angelegt; dadurch erscheinen weder neue Positionen noch beeinflusst der Header Mengen, Werte,
  Einstand oder P/L. Ist das Logo nicht verfuegbar, bleibt das vorhandene Krypto-Symbol sichtbar.
- Bearbeitbare Coin-Zeilen verwenden einen kompakten SVG-Stift mit einem 42px Touch-Target statt
  des breiten sichtbaren Textes `Bearbeiten`. Die bestehende Edit-Aktion und die Wertdarstellung
  bleiben unveraendert.
- Verifikation lokal: 31 Crypto-Tests erfolgreich, Python- und JavaScript-Syntax OK,
  `git diff --check` OK. Kein Deploy erfolgt.

## Update 28.08.2026 - Contracts Press-Animation Fix (lokal, noch nicht deployt)

- Die globale `.card:active`-Regel konnte beim Tippen auf eine Vertragszeile den uebergeordneten
  Vertragsgruppen-Container skalieren. Die Gruppenkarte bleibt daher beim Press-Zustand starr;
  nur die direkt gedrueckte Zeile erhaelt eine kurze `scale(.99)`-Rueckmeldung ohne Translation
  oder Layout-Aenderung.
- Verifikation lokal: JavaScript-Syntax OK, `git diff --check` OK. Kein Deploy erfolgt.
