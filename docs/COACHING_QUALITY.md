# Financial Intelligence: Coaching-Qualitaet, Block 3

Stand: 22.10.2026. Basis: `84bbc604f7dda73ab68b32c7f49b09bf925438b4`.
Die Matrix und Befunde dokumentieren den damaligen Block-3-Stand; spaetere
Aufloesungen der Darstellungsluecken stehen unter Block 4A weiter unten.
Alle Profile sind synthetisch. Geprueft werden die bestehende Engine, der echte
Snapshot, der gemeinsame Insight-Vertrag und die deterministische Erklaerung.
Ein bestandener Engine-Test ist kein Nachweis einer nutzersichtbaren Funktion.

## Szenariomatrix

| Szenario | Engine und Fakten | Darstellung / Erklaerung | Ergebnis und Grenze |
|---|---|---|---|
| Lieferdienst an festen Wochentagen | PASS: drei unterschiedliche Montage, Anzahl, Gesamtbetrag und Monatszeitraum stimmen. Mehrere Bestellungen an einem einzigen Tag ergeben kein Wochenmuster. | FAIL: bestehender sichtbarer Gate sperrt `repeated_spending_pattern`. | Erkennung korrekt; noch kein bedienbarer Mentor-/Ask-Rovi-Hinweis dieses Typs. |
| Deutlich steigende Kategorie | PASS: drei aufeinanderfolgende abgeschlossene Monate mit sechs Buchungen; Monatswerte und Differenz stimmen. | FAIL: `spending_trend_worsening` bleibt gesperrt. | Keine neue Warnung aus wenigen oder lueckenhaften Monaten. Sichtbare Erklaerung fehlt. |
| Ausgaben nach Gehalt | PASS: drei zuverlaessige Zyklen und mehrere Buchungen je Fenster; Vergleichswerte bleiben erhalten. Zwei Zyklen reichen nicht. | FAIL: `post_income_pattern` bleibt gesperrt. | Vertrag verlangt ausdruecklich zeitlichen Zusammenhang ohne Kausalbehauptung. |
| Vertraege / moegliche Doppelabos | PASS: aehnliche Dienste erkannt; doppelte Zeilen desselben Diensts werden nicht doppelt summiert. Widerspruechliche Doppelzeilen liefern keinen erfundenen Gesamtbetrag. | FAIL: `subscription_cluster` bleibt gesperrt. | Aehnliche Dienste sind keine bewiesenen unnoetigen Doppelabos und kein belegter Einsparbetrag. |
| Positive Entwicklung | PASS: starke Entwicklung ueber drei Monate; sechs Quellbuchungen und Werte sind exakt. | PASS nach Fix: Mentor und Erklaerung nutzen denselben Vertrag; die urspruengliche mittlere Konfidenz bleibt erhalten. | Nur hohe Musterstaerke passiert den positiven Test-Gate. Mittlere Staerke und fehlende Monate bleiben gesperrt. Budget-Recovery bleibt ein separater, noch gesperrter Typ. |
| Wenige Buchungen | PASS: eine Buchung ergibt keinen Verhaltenshinweis. | PASS: kein Hinweis und keine erfundene Erklaerung. | Eine noch fehlende Datenbasis wird nicht als Verhaltensmuster ausgegeben. |
| Unvollstaendige, widerspruechliche, alte Daten | PASS: widersprechende Richtungen, veraenderte Quellen und abgelaufene Snapshots werden unterdrueckt. | PASS nach Fix: unvereinbare Budgetwerte und doppelte, lueckenhafte oder widersprechende Monatsreihen passieren den sichtbaren Gate nicht. | Nicht erfasste Ausgaben kann die Engine nicht ausschliessen; Texte sprechen deshalb von erfassten Ausgaben. |
| Mehrere Hinweise / Dringlichkeit | PASS: die bestehende globale Engine-Auswahl wird wiederverwendet. | PASS nach Fix: ein ausgewaehlter V4-Vertrag, V3 hat Vorrang; Pfeil und Kartenflaeche oeffnen denselben Kontext. Nach Oeffnen kein erneuter Hinweis am selben Tag, auch nach Reload. | Keine neue Rangfolge. Keine Aktivierung weiterer Insight-Typen. |

