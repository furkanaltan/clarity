# Release-Nachweise und Git-Abgleich, 21.10.2026

Dies ist ein Nachweis bestehender Releases, kein neuer Deploy-Auftrag und keine
zweite Arbeitsregel. Massgeblich bleibt `DEVELOPMENT_WORKFLOW.md`. Keine
Production-Verbindung, produktive Datei-/ENV-/Datenmutation oder Push in diesem Auftrag.

Fortschreibung nach freigegebener Teststabilisierung: Phase A ist mit
`4e3635d` abgeschlossen (2.074/2.074 PASS). Der Arbeitsbranch ist jetzt
`codex/full-suite-and-sync`; die kanonische Navigationsangleichung und die
konkrete Git-Synchronisierung stehen in den nachgefuehrten Abschnitten unten.

## Frisch verifizierter GitHub-Stand

Remote: `https://github.com/furkanaltan/clarity.git`. Alle veroeffentlichten
Branch-Refs wurden kontrolliert mit deaktivierten lokalen Hooks und ohne
automatische Wartung abgerufen, ohne Pull/Merge/Rebase/Prune. Danach bestaetigte
`git ls-remote --symref origin HEAD 'refs/heads/*' 'refs/tags/*'`:

| Remote-Ref | Commit |
|---|---|
| Default `HEAD` -> `main` | `663e49db860fd421dbb23f32fc2ab1705eb92940` |
| `main` | `663e49db860fd421dbb23f32fc2ab1705eb92940` |
| `rove-website-private` | `663e49db860fd421dbb23f32fc2ab1705eb92940` |
| `feature_clarityr-report` | `4e6c29074acb7f08f7908ea34f2d405051b4925b` |
| Tags | keine veroeffentlichten Tags gemeldet |

Der korrekte bestehende Integrationsbranch bleibt `feature_clarityr-report`,
nicht der wesentlich aeltere Defaultbranch. Lokal steht dieser Branch auf
`e25f00dce1d59f89bd1a287717aa9e21e06f46d2` (16 Commits vor dem Remote).
Der bisherige enge Fetch-Refspec wurde nicht still global umkonfiguriert;
fuer den jetzigen Vergleich wurden ausdruecklich alle Branch-Refs abgerufen.

Bei Auftragsbeginn war `codex/quick-add-preserve-context` auf
`d94b3c7a846ee98a6f4130370b448743b1ce05cd` sauber. 36 seiner Commits sind
von keinem aktuell veroeffentlichten Branch/Tag erreichbar, keine Remote-only
Commits gegenueber `origin/feature_clarityr-report`. Es wurde nicht behauptet,
dass auch kein unreferenziertes Objekt auf GitHub existieren kann.

Die fehlende kanonische Kette, aeltester zuerst:

```text
cf59b15 docs: align recovery runbook with gate v3 contract
5a8cdc4 feat: add external recovery trust authorities
91c5ad2 feat: automate externally verified recovery operations
bebf651 fix: preserve recovery diagnostics through cleanup failures
b7d9d78 feat: add failed recovery SQLite diagnostics
dc23c37 fix: diagnose failed recovery SQLite candidates
eaa89fa fix: preserve recovery alert renewal evidence
2b6761d Complete monthly report PDF cashflow and largest expense details
78b4cac Polish PDF largest expense and milestone hierarchy
20277af Correct synthetic report sample and clarify remaining balance
345591f Expand PDF monthly recap with evidence-based snapshot insights
c4eb48b Align and polish Webreport V2 presentation
25e9964 Polish Webreport copy and typography
3b48c81 Require report ownership and complete app report artifacts
0a9ae97 Fix login recovery and simplify authentication forms
e25f00d Keep initial session checks quiet on login
b28fd8f Record deployed login feedback baseline
0a970be Add black glass icon foundation and navigation
f0c84d2 Limit black glass icons to assets and restore navigation
cb709b4 Apply black-glass style to app feature icons
e464cab Restore original bottom navigation styling
7e68645 Round expense and Rovi glass badges
c428319 Round contract detail icons with shared glass styling
ad6b8c8 Open the matching monthly report from mentor actions
3073a46 Fix onboarding and quick-add flow stability
c020dd7 Polish onboarding vehicle costs and first-budget guidance
badab59 Refine budget suggestion attribution in total summary
b1aa0ce Store account-owned valuables and integrate optional onboarding
2e4e8f5 Polish valuables onboarding and sheet copy
8ed5ea4 Fix valuables visibility and add actions
d8cca35 Polish valuables form and confirmed save feedback
311c3af Keep valuables entry accurate and verify standalone mobile flow
f762ae5 Align valuables actions with existing investment controls
c52891e Polish valuables sheet hierarchy and field spacing
27af4c9 Stabilize canonical cash accounts and reviewed user transitions
d94b3c7 Preserve navigation context after confirmed quick entries
```

