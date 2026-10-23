# Rov.E Dynamische Mehrfachkonten - Sprint 1

Stand: 15.08.2026

Status: Lokal implementiert und gegen isolierte Datenbankkopien getestet. Kein produktives
`--apply` ausgefuehrt. Keine sichtbare App-Aenderung.

## A. Geaenderte Dateien

- `rove_financial_accounts.py`: additives Schema und strikt nutzergebundene Domain-Helper.
- `migrate_financial_accounts.py`: standardmaessig read-only Dry-Run, explizites Apply mit Backup.
- `rove_app_api.py`: zwei neue Exportbereiche und FK-sichere Kontoloeschung.
- `test_financial_accounts_sprint1.py`: isolierte Schema-, Migrations-, Sicherheits- und
  Regressionstests.
- Gemeinsame Status-/Handoff-Dokumentation.

Nicht geaendert wurden UI, `rove_app_state.py`, aktive Cash-Endpunkte, Monatsplan,
Screenshot-Import, ETF-Pfade, Reports, Score und Bot-Logik.

## B. Neue Tabellen

```sql
CREATE TABLE app_financial_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    account_type TEXT NOT NULL
                 CHECK(account_type IN ('checking', 'savings', 'wallet')),
    name         TEXT NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'EUR' CHECK(currency = 'EUR'),
    balance      REAL NOT NULL DEFAULT 0.0,
    legacy_key   TEXT
                 CHECK(legacy_key IS NULL OR legacy_key IN ('giro', 'tagesgeld', 'bargeld')),
    source       TEXT NOT NULL DEFAULT 'legacy' CHECK(source IN ('legacy', 'manual')),
    status       TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at  DATETIME,
    UNIQUE (user_id, id),
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_app_financial_accounts_legacy
ON app_financial_accounts(user_id, legacy_key)
WHERE legacy_key IS NOT NULL;

CREATE TABLE app_financial_account_roles (
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL
               CHECK(role IN ('expense', 'income', 'fixed_cost', 'screenshot')),
    account_id INTEGER NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role),
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY(user_id, account_id)
        REFERENCES app_financial_accounts(user_id, id) ON DELETE CASCADE
);

CREATE TABLE app_user_features (
    user_id     INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, feature_key),
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
```

`other_cash` ist in Sprint 1 bewusst noch nicht freigegeben. Solange es keinen sicheren
Legacy-Spiegel fuer diesen Typ gibt, bleiben nur Giro, Tagesgeld und Wallet erlaubt.

## C. Feature-Flag

- Tabelle: `app_user_features`.
- Key: `multi_cash_accounts_v1`.
- Kein Datensatz bedeutet aus.
- Die Migration legt keinen aktivierten Datensatz an.
- Bestehende API-Endpunkte fragen die Flag noch nicht ab und bleiben auf dem alten Pfad.

## D. Bootstrap-Regeln

- Existieren Detailzeilen, werden nur vorhandene `giro`-, `tagesgeld`- und `bargeld`-Zeilen mit
  exakt demselben Betrag uebernommen.
- Mapping: `giro -> checking`, `tagesgeld -> savings`, `bargeld -> wallet`.
- Existieren keine Detailzeilen, wird exakt ein `Girokonto` mit `balance = current_cash` erzeugt.
- Fehlt bei vorhandenen Detailzeilen Giro, wird nur ein 0-EUR-Giro fuer die Standardrollen
  angelegt. Es wird niemals still Tagesgeld ausgewaehlt und kein Geld verschoben.
- Die Rollen `expense`, `income`, `fixed_cost` und `screenshot` zeigen initial auf Legacy-Giro.
- Unbekannte Legacy-Keys blockieren den Nutzer statt interpretiert zu werden.

## E. Invarianten

- Bei Detailzeilen muss deren Cent-genaue Summe vorab `users.current_cash` entsprechen.
- Nach Bootstrap muss die Summe aller neuen Konten `users.current_cash` entsprechen.
- Abweichung blockiert und rollt nur diesen Nutzer zurueck; es gibt keine automatische Reparatur.
- `current_investments`, Immobilien-Eigenkapital, Nettovermoegen, Anzahl Ausgaben und Anzahl
  Cash-Movements werden vor/nach jeder Nutzermigration verglichen.
- Der vorbereitete Dual-Write-Helper verlangt eine offene Transaktion und spiegelt erst neue
  Konten, dann `app_account_balances` und `users.current_cash`. Kein aktiver Endpunkt nutzt ihn.

