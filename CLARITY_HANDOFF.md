# Clarity Handoff fuer neuen Lead Developer / Agent

> **WICHTIG - HISTORISCHE LANGDOKUMENTATION:** Viele Aussagen in dieser Datei stammen aus
> der Telegram-/Clarity-Phase und sind nicht mehr der aktuelle Produktstand. Verbindlicher
> App-first-Snapshot vom 14.08.2026:
> `../ROVE_STATUS_2026-07-22.md`, Abschnitt
> `AKTUELLER HANDOFF-SNAPSHOT FUER CLAUDE/CODEX`.
>
> Heute ist Rov.E App-first, besitzt zentrale App-only-E-Mailkonten, Admin-Einladungen,
> Cashflow/Screenshot-Import, Monatsplanung, Reports, Push, Analysen und persistente
> Investments. Telegram ist nur noch Uebergang. Bank-Anbindung, Stripe und echte dynamische
> Mehrfachkonten sind noch nicht live. Bei Widerspruechen gilt immer der aktuelle Snapshot
> und danach der tatsaechlich gelesene Code.

## 1. Kurzfassung

Clarity ist ein Telegram-basierter persoenlicher Finanzassistent. Nutzer tragen Ausgaben und Profiländerungen natuerlich per Chat ein, zum Beispiel:

- `Lidl 34€`
- `Miete jetzt 850€`
- `Kredit ist abbezahlt`
- `10k in Crypto investiert`

Clarity speichert diese Daten in SQLite, beantwortet Finanzfragen auf Basis des Nutzerprofils und erzeugt monatlich einen hochwertigen 10-seitigen PDF-Finanzreport.

Das Produkt ist bewusst keine Banking-App und kein klassisches Haushaltsbuch. Clarity ist ein System fuer finanzielle Selbstreflexion, Kontrolle, Motivation und Vermoegensaufbau.

Der Nutzer soll das Gefuehl haben:

> Ich habe mein Geld im Griff.

## 2. Mission und Produkt-DNA

Clarity soll sich ruhig, hochwertig und vertrauensvoll anfuehlen. Kein Gaming-Look, kein lauter Coach, kein App-Gelaber. Eher wie ein diskreter Finanzbegleiter.

Grundprinzipien:

- Clarity erklaert nicht endlos, sondern behaelt den Ueberblick.
- Antworten sollen kurz, klar, menschlich und hilfreich sein.
- Keine erfundenen Daten.
- DB-first.
- KI darf unterstuetzen, aber nicht die Datenlage ersetzen.
- Nutzer sollen moeglichst natuerlich schreiben koennen.
- Das Premium-Erlebnis entsteht vor allem durch den monatlichen Report.

Langfristige Vision:

- Bot = Produkt.
- Report = monatlicher Wow-Moment.
- Cockpit = Kontrollzentrum fuer Betrieb und Nutzerstatus.
- Agent = Operations- und Support-Assistent.
- Gruender/Admin steuert das System wie eine One-Man-Army.

## 3. Aktueller Code-Stand

> **Aktualisierung 22.07.2026 – Rov.E App-first:** Der bisherige Telegram-Bot bleibt
> fuer die Beta aktiv, aber Rov.E entwickelt sich zur App-first-Plattform. Die Marke lautet
> immer `Rov.E`. Bestehende Telegram-Daten sind die Quelle der Wahrheit und werden in die
> Web-App gespiegelt. Neue App-Daten werden nur dann als "gespeichert" bezeichnet, wenn sie
> zentral in derselben SQLite-Datenbank liegen.

Wichtige lokale Projektdateien:

- `bot.py`
- `report_engine.py`
- `report_html_renderer.py`
- `report_html/report-main/index.html`
- `report_html/report-main/style.css`
- `report_html/report-main/pages/*.html`

Server-Stand:

- Produktivordner: `/root/clarity`
- Branch: `feature_clarityr-report`
- Bot laeuft ueber `systemd` als `clarity-bot`
- Neustart: `systemctl restart clarity-bot`
- Status: `systemctl status clarity-bot --no-pager`
- Logs: `journalctl -u clarity-bot -f`

