# Rov.E Arbeitsstand – 22.07.2026

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

### Nächster Arbeitsschritt: Zahltag (vereinbart 27.07.)

Es gibt nirgends ein `payday`-Feld — nicht in `bot.py`, nicht im API, nicht im State. Der
Monatscheck öffnet sich deshalb pauschal am **1.** eines Monats. Wer später Gehalt bekommt,
bestätigt zu früh oder muss ihn selbst öffnen. **Furkan bekommt am 15.** und hat den Punkt
ausdrücklich als „muss auf jeden Fall rein" bestätigt.

Ein `payday`-Block war am 27.07. bereits gebaut (`users.payday`, plus `faellig`/`gebucht` im
State) und **bewusst wieder entfernt**, weil sich herausstellte, dass der Monatscheck den Auslöser
schon abdeckt. Wer das wieder aufgreift, sollte wissen: die Erkennung „Gehalt schon gebucht?" lässt
sich aus den Bewegungen ableiten (Einnahme dieses Monats mit Label nach Gehalt klingend ODER
Betrag ≥ 50 % des erwarteten Gehalts) — ein eigenes Merker-Feld braucht es nicht und wäre
fehleranfällig, weil es von der Wahrheit abweichen kann.

Offene Entwurfsfragen: Woher kommt der Tag (App fragt einmal? eigener Endpunkt? Onboarding?), was
bei mehreren Einkommensquellen mit verschiedenen Terminen, und was der Monatscheck vor dem Zahltag
anzeigt.

**Kein Termindruck mehr:** Furkan am 27.07. — der Bot kann bei Bedarf länger laufen als bis zum
01.08. Qualität geht vor Abschalttermin.

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

## Telegram-Abschaltung: Termin steht

Der Bot wird abgeschaltet, sobald alle Beta-Nutzer in der App sind — aber **frühestens ab dem
01.08.2026**, und die Migration steuert Furkan selbst und bewusst. Bis dahin bleibt `bot.py` in
vollem Betrieb und darf nicht als „tot" behandelt werden.

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
