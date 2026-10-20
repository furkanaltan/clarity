# Mehrkonten: lokale Stabilisierung und nutzerweiser Uebergang

Stand: 20.10.2026. Keine Production-Aktion. Keine allgemeine Freigabe.
Grundlage: der abgeschlossene Audit unter
`/Users/furkanaltan/Documents/Codex/rove-multi-account-reconciliation-20261020/AUDIT.md`.

## Umgesetzter Umfang

- Ein frisches App-Onboarding initialisiert Konten, vier Zahlungsrollen, Legacy-Aggregate
  und Modellmarkierung in derselben Transaktion. Ein Girokonto mit bekanntem Nullsaldo
  bleibt als Zahlungsziel vorhanden; leere Tagesgeld-/Wallet-Konten werden nicht erfunden.
- Die erste Anlage eines bisher fehlenden Kontotyps erhaelt dessen Kompatibilitaetsbindung.
  Insbesondere kann das erste Wallet fuer Barzahlungen benutzt werden.
- Kontoanlage verlangt `request_id` (maximal 128 Zeichen). Dieselbe Nutzer-ID und derselbe
  Schluessel mit gleichem Inhalt liefern dieselbe Konto-ID. Geaenderter Inhalt ergibt 409.
  Anlage und Quittung werden gemeinsam committed. Der Client behaelt einen unbestaetigten
  Versuch im Arbeitsspeicher, blockiert geaenderte Angaben und doppelte Klicks und sendet
  die bereits vorhandene Nutzerkennung als Header. Keine neue Browserpersistenz.
- Anzeige und Buchungen gleichen die protokollierten Quellen ab. Widerspruechliche Legacy-
  Aufteilung/current_cash oder aktive Konten/Spiegel ergeben `cash_reconciliation_required`
  (HTTP 409). Es wird weder eine Differenz ins Giro gerechnet noch eine Quelle gewonnen.
- Bot-Bearbeitung und Rueckbuchung nutzen die gespeicherte urspruengliche Konto-ID,
  auch nach Rollenwechsel. Der alte Bot-Schreibweg fuer `current_cash` ist fuer das aktive
  Kontenmodell gesperrt. `/investiert` verweist vor einer Mutation auf den bestehenden
  Monatscheck in der App; die App-Sparlogik wurde nicht geaendert.
- Der alte Tester-Helfer oeffnet Dry-Runs mit `mode=ro` und `query_only`; fehlendes Schema
  wird gemeldet. Sein unsicherer pauschaler Apply-Einstieg ist stillgelegt.

## Additives Schema und Modellwahl

`app_financial_account_requests` speichert nutzergebundene Anlagequittungen mit Payload-Hash
und Konto-ID. `app_financial_account_migrations` markiert ein vollstaendig initialisiertes
oder explizit abgestimmtes Modell. Beide Tabellen sind ueber Nutzer-FKs gebunden und werden
bei der bestehenden Kontoloeschung entfernt. Keine bestehende Tabelle wird neu aufgebaut.

Die Modellmarkierung ist keine Pilotberechtigung. Neue Nutzer erhalten sie im Onboarding;
Bestandsnutzer erst nach geprueftem Uebergang. Der alte Feature-Schluessel wird als technischer
Kompatibilitaetsspiegel mitgefuehrt, damit unveraenderte Score-/Report-Leser denselben Cashwert
verwenden. Ein abgeschlossenes Modell darf nicht per Flag-off auf veraltete Daten wechseln.
Bestandsnutzer werden beim normalen Lesen weder migriert noch pauschal aktiviert.

## Lesende Live-Inventur

`reconcile_financial_accounts.py --db <pfad>` oeffnet ausschliesslich eine lesende SQLite-
Verbindung mit konsistenter Lesetransaktion. Es importiert weder API noch Bot, laedt keine
ENV-Dateien und ruft keine Schema-Helfer auf. Die Standardausgabe enthaelt nur Klassenanzahl,
Konfliktcodes, Zugangsstatus-Anzahlen und Integritaetsergebnisse. Keine Namen, E-Mails,
Kontostaende, Zugangsdaten oder Codes werden ausgegeben.