Diese Source-Historie ist **keine** Production-Allowlist. Insbesondere wurden
Recovery-/Report-Aenderungen nicht durch einen ganzen Branch-Deploy nachgezogen.

## Isolierte bereits produktive Backend-Historie erhalten

Die Production-Ausgaben nennen weiterhin Git-HEAD
`851c8abb10040a3dc6aeed61beb4ef1e72b5bd59` plus spaetere explizit freigegebene
Datei-Overlays. HEAD allein bezeichnet daher nicht die laufenden Dateien.
Dieser isolierte Commit war bei Auftragsbeginn im kanonischen Objektbestand
nicht vorhanden. Sein erhaltenes lokales Release-Repository wurde geprueft:

- Release-Paket: `/Users/furkanaltan/Documents/Codex/first-user-deployment-candidates/first-user-3073a46-production-20261019`.
- `first-user-backend.bundle` SHA256: `221266c5da934015ac90185ebcb545d7aef69d0b78f9e16725cdf6084fe4f56e`.
- Target `851c8abb...`, direkter Parent `c95d7c01a74af18cebebf5945f565cf0fb4d282a`;
  API-/State-Hashes beider Staende passen exakt zu den damaligen Operator-Belegen.
- Das kleine Bundle allein benoetigt den vorher fehlenden Parent. Deshalb wurde
  die vorhandene vollstaendige lokale Release-Historie nur unter
  `codex/production-baseline-20261019` ins kanonische Git uebernommen. Kein
  Checkout, Merge, Rebase oder Push; keine Source-Datei wurde ersetzt.

Die acht zusaetzlichen isolierten Commits sind `765a615`, `8f6a028`, `a4b2fdb`,
`e991483`, `4943dc6`, `fba26cd`, `c95d7c0`, `851c8ab`. Der weitere alte isolierte
Report-Branch `codex/report-v2-production-20261011` (`86b8577`) bleibt separat.
Dadurch sind vor den neuen Dokumentations-Commits insgesamt 45 lokale Commits
nicht von den veroeffentlichten Remote-Refs erreichbar (36 + 8 + 1).
Diese Zahl ist nicht mit 45 ungesichteten neuen Produktfixes gleichzusetzen.

## Nachweis der drei letzten Frontend-Releases

| Build / Source-Commit | SHA256 des bestaetigten Artefakts |
|---|---|
| `frontend-f762ae5` / `f762ae55180a149cad88a1136207c60a9e358537` | `8720df40755d0fb7ebe5ca1d67a1220f83d0119e4f86ff2b6f401ce7b38d36ff` |
| `frontend-release-20261021-27af4c9` / `27af4c9ccaff71c60939481fcfaae2d256f1b518` (enthaelt `c52891e`) | `2eb7ae476b1e8aa5139aa858d087dce45c326fcc7bdf886c1b70d0a82e117bd8` |
| `frontend-d94b3c7` / `d94b3c7a846ee98a6f4130370b448743b1ce05cd` | `e77e5789ace28a9724584b458c64159ac82af6efeec174dc13d5c424a3861262` |

Lokale Artefakthashes wurden erneut gelesen und gegen die vorhandenen
Production-Terminalbelege verglichen, nicht erneut von Production abgerufen.
Der Operator bestaetigte erfolgreiche iPhone-/Mac-Nutzung von Sachwerte f762ae5.
Fuer den spaeteren Mehrkonten-Gesamtumfang und Quick-Add liegt keine ausdrueckliche
vollstaendige manuelle Abnahme vor: **PENDING**, kein behauptetes FROZEN.
Quick-Add: atomischer Wechsel, oeffentlicher Hash/Marker, beide Healthchecks,
weitere Assets unveraendert und kein Dienstneustart laut letztem Operator-Beleg.