## Bestaetigte Fehler und Korrekturen

1. Die Engine liefert sichere Timing-Metadaten, die der sichtbare Gate bisher
   pauschal verwarf. Nur die tatsaechlich erzeugten, begrenzten Metadaten werden
   jetzt validiert akzeptiert; direkte Quell- und Konto-IDs bleiben ausgeschlossen.
   Der bereits validierte Gesamtbudgetstatus bleibt im DTO erhalten.
   Falsch typisierte Metadaten werden ohne Ausnahme verworfen.
2. Hohe Musterstaerke positiver Entwicklungen war wegen mittlerer finanzieller
   Relevanz nie sichtbar. Die Auslieferung akzeptiert fuer diesen bestehenden
   Typ hohe Musterstaerke mit mittlerer Konfidenz. Keine Konfidenz wird erhoeht,
   kein Detektor und keine Engine-/V3-Prioritaet veraendert. Schwache positive
   Evidenz bleibt ausgeschlossen.
3. Bei mehreren unabhaengigen Primaerkandidaten nahm der Snapshot den ersten
   statt des vom Inspector gewaehlten Hinweises; der Vertrag blieb leer. Snapshot
   und Vertrag verwenden nun ausschliesslich dessen vorhandene globale Auswahl.
4. Ein widerspruechlicher Budget-Ueberbetrag konnte als Tatsache gerendert
   werden. Die bereits vorhandenen Fakten muessen zusammenpassen. Das ist eine
   Konsistenzpruefung, keine neue Budgetformel.
5. Der Pfeil einer V4-Karte verlor den Kontext. Pfeil und Kartenflaeche verwenden
   nun denselben Einstieg. Allgemeiner Chat und bestehende Modul-CTAs bleiben
   durch Regressionen abgedeckt. Datumsangaben und Kontexttexte sind lesbar
   auf Deutsch formuliert, ohne neue Tatsachen oder Verhaltensursachen abzuleiten.

Vorher-Nachweis: vier rote Szenariotests sowie ein separat reproduzierter
Pfeil-Kontexttest. Keine bestehende Assertion wurde fuer PASS entfernt.

## Pruefnachweis und Freigabegrenze

Gezielte Python-Regressionen: 308 PASS, einschliesslich echtem Chat-API-Pfad
mit berechnetem Snapshot, Quellbuchungen, Kontoabgrenzung und deaktiviertem
Gate. Chromium und WebKit: je sechs Flows PASS, jeweils Desktop, 390 px und
Standalone-iPhone-PWA; Pfeil/Kartenkontext, V3-/Gehaltsvorrang, Retry und
Unterdrueckung nach Reload. WebKit 18.2 / Playwright 1.49.1 wurde fuer das
vorhandene macOS 13 isoliert bereitgestellt. Das ist keine physische
iPhone-Abnahme. Full-Suite: 2.122/2.122 PASS in 564,727 Sekunden,
`bash scripts/test.sh full`, Protokoll `full-suite-gate-v3.log`.
SHA256 des vollstaendigen Protokolls:
`94552c2b030b35d9dbb8220a46d189605864b5d75e128695da6fd5cd1bbc9d32`.
Laufzeit: isoliertes Python 3.12.14,
Abhaengigkeiten aus `requirements/dev.txt`, GnuPG 2.4.4 / libgcrypt 1.10.3.
Die GPG-Quellen verwenden die bereits dokumentierten SHA256-Pruefsummen.
Testumgebung und Protokolle liegen ausserhalb des Repositories unter
`/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/`.

