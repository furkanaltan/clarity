# Financial Intelligence: Coaching-Qualitaet, Block 3

Stand: 22.10.2026. Basis: `84bbc604f7dda73ab68b32c7f49b09bf925438b4`.
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

Die vier gesperrten Mustertypen und Budget-Recovery sind konkrete verbleibende
Darstellungsluecken. Der Gate wurde in diesem Qualitaetsblock nicht auf weitere
Typen erweitert. Fuer deren sichtbare Nutzung fehlen typspezifische Texte,
gemeinsame Faktenprojektionen und deterministische Erklaerungen mit passenden
Abnahmen. Neue Detektoren sind dafuer nicht erforderlich.

Die bestehende Wiederholungsregel gilt lokal fuer dieselbe Insight-ID am
selben Tag. Eine dauerhafte oder geraeteuebergreifende Quittierung gibt es
noch nicht; dafuer wird hier kein neues Zustandsmodell eingefuehrt.

Coach V4 bleibt standardmaessig deaktiviert. Sichtbarkeit wird ausschliesslich
fuer synthetische Tests ueber die vorhandenen Schalter gesetzt. Kein Push,
Deployment oder Zugriff auf Produktionsdaten ist Bestandteil dieses Nachweises.