### Exakte Reproduktion und kanonische Angleichung

Ein Standardbuild nur aus dem jeweiligen historischen Source-Commit ergibt **nicht** diese
Production-Bytes. Der Unterschied besteht aus Build-Marker plus dem bereits
isoliert freigegebenen Report-/Mentor-Navigationsfragment (Vorschau fuer laufende
Reports). Keine Renderer-, Berechnungs-, Login- oder Datenaenderung.

Der exakte kleine Unterschied ist jetzt als historischer Source-Nachweis
versioniert: `releases/20261021/frontend-d94b3c7-production.patch`.
Patch-SHA256: `41ebca6732e06c75bb4eb5f9c2e05578e1133228d17bdb7f4233e0e1d48f8be2`.
Beim ersten Nachweisauftrag wurde er **nicht** auf die kanonische Runtime-Datei
angewendet. Der damalige Build-Marker bleibt Teil des historischen Patches.

Lokale Rekonstruktion in einem temporaeren Verzeichnis, fern von Production:

1. `git show <source-commit>:frontend/index.html` als temporaere `frontend/index.html` entnehmen.
2. Den versionierten Patch dort mit `git apply --check` pruefen, dann anwenden.
3. Fuer f762ae5 bzw. 27af4c9 nur den gestempelten `frontend-d94b3c7`-Marker
   durch den jeweiligen Marker aus der Tabelle ersetzen.
4. SHA256 gegen die Tabelle vergleichen.

Ergebnis der erneut ausgefuehrten historischen Rekonstruktion:
**3/3 bytegenau PASS**. Historische Commits und Archiv-Refs werden nicht
umgeschrieben; fuer die beiden aelteren Quellen bleibt der versionierte
historische Patch Teil der Reproduktionsanleitung.

Die anschliessend ausdruecklich freigegebene Phase B gleicht ausschliesslich
`openMentorCandidate()` an dieses bereits ausgelieferte Fragment an. Der
kanonische Marker bleibt `frontend-__GIT_COMMIT__`; Renderer, Finanzwerte,
Monatscheck, Login und Backend bleiben unveraendert. Neue Tests reproduzierten
vorher die abweichenden Vorschau-/Fehlerwege und pruefen jetzt Running-Preview,
Ready-Web/PDF, richtigen Monat, fehlende Artefakte und unveraenderten Monatscheck.

Die aktuelle kanonische Quelle ergibt ueber den vorhandenen Release-Builder
mit dem ausdruecklich historischen Marker `frontend-d94b3c7` direkt und ohne
zusaetzlichen Patch den bestaetigten SHA256 `e77e5789ace28a9724584b458c64159ac82af6efeec174dc13d5c424a3861262`.
Damit sind alle zuletzt freigegebenen Mehrkonten-, Sachwerte- und Quick-Add-
Frontendbytes erhalten. Neue regulaere Builds tragen weiterhin ihre neue
Commit-ID, niemals einen zurueckdatierten Production-Marker.

## Mehrkonten-Backend: Git- und Dateizuordnung

Die fuenf ausgelieferten Dateien sind bytegenau die Quellen aus `27af4c9`:

| Runtime-Datei | Ausgelieferter SHA256 |
|---|---|
| `bot.py` | `fef5e99ec3736d87ab0af61bc2269a289d4a751f8b7665d15a49254328b1ebcd` |
| `rove_app_api.py` | `134064e1a3428b729fb59050cb9047a293e8fd23bf6cad281917035593116a42` |
| `rove_app_state.py` | `709437f0465b43cf9274af81732026ec1d04723c8f09eb7bdfa526bc8f31cc40` |
| `rove_expense_domain.py` | `39170143a6119349295a5b6da837697ca6f1ad140e6e35919f231060eed9af8f` |
| `rove_financial_accounts.py` | `775a144e3756c5d9c4f6a35c0f9b1536f6e942d3a2cb611ce8f6190f64754feb` |

`rove_valuables.py` blieb beim Mehrkonten-Release unveraendert:
`2c988287804b07179efa0d3c71ed3d8049848f2cb363246f9273f35a92d07778`.
Nur API-Neustart, Bot inaktiv, ENV unveraendert laut Operator-Ausgaben.
Kein ganzer Entwicklungsbranch wurde als Production-Code aktiviert.