Ein vollstaendiger Lauf (`full-suite-gate-v2.log`) hatte bei 2.122 Tests genau
einen Error im unveraenderten Zwei-Worker-WSGI-Test: `ConnectionResetError`
waehrend der lokalen PID-Abfragen. Ohne Code- oder Testaenderung bestand
derselbe Test isoliert (`wsgi-recheck.log`): beide Worker erreicht, jeweils
sechs zugelassene und 42 begrenzte Requests, Limit nach Neustart erhalten.
Der einzelne Transportfehler wird nicht als Coaching-Produktfehler oder als
bestandener Gesamtlauf umgewertet. Der vollstaendige Wiederholungslauf bestand
ohne weitere Code- oder Testaenderung. Die neun Runtime-/Testdateien stimmen
mit `source-before-tests.sha256` ueberein; kein Test wurde abgeschwaecht oder
uebersprungen. Die Ursache des einmaligen Verbindungsabbruchs bleibt unbewiesen.

Zum damaligen Stand von Block 3 blieben vier Mustertypen und Budget-Recovery
gesperrt. Die anschliessende gezielte Integration und Abnahme dieser Muster ist
unter Block 4A dokumentiert; die historische Matrix oben bleibt als damaliger
Vorher-Nachweis erhalten.

Die bestehende Wiederholungsregel gilt lokal fuer dieselbe Insight-ID am
selben Tag. Eine dauerhafte oder geraeteuebergreifende Quittierung gibt es
noch nicht; dafuer wird hier kein neues Zustandsmodell eingefuehrt.

Coach V4 bleibt standardmaessig deaktiviert. Sichtbarkeit wird ausschliesslich
fuer synthetische Tests ueber die vorhandenen Schalter gesetzt. Kein Push,
Deployment oder Zugriff auf Produktionsdaten ist Bestandteil dieses Nachweises.

## Block 4A: bestehende V4-Muster fuer Mentor und Ask Rovi

Arbeitsbasis: `f4141c9ee9eb0f4d240374a987652fdfac776d15`. Die Integration nutzt
die vorhandenen V4-Detektoren, den gemeinsamen Insight-Vertrag, den vorhandenen
V3-Prioritaetsentscheid und den bestehenden sichtbaren Default-Deny-Gate. Keine
neuen Muster, Finanzformeln oder globalen Rangregeln. Die bestehende
Improvement-Ausgabe bleibt mit ihrem bisherigen Monatsreihen-Vertrag kompatibel.

| Bestehendes Muster | Sichtbares Evidence-Gate und Grenze | Ergebnis |
|---|---|---|
| Lieferdienst/Wochentag | Mindestens vier passende Wochentage im Zeitraum, mindestens drei Buchungstage, mindestens 60 Prozent Wiederholung und 30 EUR belegte Gesamtsumme; Karten- und Chatfakten stimmen mit demselben Vertrag ueberein. | PASS; ein einzelner Tag mit mehreren Bestellungen bleibt unsichtbar. |
| Steigende Kategorieausgaben | Mindestens drei aufeinanderfolgende Monate, streng steigende Monatswerte und der vorhandene Delta-Wert muss exakt zu den Endpunkten passen. | PASS; fehlende, lueckenhafte, nicht monotone oder widerspruechliche Reihen werden unterdrueckt. |
| Ausgaben nach Gehalt | Mindestens drei verlaessliche Gehaltszyklen, nur vorhandene 3-/7-Tage-Fenster, mindestens zwei Buchungen je Zyklus, expliziter Zeitkorrelations-Hinweis und Delta-Konsistenz zwischen Durchschnitt, Basis und Differenz. | PASS; die Darstellung behauptet keine Ursache. Zwei Zyklen, unsichere Gehaltseingaenge oder widerspruechliche Werte bleiben unsichtbar. |
| Aehnliche laufende Vertraege | Mindestens zwei Services, vollstaendige Frequenz, bestaetigter Aktivstatus und vollstaendige Datenqualitaet. Aehnlichkeit wird weder als bewiesenes Doppelabo noch als Ersparnis dargestellt. | PASS; Ask Rovi nennt aktuelle, nutzergebundene Vertragszeilen statt Ausgabebuchungen. Widerspruechliche Duplikate liefern keinen erfundenen Gesamtbetrag. |
| Positive Budget-Recovery | Nur abgeschlossener Monat, vergleichbare Budgetrahmen, mindestens drei belegte fruehere Ueberschreitungsmonate und aktuelle Kategorieausgaben innerhalb des aktuellen positiven Rahmens. | PASS; die Aussage gilt nur fuer die Kategorie, nicht fuer Gesamtbudget oder Ursache. Materielle Budgetaenderung unterdrueckt den Hinweis. |