Lokaler Main-Ordner fuer Git/Deployment:

- `/Users/furkanaltan/Documents/Project Clarity/Calrity_Main`

Arbeitsordner fuer Codex:

- `/Users/furkanaltan/Documents/Codex/2026-06-07/last-login-sun-jun-7-17/work/Calrity_Main`

### Rov.E Web-App (Stand 22.07.2026)

- Live-App: `https://getrove.de/app/`
- Lokale App-Datei: `/Users/furkanaltan/Documents/Codex/rove-app/index.html`
- Arbeitskopie: `work/rove-app/index.html`
- App-API: `rove_app_api.py`, eigener systemd-Service `rove-app-api`, intern Port `5057`.
- nginx-Route: `/app-api/`; statische App-Dateien: `/var/www/getrove/app/`.
- Bot und App verwenden dieselbe produktive `clarity.db`. App-Ausgaben erscheinen deshalb im
  Bot, Budget und Report; Bot-Ausgaben erscheinen beim App-Refresh.
- Beta-Zugang: Telegram erstellt per `/app` einen privaten State-Link. Der Link wird auf dem
  Geraet lokal hinterlegt; ein dauerhaftes App-Konto mit E-Mail-Einmalcode folgt erst nach der
  Stabilitaetsphase.

### Zentrale App-Daten (keine Demo-Werte)

- `expenses`: Ausgaben aus Bot und App.
- `category_budgets`: Budgets und Umbuchungen.
- `app_account_balances`: Girokonto, Tagesgeld, Bargeld.
- `app_properties`: Immobilienwert, Restschuld und zugehoerige Werte.
- `investment_events` / `portfolio_snapshots`: ETF-, Aktien- und Krypto-Korrekturen.
- `app_goals`: zusaetzliche App-Ziele wie Notgroschen. Anlegen, Zuweisen, Zielbetrag aendern
  und Loeschen sind zentral gespeichert. Das Telegram-Hauptziel bleibt getrennt.
- `app_contracts`: neue Verträge aus der App. Sie werden in `fixed_costs_details.app_vertraege`
  gespiegelt, sofort in Fixkosten/Budget/Report eingerechnet und im Bot mit echtem Namen statt
  technischer ID angezeigt.
- `app_monthly_plan_status`: Monatscheck-Bestaetigungen fuer Einkommen, Fixkosten und Sparrate.

### Ehrliche Produktgrenzen (nicht als fertig verkaufen)

- Sachwerte wie Auto, Gold oder Uhren sind aktuell nur auf dem jeweiligen App-Geraet gespeichert
  und noch nicht Teil des zentralen Report-Nettovermoegens. Die App kennzeichnet sie deshalb als
  `auf diesem Gerät`.
- Die Glocke zeigt reale anstehende Abbuchungen und echte Nutzeraktionen. Sie darf keine
  Demo-Abbuchungen oder erfundene Investment-Ausfuehrungen zeigen.
- Automatische Kündigungserinnerungen und Kündigungsservice existieren noch nicht. Die App darf
  nur sagen, dass der Service spaeter kommt.
- Bestehende Telegram-Verträge werden in der App angezeigt, aber derzeit absichtlich nur im Bot
  geändert oder gelöscht. Das verhindert einen irrefuehrenden lokalen Schein-Delete.
- Keine Bank-, Broker- oder Krypto-API und keine aktive Stripe-Zahlung vor Stabilitaetsphase.

### Aktuelle Prüfpriorität

1. `/testreport 2026-07` mit echten Beta-Daten erzeugen und Fixkosten, Ausgaben, Vermoegen,
   Zielprognose, PDF und Weblink gegenpruefen.
2. Am 01.08. echten Monatsjob, PDF, Weblink und App-Archiv pruefen.
3. Danach API-Sicherheit, Backups, Logging und dauerhaftes App-Konto priorisieren.

