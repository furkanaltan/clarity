import sqlite3
import tempfile
import unittest
from pathlib import Path

import rove_app_state as state
from rove_consumer_debt import ensure_consumer_debt_schema, net_worth_total


class MonthCloseSnapshotTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(
            """CREATE TABLE users (
                   user_id INTEGER PRIMARY KEY,
                   current_cash REAL,
                   current_investments REAL
               );
               INSERT INTO users VALUES (1,10000,0);
               CREATE TABLE app_account_balances (
                   user_id INTEGER NOT NULL,
                   account_key TEXT NOT NULL,
                   amount REAL NOT NULL,
                   PRIMARY KEY(user_id,account_key)
               );
               INSERT INTO app_account_balances VALUES (1,'giro',10000);
               INSERT INTO app_account_balances VALUES (1,'tagesgeld',0);
               INSERT INTO app_account_balances VALUES (1,'bargeld',0);
               CREATE TABLE monthly_snapshots (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id INTEGER NOT NULL,
                   month TEXT NOT NULL,
                   clarity_score INTEGER,
                   total_expenses REAL,
                   budget_ok INTEGER,
                   net_worth REAL,
                   UNIQUE(user_id,month)
               );"""
        )
        state.ensure_app_properties_table(self.conn)
        ensure_consumer_debt_schema(self.conn)
        self.conn.execute(
            "INSERT INTO app_properties(user_id,market_value,remaining_debt) VALUES (1,100000,120000)"
        )
        self.conn.execute(
            """INSERT INTO app_consumer_debts
               (user_id,name,debt_type,outstanding_balance,active)
               VALUES (1,'Kreditkarte','credit_card',5000,1)"""
        )
        self.conn.commit()

    def write_snapshot(self):
        return state.write_legacy_monthly_snapshot(
            self.conn, 1, "2026-08", 74, 1800, True
        )

    def test_snapshot_uses_canonical_net_worth_including_negative_property_equity_and_debt(self):
        snapshot_net_worth = self.write_snapshot()

        self.assertEqual(state.get_app_property(self.conn, 1)["equity"], -20000)
        self.assertEqual(snapshot_net_worth, -15000)
        self.assertEqual(net_worth_total(10000, 0, -20000, 5000), snapshot_net_worth)
        stored = self.conn.execute(
            "SELECT net_worth FROM monthly_snapshots WHERE user_id=1 AND month='2026-08'"
        ).fetchone()["net_worth"]
        self.assertEqual(stored, -15000)

    def test_consumer_debt_is_deducted_exactly_once(self):
        self.assertEqual(self.write_snapshot(), -15000)

    def test_enabled_financial_accounts_are_the_only_cash_source(self):
        self.conn.executescript(
            """CREATE TABLE app_user_features (
                   user_id INTEGER NOT NULL,
                   feature_key TEXT NOT NULL,
                   enabled INTEGER NOT NULL,
                   PRIMARY KEY(user_id,feature_key)
               );
               CREATE TABLE app_financial_accounts (
                   id INTEGER PRIMARY KEY,
                   user_id INTEGER NOT NULL,
                   account_type TEXT NOT NULL,
                   name TEXT NOT NULL,
                   currency TEXT NOT NULL,
                   balance REAL NOT NULL,
                   legacy_key TEXT,
                   source TEXT,
                   status TEXT NOT NULL,
                   created_at TEXT,
                   updated_at TEXT,
                   archived_at TEXT
               );
               INSERT INTO app_user_features VALUES (1,'multi_cash_accounts_v1',1);
               INSERT INTO app_financial_accounts
                   VALUES (1,1,'checking','Giro','EUR',9000,'giro','app','active',NULL,NULL,NULL);
               INSERT INTO app_financial_accounts
                   VALUES (2,1,'savings','Tagesgeld','EUR',1500,'tagesgeld','app','active',NULL,NULL,NULL);
               INSERT INTO app_financial_accounts
                   VALUES (3,1,'checking','Alt','EUR',20000,'old','app','archived',NULL,NULL,NULL);"""
        )
        self.conn.execute("UPDATE users SET current_cash=999999 WHERE user_id=1")

        self.assertEqual(self.write_snapshot(), -14500)

    def test_existing_historical_snapshot_is_never_rewritten(self):
        self.conn.execute(
            """INSERT INTO monthly_snapshots
               (user_id,month,clarity_score,total_expenses,budget_ok,net_worth)
               VALUES (1,'2026-08',41,1200,0,10000)"""
        )

        self.assertEqual(self.write_snapshot(), -15000)
        stored = self.conn.execute(
            """SELECT clarity_score,total_expenses,budget_ok,net_worth
                 FROM monthly_snapshots WHERE user_id=1 AND month='2026-08'"""
        ).fetchone()
        self.assertEqual(tuple(stored), (41, 1200, 0, 10000))


if __name__ == "__main__":
    unittest.main()