Gezielte Regressionen: **18/18** in `test_coaching_quality`; gemeinsam mit
Sichtbarkeits- und Vertragsregressionen **43/43 PASS**. Abgedeckt sind positive
Faelle, zu wenige Buchungen/Zyklen, unvollstaendige oder widerspruechliche
Monats-/Budgetdaten, veraenderte oder veraltete Quellen, aktuelle user-scoped
Beispiele, deterministische Warum-/Buchungs-/Handlungsantworten, Dedupe und
V3-Vorrang. Der Test fuer widerspruechliche Monatsreihe und Delta bestaetigt
fail-closed Verhalten. Der vorhandene V4-Flag-/Allowlist-Gate bleibt unveraendert
und ohne Serverkonfiguration standardmaessig aus; keine globale Freigabe und
keine Production-Konfiguration wurde geaendert.

Browser-Smokes mit synthetischem lokalem API-Transport: Chromium **13/13** und
WebKit **13/13** Flows. Je Engine: allgemeine Desktop-, 390-px- und
Standalone-iPhone-PWA-Flows sowie die fuenf Muster auf Desktop und iPhone-PWA.
Geprueft wurden Mentorprioritaet, dieselbe Insight-ID im Ask-Rovi-Kontext,
deterministische Antworten, Retry, Dedupe nach erneutem Laden und mobile
Darstellung. Screenshots liegen ausserhalb des Repositories unter
`/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/` in den
Ordnern `block4a-browser-chromium/` und `block4a-browser-webkit/`. Die
Standalone-Emulation ist kein physischer iPhone-Test.

Der finale Pflichtlauf `bash scripts/test.sh full` besteht mit **2.126 Tests in
541,236 Sekunden, 0 Failures, 0 Errors, 0 Skips**. Verwendet wurde die
isolierte Python-3.12.14-/GPG-2.4.4-Umgebung aus dem bestehenden Workflow; der
Loopback-/GPG-Lauf lief ausserhalb der Sandboxbeschraenkung mit ausschliesslich
synthetischen lokalen Testdaten. Das Protokoll liegt ausserhalb des Quellbaums
unter `/Users/furkanaltan/Documents/Codex/rove-coaching-quality-20261022/`.

Der unmittelbar vorherige Gesamtlauf hatte einen einzelnen Fehler im
unveraenderten WSGI-Test zur Worker-PID-Verteilung: unter paralleler Last wurden
die Requests nur einem der gestarteten Worker zugeordnet. Derselbe Test bestand
isoliert; der unveraenderte abschliessende Gesamtlauf bestand anschliessend
vollstaendig. Es wurden weder Produktcode noch Testannahmen fuer diesen Lauf
geaendert.

Ein erster eingeschraenkter Gesamtlauf deckte eine versehentliche Ausweitung
des neuen Delta-Gates auf den bestehenden positiven Trendtyp auf. Das Gate ist
auf den neuen steigenden Trend begrenzt; die alten Improvement-Vertraege und
Assertions bestehen unveraendert. Ein zusaetzlicher Konsistenzcheck verwirft
nur den Fall, dass der bestehende steigende Trend-Delta nicht zu seinen
belegten Monatsendpunkten passt. Kein Test wurde entfernt oder abgeschwaecht.

