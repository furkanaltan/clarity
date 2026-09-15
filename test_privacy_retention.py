import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import rove_account_delete_cleanup as cleanup
import rove_app_api as api


class PrivacyRetentionTests(unittest.TestCase):
    def test_delete_tombstone_survives_and_reapply_is_user_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "tombstones.jsonl"
            cleanup.record_delete_tombstone(1, ledger)
            self.assertEqual(cleanup.read_delete_tombstones(ledger), {1})
            db = root / "restore.db"
            with sqlite3.connect(db) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                    CREATE TABLE app_accounts (id INTEGER PRIMARY KEY, user_id INTEGER);
                    CREATE TABLE app_sessions (id INTEGER PRIMARY KEY, account_id INTEGER);
                    CREATE TABLE user_data (user_id INTEGER, value TEXT);
                    INSERT INTO users VALUES (1), (2);
                    INSERT INTO app_accounts VALUES (10, 1), (20, 2);
                    INSERT INTO app_sessions VALUES (100, 10), (200, 20);
                    INSERT INTO user_data VALUES (1, 'deleted'), (2, 'kept');
                    """
                )
                conn.execute("BEGIN IMMEDIATE")
                for user_id in cleanup.read_delete_tombstones(ledger):
                    api.delete_user_rows_for_tombstone(conn, user_id)
            with sqlite3.connect(db) as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE user_id=1").fetchone())
                self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE user_id=2").fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM user_data WHERE user_id=1").fetchone())
                self.assertIsNotNone(conn.execute("SELECT 1 FROM user_data WHERE user_id=2").fetchone())

    def test_tombstone_write_is_append_only_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            cleanup.record_delete_tombstone(42, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["user_id"], 42)
            self.assertNotIn("email", record)

    def test_export_allowlist_contains_financial_history_without_auth_tables(self):
        exported = {table for _label, table in api.DATA_EXPORT_TABLES}
        self.assertTrue({
            "monthly_financial_snapshots", "report_snapshots_v2", "app_consumer_debt_events"
        } <= exported)
        self.assertFalse({
            "app_credentials", "app_password_reset_codes", "app_sessions", "app_login_codes"
        } & exported)

    def test_export_rows_are_user_scoped_for_new_history_tables(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE monthly_financial_snapshots (user_id INTEGER, report_month TEXT, net_worth REAL);
                INSERT INTO monthly_financial_snapshots VALUES (1, '2026-08', 10), (2, '2026-08', 20);
                """
            )
            _columns, rows = api.export_table_rows(conn, "monthly_financial_snapshots", 1)
        self.assertEqual([row["net_worth"] for row in rows], [10.0])

    def test_frontend_payday_history_is_scoped_and_cleared(self):
        source = Path(__file__).parent.joinpath("frontend", "index.html").read_text(encoding="utf-8")
        self.assertIn("rove:${BRIDGE_USER_ID}:payday-hist", source)
        self.assertIn("clearPaydayHistory(userId)", source)
        self.assertIn("loadPaydayHistory();", source)


if __name__ == "__main__":
    unittest.main()
