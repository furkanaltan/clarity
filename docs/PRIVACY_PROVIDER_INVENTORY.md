# Rov.E Provider Inventory

Stand: 15.09.2026

Diese Datei ist eine technische Bestandsaufnahme der im kanonischen Repository
nachweisbaren Drittanbieter- und Infrastrukturpfade. Sie ist keine juristische
Freigabe. Speicherort, Region, Retention und DPA/AVV werden nur als verifiziert
angegeben, wenn sie fuer den konkreten Rov.E-Betrieb nachweisbar sind.

## Ergebnis in Kurzform

- OpenAI, Brevo, Brandfetch, CoinMarketCap, Twelve Data, Leeway, Google Fonts,
  Hosting/Nginx und Web Push sind technisch nachweisbar.
- finAPI und Stripe sind nicht als aktive Runtime-Pfade nachgewiesen und werden
  deshalb nicht als aktive Anbieter dokumentiert.
- Die App verlinkt auf `https://getrove.de/datenschutz.html`, aber die Quelle
  dieser oeffentlichen Datenschutzerklaerung liegt nicht im Repository. Dieses
  Dokument ist daher ein versioniertes Update-Delta und kein Ersatz fuer die
  oeffentliche Policy.

## Provider-Matrix

| Anbieter | Feature und Ausloeser | Uebertragene Daten | Personenbezug | Zweck | Nutzerinitiiert | Speicher/Region | DPA/AVV |
|---|---|---|---|---|---|---|---|
| OpenAI | AI-Chat bei nicht-deterministischen Fragen; optionale Report-Narrative; Screenshot-Import nur nach bewusster Bildauswahl; Crypto-Screenshot-Analyse nach bewusster Auswahl | Chat: aktuelle Frage, begrenzte relevante Historie und intent-bezogener Kontext. Reports: aggregierte Monats-/Score-/Budget-/Zielsignale. Screenshots: ausgewaehlte Bildbytes plus Extraktionsanweisung | Ja, je nach Pfad | Erklaerung, Reporttext oder Bild-/Transaktionsextraktion | Gemischt: Chat/Upload bewusst, Report optional serverseitig | Konkrete Rov.E-Account-Konfiguration, Region und Retention nicht verifiziert | Oeffentliche DPA vorhanden; konkrete Vereinbarung/Konfiguration von Rov.E nicht verifiziert |
| Brevo | Login-, Passwort-Reset- und Account-Loesch-E-Mails bei den jeweiligen Auth-Aktionen | Empfaengeradresse, Absender, kurzlebiger Code und E-Mail-Text | Ja | Zustellung von Auth-/Kontosicherheits-E-Mails | Gemischt, durch Login-/Reset-/Loeschvorgang | Konkreter Account, Region und Retention nicht verifiziert | Oeffentlicher AVV/DPA-Hinweis vorhanden; konkreter Rov.E-Abschluss nicht verifiziert |
| Brandfetch | Browser-Logo fuer erkannte Haendler beim Rendern von Transaktionen | Bekannte Haendler-Domain und oeffentliche Client-ID; kein Betrag, keine Nutzer-ID und kein Finanzprofil in der URL. Browser uebermittelt zusaetzlich uebliche Request-Metadaten | Indirekt/potentiell durch Browsermetadaten | Darstellung eines Markenlogos | Nein, automatisch beim Rendern | Anbieter-Logs, Region und Retention fuer Rov.E nicht verifiziert | UNVERIFIED |
| CoinMarketCap / CMC | Serverseitige Crypto-Suche, Kurse und Metadaten; CDN-Logo bei Darstellung | Asset-ID/Symbol, Waehrung und serverseitige API-Authentisierung; Browser kann ein oeffentliches Logo laden. Kein Rov.E-Nutzer oder Vollprofil | Nein im fachlichen Payload; Requestmetadaten potentiell | Markt-/Kursdaten und Asset-Darstellung | Gemischt: Portfolio-/Suche/Refresh | Region, Logs und Retention fuer Rov.E nicht verifiziert | UNVERIFIED |
| Twelve Data | Aktien-/ETF-/FX-Kurse, Metadaten und Logo-Fallback bei Portfolio-/Markt-Refresh | Symbol, Boerse/Waehrung, Parameter und serverseitige API-Authentisierung | Nein im fachlichen Payload; Requestmetadaten potentiell | Markt-/Kursdaten | Gemischt: Portfolio-/Refresh-Pfad | Anbieter ist eine Singapore-Gesellschaft; konkrete Verarbeitung/Region/Retention fuer Rov.E nicht verifiziert | UNVERIFIED |
| Leeway | Fallback fuer europaeische Marktdaten, wenn Twelve Data nicht verfuegbar oder ungeeignet ist | Symbol, Boerse und serverseitige API-Authentisierung | Nein im fachlichen Payload; Requestmetadaten potentiell | Markt-/Kursdaten | Nein, serverseitiger Fallback | Region, Logs, Retention und Vertrag nicht verifiziert | UNVERIFIED |
| Google Fonts | Report-Template laedt CSS und Fonts beim Rendern | HTTP-/Browsermetadaten; keine Finanzfelder in der Font-URL | Indirekt/potentiell durch Requestmetadaten | Typografie des Reports | Nein, automatisch beim Rendern | Region und Retention fuer Rov.E nicht verifiziert | UNVERIFIED |
| Hostinger / Hosting / Nginx | VPS hostet App, API, Datenbank, Reports, Backups und Logs; Nginx liefert App aus und proxyt API/Reports | Je nach Request: Auth-, Finanz-, Report- und Betriebsdaten; Nginx verarbeitet Pfad, Header und Proxy-Request | Ja | Betrieb und Auslieferung | Gemischt | Oeffentlicher Hostinger-DPA vorhanden; konkret gewaehlte Serverregion, Logs und Retention nicht verifiziert | Oeffentlicher DPA vorhanden; konkreter Vertrag/AVV nicht verifiziert |
| Web Push / Browser-Push-Dienste | Push-Abo bei Nutzerfreigabe; Report-, Monats- und Tracking-Erinnerungen | Push-Endpunkt, p256dh, auth, Nutzerbezug und Zeitstempel; ausgehend Titel, Text, Tag, URL und Ziel | Ja/potentiell, insbesondere Endpunkt und Nachricht | Zustellung optionaler App-Hinweise | Gemischt: Abo bewusst, Versand teilweise serverseitig | Je nach Endpunkt FCM, Mozilla, Apple oder Windows; Region, Logs, Retention und Vertrag nicht verifiziert | UNVERIFIED |
| Telegram Legacy Bot | Keine aktuelle produktive Verarbeitung: Service wurde gestoppt und maskiert; historische Code-/Datenpfade bleiben zur Nachvollziehbarkeit | Kein aktueller aktiver Requestpfad nachgewiesen | Historisch ja | Legacy-Kompatibilitaet/Abschaltung dokumentiert | Nein | Aktive Nutzung nicht nachgewiesen | UNVERIFIED |