## 4. Tech Stack

Backend:

- Python
- SQLite
- Telegram Bot API ueber `telebot`
- OpenAI API fuer KI-Fallback und Finanzfragen
- APScheduler fuer Monatsreport-Jobs
- WeasyPrint fuer HTML-to-PDF-Reports
- Report HTML/CSS mit Manrope-Font

Wichtige Env Vars:

- `TELEGRAM_TOKEN`
- `OPENAI_API_KEY`
- `CLARITY_DB_NAME`, default `clarity.db`
- `ADMIN_USER_ID` oder `ADMIN_USER_IDS`
- `CLARITY_USER_APPROVAL`
- `REPORT_SEND_WINDOW_START_HOUR`, default 8
- `REPORT_SEND_WINDOW_END_HOUR`, default 14
- `REPORT_WORKER_BATCH_SIZE`, default 1
- `REPORT_WORKER_INTERVAL_SECONDS`, default 10
- `REPORT_MAX_ATTEMPTS`, default 3

## 5. Datenbank und Tabellen

SQLite ist der zentrale Zustand.

### `users`

Finanzprofil pro Nutzer.

Enthaelt:

- Einkommen
- Nebeneinkommen
- Fixkosten
- Sparziel
- Zielbetrag
- aktuelle Investments
- Cash
- ETF-Sparrate
- Cash-Sparrate
- Clarity Points
- Streaks
- Onboarding-Step
- aktueller Monat
- `fixed_costs_details` als JSON fuer detaillierte Fixkosten

### `expenses`

Einzelne Ausgaben.

Felder:

- `user_id`
- `amount`
- `category`
- `merchant`
- `description`
- `created_at`

### `user_badges`

Badges, Monatsmomente und interne Marker.

Wird auch fuer einmalige Momente verwendet, zum Beispiel:

- Report-Seed-Moment nach genug Eintraegen
- monatliche Investment-Bestaetigung
- Badges

### `monthly_snapshots`

Monatsabschluss mit:

- Score
- Ausgaben
- Budgetstatus
- Nettovermoegen

### `score_history`

Taegliche Score-Historie fuer Proof-/Datenbasis.

### `investment_events`

Investment-Historie.

Wichtige Felder:

- `direction`
- `asset_type`
- `asset_name`
- `event_type`
- `source`

Wichtig fuer spaeteren Report und Investment-Verlauf.

### `portfolio_snapshots`

Manuelle oder automatische Vermoegensstaende.

Scopes:

- `investments`
- `cash`
- `net_worth`

### `report_jobs`

Queue fuer Monatsreports.

Status:

- `pending`
- `processing`
- `sent`
- `failed`
- `skipped`

### `user_access`

Freigabesystem fuer Testnutzer.

Status:

- `pending`
- `approved`
- `revoked`

## 6. Bot-Funktionen

### Onboarding

Startet mit `/start`.

Fragt ab:

- Einkommen
- Nebeneinkommen
- Sparziel
- Zielbetrag
- aktuelle Investments
- Cash
- ETF-Sparrate
- Cash-Sparrate

Wichtig:

- Fixkosten-Gesamtfrage wurde bewusst entfernt, weil Nutzer ihre Gesamtfixkosten oft nicht kennen.
- `/zurueck` oder `zurück` geht im Onboarding einen Schritt zurueck.
- Wenn Onboarding fertig ist, geht Nutzer in `STEP_NORMAL`.

### Profil verfeinern

Command: `/verfeinern`

5 Bereiche:

- Wohnen
- Mobilitaet
- Abos
- Versicherungen
- Kredite

Beispiele:

- Miete, Strom, Gas
- Auto, Tanken, Bahn
- Netflix, Spotify, Prime, Disney
- Haftpflicht, BU, Rechtsschutz, Autoversicherung
- Immobilie, Hausgeld, Hausverwalter

Parser:

- Versteht Label-vor-Betrag und Betrag-vor-Label.
- Beispiel: `Miete 400 Strom 69€`
- Beispiel: `400 Miete 69 Strom`
- Autoversicherung wird in Versicherungen eingeordnet, nicht Mobilitaet.

### Normale Ausgaben

Nutzer schreibt zum Beispiel:

- `Lidl 34€`
- `Tanken 60€`
- `Restaurant 20€`

Bot erkennt:

- Betrag
- Haendler
- Kategorie

Antwort rotiert ruhig:

- `Ist drin.`
- `Hab ich notiert.`
- `Erfasst.`
- `Ich hab's im Blick.`

Nach ca. 7 Ausgaben im Monat kommt einmalig ein leiser Wow-Moment: genug Daten fuer erstes klares Bild.

### Profiländerungen im Alltag

Hinzufuegen/Aendern:

- `Miete jetzt 850€`
- `Autoversicherung 105€ im Monat`
- `neuer Kredit 300€ im Monat`

Entfernen/Loeschen:

- `lösche Spotify`
- `Autoversicherung gekündigt`
- `Kredit ist abbezahlt`
- `entferne Handy`

Wenn `Kredit ist abbezahlt` mehrere Kredit-Eintraege betreffen koennte, fragt Clarity nach statt blind zu loeschen.

Fixkosten werden nach jeder Aenderung neu berechnet.

### Investments

Erkennt:

- ETF
- Aktien
- Crypto/Krypto
- Bitcoin
- Ethereum
- Depot
- Portfolio
- Sparplan
- Fonds
- MSCI
- Nasdaq

Wichtig:

- `10k` wird als `10000` erkannt.
- `Ich habe noch 10k in cryptos investiert` wird als Investment-Event gespeichert und erhoeht `current_investments`.
- `Depotstand 15000€` oder Portfolio-Stand kann als Snapshot gespeichert werden.
- Investments werden nicht mehr faelschlich als Fixkosten-Profiländerung behandelt.

### Fragen

Clarity beantwortet unter anderem:

- `Wie viel habe ich noch übrig?`
- `Was war meine größte Ausgabe?`
- `Was ist meine größte Kategorie?`
- `Wie weit bin ich von meinem Ziel entfernt?`
- `Zeig mir meine Fixkosten`
- `Kannst du mir die Fixkosten aufschlüsseln`
- `Was ist der Score?`

ETF- und Finanzbildungsfragen sind erlaubt.

### Fixkosten-Antwort

Soll als Liste kommen, nicht als Fliesstext.

Beispiel:

```text
Deine Fixkosten

🏠 Wohnen
Miete: 400.00€
Strom: 69.00€

🚗 Mobilität
Auto: 200.00€

Gesamt: 2005.88€
```

### Score

Clarity Score V2.

Vier Saeulen:

- Budget Control
- Savings Execution
- Tracking Consistency
- Financial Structure

Raenge:

- Rookie
- Stratege
- Controller
- Investor
- Manager
- Kapitalist
- Clarity Elite

Prinzip:

- Score ist schwer und statusartig.
- Nicht inflationaer.
- Hohe Scores werden ueber Zeit freigeschaltet.
- `/score` zeigt aktuellen Score.
- `/scoreinfo` und natuerliche Fragen wie `Was ist der Score?` erklaeren das System.

### Admin

Commands:

- `/admin`
- `/pending`
- `/approve USER_ID`
- `/revoke USER_ID`
- `/adminusers`
- `/health`
- `/reportjobs`
- `/backupnow`
- `/testreport YYYY-MM`

Hinweise:

- Freigabe-Buttons sind vorbereitet und muessen beim naechsten neuen Tester live geprueft werden.
- Manuelle Freigabe per `/approve USER_ID` funktioniert.

### Reset/Edit

- `/reset` loescht nach Textbestaetigung.
- `/undo` loescht letzte Ausgabe.
- `/editlast` aendert letzte Ausgabe.
- Reset soll bewusst sicher sein, nicht versehentlich per Button.

## 7. KI-Logik

Grundregel:

- DB-first.
- KI nur unterstuetzend.
- Keine erfundenen Daten.
- KI bekommt Nutzerprofil, Ausgaben, Fixkosten, Investments und relevante Historie als Kontext.
- Harte Offtopic-Fragen werden geblockt, zum Beispiel `schreib mir ein Buch`.
- Finanzfragen, ETF-Fragen und Sparfragen sollen beantwortet werden.
- Kein uebertriebener Anlageberater-Disclaimer.
- Ruhig, sachlich, keine konkreten Kauf- oder Verkaufsempfehlungen.

Prioritaet:

- Wenn eine Frage durch feste Logik beantwortbar ist, soll sie nicht in die KI.
- Typische feste Logik: Score, Budget, groesste Ausgabe, Fixkosten, Zielprognose, Profiländerung, Investment-Update.

## 8. Report-System

### Report-Ziel

Monatlicher Premium-Finanzreport als PDF.

Eigenschaften:

- 10 Seiten
- Kein Banking-Export
- Kein Dashboard
- Eher Luxus-Magazin
- Viel Weissraum
- Helle Grautoene
- Schwarze Typografie
- Kacheln
- Ruhig

### Aktueller Aufbau

- `report_engine.py` aggregiert Daten.
- `report_html_renderer.py` rendert HTML und PDF.
- HTML-Templates liegen unter `report_html/report-main/pages`.
- `style.css` definiert Design.
- WeasyPrint erzeugt PDF.
- Report kann per Bot versendet werden.

### Report-Struktur

1. Cover: Zeitraum, Freiheitsschritt, Entwicklung.
2. Financial Story: Nettovermoegen, Cash, Investments, Entwicklung.
3. Dein Monat: beste Entscheidung, groesste Ausgabe, staerkste Kategorie, Fokus.
4. Clarity Score: Score, Rang, Proof, Breakdown.
5. Wealth Journey: Vermoegenskurve, Sparplan, Einmalinvestments.
6. Your Goal: Ziel, Fortschritt, Prognose.
7. Money Map: Kategorien und Insights.
8. Meilensteine: Fortschritt, Vermoegenslevel, Punkte bis naechstes Level.
9. Clarity Recap: Was gut lief, Aufmerksamkeit, naechster Hebel.
10. Closing: `Jeder Euro hat eine Aufgabe.`

### Report-Regeln

- Keine doppelten Infos.
- Jede Seite braucht einen Job.
- Keine Fuellseiten.
- Keine Quellen- oder Technikseiten.
- Keine blauen Wellen.
- Keine grossen farbigen Flaechen.
- Keine schweren Schatten.
- Kachelstruktur beibehalten.
- Report muss echte Nutzerdaten nutzen, keine Fantasiewerte.

### Report-Versand

- Scheduler erstellt Jobs am 1. des Monats um 07:55.
- Versandfenster 08:00-14:00.
- Worker alle 10 Sekunden.
- Batch default 1, damit Server nicht ueberlastet.
- Minimum Tracking Days aktuell relevant: Reports koennen bei zu wenig Daten uebersprungen werden.
- Fuer echte Monatsreports ist 01.08 der wichtige Test, wenn Juli-Daten vorhanden sind.

## 9. Deployment und Betrieb

Lokaler Ablauf nach Aenderungen:

1. Arbeitsdateien aus Codex-Workfolder in Main kopieren.
2. In Main committen.
3. Push auf `feature_clarityr-report`.
4. Auf Server pullen.
5. `systemctl restart clarity-bot`.

Typischer lokaler Copy-Befehl:

```bash
cp "$WORK/bot.py" "$MAIN/bot.py"
```

Optional zusaetzlich:

```bash
cp "$WORK/report_engine.py" "$MAIN/report_engine.py"
cp "$WORK/report_html_renderer.py" "$MAIN/report_html_renderer.py"
rsync -a --delete --exclude "generated/" "$WORK/report_html/" "$MAIN/report_html/"
```

Server:

```bash
cd /root/clarity
git pull origin feature_clarityr-report
systemctl restart clarity-bot
systemctl status clarity-bot --no-pager
journalctl -u clarity-bot -f
```

Wichtig:

- Nur eine Bot-Instanz darf laufen, sonst Telegram 409 Conflict.
- Bot laeuft aktuell ueber systemd, nicht ueber offenes SSH-Terminal.
- Server hat WeasyPrint installiert.
- `pdfinfo`/poppler wurde genutzt, um Seitenzahl zu pruefen.
- Reports sollen 10 Seiten haben.

## 10. Aktueller Launch-Plan

Ziel:

- Start mit ersten 10 Testnutzern am 01.07.
- Testphase 01.07-01.08.
- Echter Monatsreport-Test am 01.08 mit Juli-Daten.

Vor 01.07 noch zu pruefen:

- Neuer Tester startet `/start`.
- Admin sieht Pending/Freigabe.
- Freigabe-Button testen.
- Onboarding komplett ohne Hilfe durchlaufen lassen.
- `/verfeinern` mit echten Nutzereingaben testen.
- `/health` ausfuehren.
- `/backupnow` ausfuehren.
- Manuelles Cockpit/Tester-Liste pflegen.

Tester-Kommunikation:

- Nutzer sollen Clarity realistisch im Alltag nutzen.
- Feedback besonders zu Onboarding, Kategorien, Profilverfeinerung, Fragen, Report.
- Nutzer koennen jederzeit `/reset` nutzen.
- Clarity greift nicht auf Bankkonto zu.
- Kein Wort `Bot` in finaler externer Kommunikation, wenn moeglich.

## 11. Manuelles Cockpit fuer Testphase

Fuer die ersten 10 Nutzer reicht eine manuelle Datei.

Empfohlene Spalten:

- Name/User
- Telegram-ID
- Freigabestatus
- Startdatum
- Onboarding abgeschlossen
- Profil verfeinert
- aktive Tage
- Anzahl Ausgaben
- letzte Aktivitaet
- Bugs
- Feedback
- Status
- Report erhalten
- Notizen

## 12. Spaeteres echtes Cockpit

Spaeter soll ein echtes Operations-Cockpit entstehen.

Bausteine:

- Event-System im Bot.
- Tabelle `user_events`.
- Live Activity Feed.
- Nutzerstatus: neu, onboarding, aktiv, inaktiv, pausiert, gekuendigt, gesperrt.
- Reportstatus.
- Feedbacksystem.
- Zahlungsstatus.
- Agent-Hinweise.

Langfristige Cockpit-/Agent-Vision:

- Produkt-Cockpit: Onboarding, Ausgaben, Aktivitaet, Reports.
- Kunden-Cockpit: Testphase, aktiv, pausiert, gekuendigt, zahlungsverzug, gesperrt.
- Support-Cockpit: offene Probleme, Feedback, Nutzer haengt fest.
- Revenue-/Access-Cockpit: Zahlung faellig, Mahnung, Zugang sperren.
- Agent sagt zum Beispiel: `Diese 3 Nutzer haengen im Onboarding`, `Zahlung ueberfaellig`, `Report fehlgeschlagen`.

## 13. Wichtige Produktentscheidungen

- Keine Bankintegration in v1.
- Kein Asset-Tracker fuer echte ETF-/Krypto-Kurse in v1.
- Investments werden als Events und Snapshot gespeichert, nicht live marktgetrackt.
- Report ist erst ab sinnvoller Datenbasis wirklich aussagekraeftig.
- Fuer neue Nutzer koennen fehlende Verlaufskurven elegant erklaert werden.
- Affiliate/Trade Republic Empfehlungen erst spaeter, sauber als Werbung kennzeichnen.
- Support-Agent erst nach Testphase.
- Cockpit erst nach Testphase.
- Waehrend Testphase: lieber beobachten als zu schnell umbauen.

## 14. Bekannte offene Punkte

Kurzfristig:

- Admin-Freigabe-Button mit neuem Tester live testen.
- Onboarding UX mit echter Person beobachten.
- Verfeinern-Flow weiter mit echten Schreibweisen testen.
- Report am 01.08 mit echten Nutzerdaten pruefen.
- Pruefen, ob `/user USER_ID` Admin-Einzeluebersicht sinnvoll vor Teststart noch eingebaut werden soll.

Mittelfristig:

- Event-System fuer Cockpit.
- `/user USER_ID`.
- Report-Vorankuendigung 2-3 Tage vor Monatsende.
- Besseres Feedback-System.
- Support-/Operations-Agent.
- Zahlungsstatus, Kuendigung, Pause, Mahnung, Reaktivierung.
- Echte Launch-/Marketing-Seite ohne Telegram-/Tool-Fokus.

Langfristig:

- Asset-Tracker fuer Investments.
- Share-Cards fuer Score.
- Cockpit mit Agent.
- Automatisiertes Operations-System.
- Skalierung ueber 10 Nutzer hinaus.

## 15. Entwicklungsregeln fuer neuen Programmierer / Agent

Sehr wichtig:

- Bestehenden Code nicht grob umbauen.
- Keine Strukturen zerstoeren.
- Nur gezielte Aenderungen.
- Vor Aenderungen aktuelle Stelle im Code lesen.
- Nach Aenderungen `py_compile` ausfuehren.
- Keine Fantasielogik fuer Finanzdaten.
- DB-first.
- KI nur unterstuetzend.
- Sprache ruhig und menschlich halten.
- Report-Design nicht eigenmaechtig aendern.
- Keine neuen Features vor Launch, wenn sie nicht Blocker loesen.
- Keine ungefragten Handoff-Dateien erstellen.
- Keine sensiblen Daten committen: `.env`, `.db`, `.db-wal`, `.db-shm`, generated reports.
- Bei Serveraenderungen immer nur eine Bot-Instanz.
- Bei Unsicherheit lieber kleinere Fixes als grosse Refactors.

## 16. Akzeptanzkriterien fuer 01.07

Clarity ist testbereit, wenn:

- Bot laeuft 24/7 auf Server.
- Neuer Nutzer kann freigegeben werden.
- Onboarding geht ohne Hilfe durch.
- `/verfeinern` speichert Fixkosten sauber.
- Ausgaben werden zuverlaessig gespeichert.
- Profiländerungen funktionieren: aendern, hinzufuegen, loeschen.
- Investments/Crypto/ETF werden nicht als Ausgaben oder Fixkosten fehlinterpretiert.
- Nutzerfragen zu Budget, Ziel, Score, Fixkosten, groesster Ausgabe funktionieren.
- `/reset`, `/undo`, `/editlast` funktionieren.
- `/health` zeigt stabilen Zustand.
- `/backupnow` funktioniert.
- Report-Engine kann Testreport erzeugen und senden.
- Feedback wird manuell gesammelt.

## 17. Mentales Modell

Clarity soll nicht noch eine Finanz-App sein.

Clarity soll sich anfuehlen wie:

- jemand haelt den Ueberblick,
- jemand sortiert das Chaos,
- jemand zeigt dir am Monatsende ruhig, was passiert ist,
- jemand motiviert ohne Druck,
- jemand hilft dir, Vermoegen aufzubauen.

Der Nutzer soll denken:

> Ich baue mir gerade aktiv ein besseres Leben auf.

## 18. Report-Validierung (22.07.2026)

Der aktuelle Testreport fuer Juli wurde visuell und gegen bekannte Live-Werte geprueft.