Coach V4 bleibt bis zur ausdruecklichen manuellen Freigabe aus. Die in den
Browser-Smokes simulierte Test-Sichtbarkeit ist keine Production-Abnahme. Kein
Push, Deployment, Produktionsdatenzugriff oder Aenderung an Reports, Login,
Mehrkonten oder Sachwerten.

## Mentor-Priorisierung: Score-Fallback nach V4, 22.10.2026

Arbeitsbasis: `f336a2f45e746a55fc46c4209a991d03b752d16d`. Die bisherige
Integrationsregel sperrte jeden V4-Hinweis, sobald irgendein V3-Kandidat
vorhanden war. Damit verdrang auch der generische Score-Hinweis `motivation`
mit Prioritaet 10 ausreichend belegte V4-Erkenntnisse. Der vorzeitige
V3-Return im Frontend wiederholte diese Sperre.

Nur der bestehende Kandidat mit ID `motivation`, Typ `motivation` und
Prioritaet 10 ist jetzt ein nachrangiger Fallback. Konkrete und unbekannte
V3-Kandidaten behalten ihren bisherigen Vorrang. Der gemeinsame V4-Vertrag
und alle Evidenzpruefungen bleiben Voraussetzung; der API-Claim verwendet
dieselbe korrigierte Auswahl unter dem bestehenden Schreib-Lock. Im Frontend
bleibt der Score bei fehlendem, noch nicht reserviertem oder unterdruecktem
V4-Hinweis verfuegbar. Nach dem Oeffnen eines bestaetigten V4-Hinweises wird
der Score wieder angezeigt; unveraenderte Evidenz bleibt nach Reload gesperrt.

Regressionen pruefen Legacy- und Mehrkontenprofile, Score gegen gueltiges V4,
V3-Vorrang, fehlende/veraltete/widerspruechliche Evidenz, echte API-Claims,
idempotente Anzeigequittierung, Oeffnen und Nutzerisolation. Die bestehenden
Browser-Smokes verwenden nun auch bei allen fuenf V4-Mustern einen gleichzeitig
vorhandenen Score-Fallback. Die bisherigen dringenden V3-, Gehalts-, Chat-
Retry- und Reload-Pruefungen bleiben erhalten.

Minimaler Runtime-Umfang: `rove_coach_v4_delivery.py` und die daraus unabhaengig
versionierte `frontend/index.html`. Kein neues Schema, keine Gate-Aenderung,
keine Finanzformel und kein Detektor. Production-Abnahme bleibt ein separates
Gate; diese lokale Korrektur fuehrt weder Push noch Deployment aus.

Pruefnachweis: Die neuen Auswahl-/API-/Renderingregressionen reproduzierten
vor der Runtime-Korrektur vier Fehlschlaege (einschliesslich beider
Profil-Unterfaelle). Danach bestehen 129 gezielte Regressionen. Chromium und
WebKit bestehen je 13 Browser-Flows mit Desktop, 390 px und Standalone-
iPhone-PWA-Emulation; dies ist kein physischer iPhone-Test. Der Pflichtlauf
`bash scripts/test.sh full` besteht mit 2.171 Tests in 568,128 Sekunden,
0 Failures, 0 Errors und 0 Skips. Python 3.12.14 und die bestehende isolierte
GPG-2.4.4-Umgebung wurden unveraendert wiederverwendet. Keine bestehenden
Assertions wurden abgeschwaecht. Runtime- und Testquellen wurden nach dem
Lauf gegen `tested-source.sha256` verifiziert.

Protokolle und Screenshots liegen ausserhalb des Quellbaums unter
`/Users/furkanaltan/Documents/Codex/rove-mentor-priority-20261022/`.
SHA256 von `full-suite.log`:
`36003b23fdc16f66b90aec9da1876bca62b1a9592818453f555f609a94ac0740`.