## Minimierter AI-Stand

Der AI-Chat verwendet intent-bezogenen Kontext, begrenzte Historie und
deterministic-first Routing. Deterministisch beantwortbare Status- und
Prioritaetsfragen umgehen OpenAI. Die Chat-Historie ist technisch begrenzt;
aktuelle Konfiguration und TTL bleiben account-/runtime-abhaengig.

Der Report-AI-Prompt verwendet aggregierte Signale wie Monat, Einnahmen,
Fixkosten, variable Ausgaben, Budget, Sparen, Score, Kategorien und Ziele. In
der aktuellen minimierten Signalstruktur werden keine Haendlernamen und keine
konkrete groesste Einzelbuchung benoetigt. Der fertige Nutzer-Report darf
Haendler- und Kategorieinformationen weiterhin aus den von Rov.E erzeugten
Reportdaten darstellen; die Minimierung betrifft nur den OpenAI-Prompt.

Bei Screenshot-Analyse wird nur ein vom Nutzer ausgewaehltes Bild gesendet.
Eine zusaetzliche Rov.E-Finanzstruktur wird dabei nicht mitgesendet; das Bild
selbst kann jedoch sensible Inhalte enthalten. Eine dauerhafte Speicherung des
Analysebilds durch Rov.E ist im aktuellen Pfad nicht nachgewiesen.