- Positiv: 10 Seiten, Ausgabenlogik und Kategorien stimmen mit dem aktuellen Juli-Teststand ueberein. Shopping `543 EUR`, Lebensmittel `218 EUR`, Restaurants `153 EUR`, groesste Einzelbuchung `380 EUR` und Score `59` sind konsistent mit den bekannten Daten.
- Gefundener Datenfehler: Das zentrale Immobilien-Eigenkapital aus `app_properties` war bisher nicht Teil des Report-Nettovermoegens. Die App rechnet es bereits ein, der Report bisher nicht.
- Gefundene Templatefehler: Die Tracking-Kachel auf Seite 2 zeigte fest `2` statt `tracked_days`. Die Meilenstein-Karte auf Seite 8 hatte eine feste Balkenbreite sowie feste Skalenwerte `15.000/20.000`, obwohl Berechnung und Ueberschrift den echten 5.000-EUR-Bereich nutzten.
- Gezielter Fix liegt im Codex-Workfolder bereit und ist per `py_compile` geprueft:
  - `report_engine.py`: App-Immobilien-Eigenkapital im aktuellen Monatsreport und Fallback beruecksichtigen.
  - `bot.py`: kommende Monats-Snapshots enthalten das App-Immobilien-Eigenkapital.
  - `report_templates/rove_pdf_report.html`: Tracking- und Meilensteinwerte dynamisch rendern.
- Nach Deployment erneut `/testreport 2026-07` ausloesen und pruefen: Nettovermoegen = Investments + Cash + Immobilien-Eigenkapital; Tracking-Tage stimmen auf Seite 2; Meilenstein-Skala zeigt den korrekten Bereich.

### PDF Money Map follow-up (22.07.2026)

- Der Webreport `Bp3C-n9JVFL0mNDaKelsY21B` wurde direkt mit der PDF verglichen.
- Die Datenquelle stimmt: Web zeigt Shopping, Lebensmittel, Restaurants, Freizeit, Mobilitaet und Pflege.
- Ursache der fehlenden Kategorien in der PDF war reine Darstellung: `rove_pdf_report.html` verwendete fest nur `money_map_categories[0]` bis `[2]`.
- Fix liegt bereit: Die PDF iteriert dynamisch ueber alle vom gemeinsamen Renderer gelieferten Kategorien (aktuell maximal sechs). Die kompakte Vorschau wurde erzeugt und bleibt bei 10 Seiten.
- Diese Aenderung wird gemeinsam mit Immobilien-Eigenkapital, Tracking-Tagen und Meilenstein-Dynamik deployed. Danach erneut `/testreport 2026-07` erzeugen.
- Financial Story wurde zusaetzlich transparent gemacht: Investments, Cash und Immobilien-Eigenkapital werden als drei getrennte Bestandteile von Nettovermoegen angezeigt. Die Vorschau mit `20.849 EUR` Investments, `10.267 EUR` Cash und `9.000 EUR` Immobilien-Eigenkapital zeigte korrekt `40.116 EUR` Nettovermoegen auf 10 Seiten.

## 19. Dynamische Mehrfachkonten Sprint 1 (15.08.2026)

- Additives, unsichtbares Backend-Fundament fertig: `app_financial_accounts`, Rollen und
  DB-basierte Feature-Flags. `multi_cash_accounts_v1` ist standardmaessig aus.
- Dry-Run-first-Skript: `migrate_financial_accounts.py`. Apply nur explizit und immer mit Backup;
  Nutzer werden einzeln atomar migriert. Kein automatisches Schema beim API-Start.
- Aktive Cash-/Buchungslogik bleibt in Sprint 1 vollstaendig auf dem Legacy-Pfad. Keine UI und
  keine historische Buchungszuordnung.
- Export und nutzergebundene Kontoloeschung sind vorbereitet. Nutzergrenzen werden durch
  `id + user_id`, serverseitige Checks und zusammengesetzte Foreign Keys geschuetzt.
- 9 automatisierte Tests sowie echter State-Vorher/Nachher-Test auf lokaler DB-Kopie gruen.
- Vollstaendiger technischer Abschlussbericht:
  `docs/DYNAMIC_ACCOUNTS_SPRINT1_2026-08-15.md`.
- Naechster Schritt nach Deployment: nur produktiven Dry-Run ansehen. Kein Server-Apply ohne
  gemeinsame Freigabe.
