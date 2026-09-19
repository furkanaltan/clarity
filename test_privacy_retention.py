import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_account_delete_cleanup as cleanup
import rove_app_api as api
import reapply_account_delete_tombstones as reapply


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

    def test_missing_or_corrupt_tombstone_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(cleanup.TombstoneLedgerError):
                cleanup.read_delete_tombstones(root / "missing.jsonl")
            corrupt = root / "corrupt.jsonl"
            corrupt.write_text('{"user_id": 1}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(cleanup.TombstoneLedgerError):
                cleanup.read_delete_tombstones(corrupt)

    def test_restore_command_stops_before_db_work_without_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "restore.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO users VALUES (101)")
            with patch.object(reapply.sys, "argv", ["reapply_account_delete_tombstones.py", "--db", str(db), "--ledger", str(Path(tmp) / "missing.jsonl")]):
                self.assertEqual(reapply.main(), 2)
            with sqlite3.connect(db) as conn:
                self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE user_id=101").fetchone())

    def test_restore_cleanup_removes_auth_artifacts_and_anonymizes_admin_events(self):
        user_id = 101
        email = "deleted@example.com"
        with patch.object(api, "AUTH_SECRET", "test-secret"), sqlite3.connect(":memory:") as conn:
            conn.executescript(
                """
                CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                CREATE TABLE app_accounts (id INTEGER PRIMARY KEY, user_id INTEGER, email TEXT);
                CREATE TABLE app_sessions (id INTEGER PRIMARY KEY, account_id INTEGER);
                CREATE TABLE app_session_pins (session_id INTEGER PRIMARY KEY);
                CREATE TABLE app_login_codes (id INTEGER PRIMARY KEY, email TEXT);
                CREATE TABLE app_invitations (id INTEGER PRIMARY KEY, email TEXT);
                CREATE TABLE app_auth_login_limits (subject_hash TEXT PRIMARY KEY);
                CREATE TABLE app_admin_events (
                    id INTEGER PRIMARY KEY, admin_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL, target_user_id INTEGER,
                    target_email TEXT DEFAULT '', details TEXT DEFAULT ''
                );
                CREATE TABLE user_data (user_id INTEGER, value TEXT);
                INSERT INTO users VALUES (101), (202);
                INSERT INTO app_accounts VALUES (1, 101, 'deleted@example.com'), (2, 202, 'kept@example.com');
                INSERT INTO app_sessions VALUES (11, 1), (22, 2);
                INSERT INTO app_session_pins VALUES (11), (22);
                INSERT INTO app_login_codes VALUES (1, 'deleted@example.com'), (2, 'kept@example.com');
                INSERT INTO app_invitations VALUES (1, 'deleted@example.com'), (2, 'kept@example.com');
                INSERT INTO app_admin_events VALUES (1, 202, 'access_revoke', 101, 'deleted@example.com', 'private');
                INSERT INTO app_admin_events VALUES (2, 101, 'invitation_created', NULL, 'other@example.com', 'private');
                INSERT INTO user_data VALUES (101, 'deleted'), (202, 'kept');
                """
            )
            user_scoped_tables = (
                "expenses", "category_budgets", "app_account_balances", "app_cash_movements",
                "app_contracts", "app_consumer_debts", "app_properties", "portfolio_holdings",
                "report_jobs", "report_snapshots_v2", "app_ai_conversations", "app_ai_usage",
                "app_push_subscriptions", "app_push_preferences", "app_state_links", "user_access",
            )
            for table in user_scoped_tables:
                conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, user_id INTEGER, value TEXT)')
                conn.execute(f'INSERT INTO "{table}" VALUES (1, 101, \'deleted\'), (2, 202, \'kept\')')
            conn.execute(
                "INSERT INTO app_auth_login_limits VALUES (?)",
                (api.login_account_subject(email),),
            )
            api.delete_user_rows_for_tombstone(conn, user_id)
            conn.commit()
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE user_id=101").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE user_id=202").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM app_sessions WHERE account_id=1").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM app_login_codes WHERE email=?", (email,)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM app_invitations WHERE email=?", (email,)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM app_auth_login_limits").fetchone())
            for table in user_scoped_tables:
                self.assertIsNone(conn.execute(f'SELECT 1 FROM "{table}" WHERE user_id=101').fetchone())
                self.assertIsNotNone(conn.execute(f'SELECT 1 FROM "{table}" WHERE user_id=202').fetchone())
            anonymized = conn.execute(
                "SELECT admin_user_id, target_user_id, target_email, details FROM app_admin_events WHERE id=1"
            ).fetchone()
            self.assertEqual(tuple(anonymized), (202, None, "", ""))
            self.assertEqual(
                tuple(conn.execute(
                    "SELECT admin_user_id, target_email, details FROM app_admin_events WHERE id=2"
                ).fetchone()),
                (0, "other@example.com", ""),
            )

    def test_frontend_account_delete_clears_only_rove_owned_storage(self):
        source = Path(__file__).parent.joinpath("frontend", "index.html").read_text(encoding="utf-8")
        self.assertIn("async function clearRoveBrowserData", source)
        self.assertIn("await clearRoveBrowserData(BRIDGE_USER_ID)", source)
        self.assertNotIn("localStorage.clear()", source)
        self.assertIn("caches.delete(name)", source)

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