## Policy-Delta fuer die oeffentliche Datenschutzerklaerung

Die externe `datenschutz.html` sollte technisch nachweisbar mindestens diese
Punkte ergaenzen oder aktualisieren:

1. OpenAI fuer AI-Chat, optionale Reporttexte und bewusst gestartete
   Screenshot-Analysen; Datenminimierung und deterministic-first erklaeren.
   Keine unbelegten Aussagen wie EU-only oder Zero Retention aufnehmen.
2. Brevo fuer Authentifizierungs-, Reset- und Account-Loesch-E-Mails mit
   Empfaengeradresse und kurzlebigem Code nennen.
3. Brandfetch als automatischen Browser-Request fuer erkannte Haendlerlogos
   nennen; ausdruecklich festhalten, dass Rov.E keine Finanzwerte oder
   Nutzer-ID an den Logo-Request haengt.
4. CoinMarketCap, Twelve Data und Leeway als Markt-/Kursprovider nennen und
   die serverseitige Uebertragung von Symbol-/Assetdaten von Nutzerprofilen
   abgrenzen.
5. Google Fonts fuer das Report-Template transparent nennen, ohne Region oder
   Retention zu behaupten, die nicht verifiziert ist.
6. Hostinger/Nginx als Hosting-/Proxy-Infrastruktur fuer App, API, Reports,
   Backups und Logs nennen; konkrete Vertrags-, Regions- und Retention-Angaben
   erst nach Account-Pruefung machen.
7. Web Push mit Endpunkt-/Abo-Metadaten und Push-Inhalten sowie den moeglichen
   Browser-Push-Diensten transparent beschreiben.
8. finAPI und Stripe nicht als aktive Verarbeitung aufnehmen.

Diese Punkte sind ein technisches Update-Delta. Rechtsgrundlage, Formulierung,
AVV/DPA und die tatsaechliche oeffentliche Policy muessen separat freigegeben
werden.

## Verifizierte oeffentliche Anbieterreferenzen

Die folgenden Links belegen nur, dass oeffentliche Anbieterinformationen bzw.
Vertrags-/Datenschutzseiten existieren. Sie belegen nicht automatisch die
konkrete Rov.E-Konfiguration:

- [OpenAI DPA](https://openai.com/policies/feb-2024-data-processing-addendum/)
- [Brevo AVV-Hinweis](https://help.brevo.com/hc/de/articles/15403782599570-Wo-finde-ich-den-Vertrag-zur-Auftragsverarbeitung-AVV)
- [Brandfetch Privacy](https://brandfetch.com/privacy?mode=dev)
- [Twelve Data Privacy](https://twelvedata.com/privacy)
- [CoinMarketCap Privacy](https://coinmarketcap.com/privacy/)
- [Hostinger DPA](https://www.hostinger.com/de/legal/dpa)

## Freigabestatus

- Technisches Provider-Inventar: GO fuer Review und Vertragsklaerung.
- Oeffentliche Datenschutzerklaerung vollstaendig aktualisiert: NO-GO, solange
  die externe Policy-Quelle nicht im kanonischen Arbeitsstand vorliegt und die
  als `UNVERIFIED` markierten Punkte nicht accountbezogen geklaert sind.
- Open-Banking-Integration: NO-GO auf Basis dieses Inventars, bis Provider,
  Regionen, Token-/ID-Modell, Loeschung und Audit-Trail separat freigegeben sind.