Ein separat erzeugtes, eigenstaendiges Inventurartefakt erlaubt dieselbe Pruefung ohne
Austausch produktiver Python-Module. Es ist fuer die spaetere manuelle VPS-Ausfuehrung
vorbereitet, wurde aber nicht mit Production verbunden. Bei laufendem WAL werden dessen
committete Daten beruecksichtigt; `immutable=1` ist auf der Live-DB ausdruecklich ungeeignet.

## Minimaler Uebergang je Nutzer

1. Inventur nach `CONSISTENT_LEGACY`, `CONSISTENT_FINANCIAL`, `REVIEW_REQUIRED` und
   `BLOCKED_INTEGRITY` einordnen. Keine Testkonten loeschen und keine Aktivitaet vermuten.
2. `--plan-user <id>` erzeugt fuer einen konsistenten Nutzer einen konkreten Quellenplan
   mit Konto-IDs, vorhandenen Rollen und Vorher-Pruefsumme. Fehlende Zahlungsrollen duerfen
   nur ueber ein vorhandenes aktives Legacy-Giro im expliziten Plan ergaenzt werden.
   Bei widerspruechlichen Betraegen entsteht kein anwendbarer Plan: Betreiberentscheidung.
3. Plan auf synthetischen Daten bzw. einer separat freigegebenen Kopie pruefen. Historische
   Snapshots, Buchungen, Budget, Vermoegensformel, Schulden und Immobilien-Eigenkapital
   bleiben erhalten. Ein Gesamtbetrag ohne Aufteilung wird im Plan ausdruecklich als Giro
   vorgeschlagen; die Anwendung verlangt die Freigabe dieser konkreten Pruefsumme.
4. Die separat aufrufbare Funktion `apply_user_plan` verlangt die genehmigte Vorher-Summe
   innerhalb einer vom Aufrufer gehaltenen Schreibtransaktion. Sie prueft den Plan erneut,
   nutzt einen eigenen Savepoint und prueft geschuetzte Finanzzeilen vor/nach. Es gibt
   keinen CLI-Massen-Apply. Fuer eine spaetere Live-Anwendung ist ein neuer gesonderter
   manueller Ablauf mit frischem Backup erforderlich.
5. Ein unveraendert wiederholter Plan erzeugt nichts neu. Aenderungen seit Planung oder
   seit dem bestaetigten Abschluss lehnen den alten Plan ab. Andere Nutzer bleiben gleich.

Die Live-Klassenzahlen und konkrete Konfliktentscheidungen stehen noch aus. Schaetzungen
zur Kontenzahl oder Aktivitaet sind kein Nachweis und erlauben keine Datenkorrektur.

## Spaetere Release- und Rollback-Grenzen

Sachwerte-UX und Mehrkonten haben getrennte Commits. Kein gemischter Release-Kandidat.
Erst nach Bestandsabnahme wird auf der dann frisch geprueften Produktionsbasis isoliert.
Fuer Kontoanlage benoetigt das Frontend den neuen Request-Schluessel: Backend zuerst,
danach kompatibles Frontend. Ein noch altes Frontend kann waehrenddessen keine neue
Kontoanlage ohne Schluessel ausfuehren (400 statt ungeschuetzter Doppelanlage).

Nach der ersten neuen kanonischen Buchung darf ein Code-Rollback das alte Legacy-Modell
nicht reaktivieren. Kein DB-Restore, keine alten Salden, keine Tabellenloeschung. Ein
Rueckweg muss die inzwischen angelegten Konto-IDs und das kanonische Schreibmodell
weiter unterstuetzen; gegebenenfalls nur die weitere Kontoanlage sperren.

Keine Login-Architektur, Report-Renderer, Report-Zustellung, Score- oder Budgetformel,
Vermoegenshistorie oder untere Tab-Leiste wurden fuer diesen Umfang veraendert.