## F. Dry-Run

Lokale Rov.E-DB am 15.08.2026:

- Modus: `dry-run`.
- Nutzer: 1.
- Status: ready 1, blocked 0.
- Keine neue Tabelle wurde durch den Dry-Run angelegt.
- Bestehender Nutzer hatte in dieser lokalen Kopie nur `current_cash`; geplant war exakt ein
  Legacy-Giro mit demselben Betrag.

Der produktive Server-Dry-Run steht noch aus. Vor dessen gemeinsamer Pruefung wird kein
produktives Apply ausgefuehrt.

## G. Idempotenz

- Schema-Setup und Apply wurden auf einer DB-Kopie zweimal ausgefuehrt.
- Ergebnis nach erstem und zweitem Lauf: ein Konto, vier Rollen, keine aktivierte Flag.
- Der partielle Unique-Index verhindert doppelte `user_id + legacy_key`-Konten.
- Jedes Apply erzeugt vorab eine eigene SQLite-Backupdatei mit Mikrosekunden-Zeitstempel.

## H. Sicherheit

- Konto lesen/aendern und Rolle setzen verwenden immer `id + user_id`.
- Fremde Konto-ID liefert keinen Datensatz bzw. wird als nicht gefunden abgewiesen.
- Zusaetzlich erzwingt der zusammengesetzte Foreign Key, dass Rollen und Konto demselben Nutzer
  gehoeren, wenn Foreign Keys fuer die Verbindung aktiv sind.
- Getestet: Nutzer A kann das Konto von Nutzer B weder lesen, aendern noch als Rolle setzen.

## I. Export und Loeschung

- Export ergaenzt `financial_accounts.csv`, `financial_account_roles.csv` und dieselben Bereiche
  in `daten.json`. Alte Exportbereiche bleiben unveraendert.
- Kontoloeschung entfernt Rollen, Konten und Feature-Flags in FK-sicherer Reihenfolge und danach
  weiterhin alle anderen nutzergebundenen Tabellen.
- Isoliert getestet: Nutzer A vollstaendig entfernt, Nutzer B unveraendert.

## J. Regressionen

- 9 automatisierte Testfaelle, alle erfolgreich und ohne Resource-Warnings.
- Abgedeckt: reiner Dry-Run, Fallback, alle drei Legacy-Typen, negatives Giro, negatives
  `current_cash`, 0-EUR-Konto, App-only, approved/migriert, zweifaches Apply, Drift-Blockade,
  nutzerweise Transaktion, Nutzertrennung, Dual-Write, Export und Loeschung.
- SQLite `PRAGMA integrity_check`: `ok`.
- SQLite `PRAGMA foreign_key_check`: 0 Fehler.
- Echte lokale Rov.E-DB-Kopie: `/v1/state` vor/nach Apply jeweils HTTP 200 und JSON exakt gleich.
- Da die aktiven Geldpfade sowie Report-/Score-Module unberuehrt bleiben und deren Aggregate vor
  und nach der Migration geprueft werden, bleiben Cashflow, Monatsplan, Screenshot, ETF, Reports
  und Score in Sprint 1 auf ihrem bisherigen Verhalten.

## K. Bekannte Grenzen

- Produktive Bestandsdaten wurden noch nicht geprueft; der Server-Dry-Run ist der naechste Gate.
- Es gibt noch keine UI, kein Konto-CRUD und keine Kontoauswahl.
- Historische Buchungen und Cash-Movements besitzen weiterhin keine neue Konto-ID.
- Aktive Geldpfade schreiben weiterhin nur Legacy-Werte; der neue Dual-Write-Motor ist vorbereitet,
  aber bewusst nicht angeschlossen.
- Nur EUR und die drei bestehenden Cash-Typen sind erlaubt.
- Keine Depots, mehreren Immobilien, Sachwerte-Migration, Bank- oder Broker-API.

## L. Empfehlung fuer Sprint 2

Erst den produktiven Dry-Run gemeinsam pruefen. Danach kontrolliertes Apply mit automatischem
Backup und weiterhin ausgeschalteter Flag. Sprint 2 sollte neue nullable Konto-Referenzen an
Bewegungen und die atomare Dual-Write-Anbindung fuer einen einzelnen Pilotnutzer bauen. Keine UI,
bevor dessen Bestandsdaten und Rueckbuchungen vollstaendig getestet sind.
