import sqlite3
import tempfile
import unittest
from pathlib import Path

from rove_app_state import ensure_app_account_balances_table
from rove_expense_domain import begin_expense_write, create_expense_for_user
from rove_financial_accounts import ensure_financial_accounts_schema


class StabilitySprint5ExpenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clarity.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, current_cash REAL DEFAULT 0);
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                merchant TEXT,
                description TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO users (user_id, current_cash) VALUES (1, 100)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_same_request_id_is_one_booking_new_id_is_second_booking(self):
        begin_expense_write(self.conn)
        first = create_expense_for_user(
            self.conn, 1, amount=20, category="SHOPPING", merchant="Lidl",
            description="Via Telegram", request_id="telegram:1:10:expense:0",
        )
        self.conn.commit()

        begin_expense_write(self.conn)
        replay = create_expense_for_user(
            self.conn, 1, amount=20, category="SHOPPING", merchant="Lidl",
            description="Via Telegram", request_id="telegram:1:10:expense:0",
        )
        self.conn.commit()

        begin_expense_write(self.conn)
        second = create_expense_for_user(
            self.conn, 1, amount=20, category="SHOPPING", merchant="Lidl",
            description="Via Telegram", request_id="telegram:1:11:expense:0",
        )
        self.conn.commit()

        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertFalse(second["idempotent_replay"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_cash_movements").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT current_cash FROM users WHERE user_id = 1").fetchone()[0], 60)

    def test_multi_account_uses_expense_role_account(self):
        ensure_app_account_balances_table(self.conn)
        ensure_financial_accounts_schema(self.conn)
        self.conn.execute(
            """INSERT INTO app_user_features (user_id, feature_key, enabled)
               VALUES (1, 'multi_cash_accounts_v1', 1)"""
        )
        self.conn.execute(
            """INSERT INTO app_financial_accounts
               (user_id, account_type, name, currency, balance, legacy_key, source, status)
               VALUES (1, 'checking', 'Giro', 'EUR', 100, 'giro', 'legacy', 'active')"""
        )
        account_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """INSERT INTO app_financial_account_roles (user_id, role, account_id)
               VALUES (1, 'expense', ?)""",
            (account_id,),
        )
        self.conn.commit()

        begin_expense_write(self.conn)
        create_expense_for_user(
            self.conn, 1, amount=25, category="SHOPPING", merchant="Lidl",
            description="Via Telegram", request_id="telegram:1:20:expense:0",
        )
        self.conn.commit()

        movement = self.conn.execute(
            "SELECT source_account_id FROM app_cash_movements WHERE expense_id = 1"
        ).fetchone()
        account = self.conn.execute(
            "SELECT balance FROM app_financial_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        self.assertEqual(movement[0], account_id)
        self.assertEqual(account[0], 75)


if __name__ == "__main__":
    unittest.main()