## Synchronisierung auf den Integrationsbranch: vorbereitet

Der Arbeitsbranch `codex/full-suite-and-sync` erbt d94b3c7 und die beiden
Dokumentations-Commits `e588a73`, `7f48c45`. Danach folgen `4e3635d` fuer die
Teststabilisierung und dieser Navigations-/Nachweiscommit. Alle aelteren kanonischen
Arbeitsbranches bis f762ae5/27af4c9 sind Vorfahren; sie muessen nicht alle einzeln
zusammengefuehrt oder separat gepusht werden. `main` und `rove-website-private`
bleiben unangetastet. Die isolierte Production-Kette bleibt eine getrennte Referenz.

Der erneute Abruf nach bestandenem Phase-A-Gate bestaetigt unveraenderte
Remote-Refs. Vor dem Navigationscommit: **0 Remote-only / 39 Local-only**
gegen `origin/feature_clarityr-report`. Mit diesem Change umfasst die zur
Pruefung vorgelegte Kette **40 Commits**: die oben gelisteten 36 plus die
beiden Dokumentations-Commits, Teststabilisierung und Navigationsangleichung.

Konkreter Vorschlag fuer Furkans **noch erforderliche** Push-Entscheidung:

1. Quelle: abgeschlossener `codex/full-suite-and-sync` mit der im Uebergabeblock
   genannten exakten SHA und vollstaendiger Commitliste. Ziel ausschliesslich
   `refs/heads/feature_clarityr-report` auf `origin`.
2. Erwarteter Remote-Target bleibt `4e6c29074acb7f08f7908ea34f2d405051b4925b`.
   Unmittelbar vor Ausfuehrung frisch lesen, auf Gleichheit pruefen und
   `git merge-base --is-ancestor` fuer Quelle/Ziel bestehen lassen.
3. Furkan fuehrt nach seiner Freigabe den normalen Fast-Forward-Push persoenlich
   aus. Codex fuehrt auch keinen Dry-Run-Push aus. Kein Force-Push und kein Merge.
4. Separate Archivhistorien bleiben unangetastet und sind kein Push-Ziel:
   `codex/production-baseline-20261019` bleibt auf `851c8abb10040a3dc6aeed61beb4ef1e72b5bd59`,
   `codex/report-v2-production-20261011` auf `86b8577f82ab6eae860ff70062e7f22f70ca2db3`.

Diese Synchronisierung der Source-Historie ist weiterhin keine Production-
Allowlist und kein Deployment. Insbesondere werden keine Archiv-Commits
zusammengefuehrt oder alte Production-Dateien dabei ersetzt.

Die externen Wegweiser liegen im separaten lokalen Repository
`/Users/furkanaltan/Documents/Codex`: nur `CLAUDE.md` und der vorhandene
`.agents/skills/rove-deploy/SKILL.md` wurden mit `fea5908` separat versioniert. Dessen schon
vorhandene fremde Aenderung an `ONBOARDING.md` bleibt unangetastet. Die globale
CTO-`~/.codex/AGENTS.md` bleibt bytegleich unveraendert.

## Offene Gates

- Full-Suite Phase A **2.074/2.074 PASS**, Phase B **2.077/2.077 PASS**;
  jeweils keine Failures, Errors oder Skips. Protokolle und Runtime in `TESTING.md`.
- Autorisierter Push noch ausstehend; GitHub ist frisch verifiziert, aber nicht synchron.
- Kanonische Quelle angeglichen; historische Reproduktion 3/3 bytegenau, letzter
  Production-Build direkt aus der aktuellen Quelle mit historischem Marker reproduziert.
- Produktive Mehrkonten-/Quick-Add-Betreiberabnahme nicht ausdruecklich bestaetigt.

**GO fuer die Freigabepruefung des manuellen Push, noch keine Ausfuehrungsfreigabe.**
Der manuelle Push wird mit bestandenem Phase-B-Gate zur Freigabe vorgelegt. Kein weiterer
Production-Release ist hier freigegeben. Keine Migration/Saldoentscheidung,
keine Datenbank-/ENV-Aktion und kein Deployment in diesem Auftrag.
