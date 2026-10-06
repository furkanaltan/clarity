import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api
import rove_report_worker


class AuthRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "auth.db"
        self._create_schema()

    def tearDown(self):
        self.tmp.cleanup()

    def _create_schema(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE app_sessions (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE app_session_pins (session_id INTEGER PRIMARY KEY);
            CREATE TABLE app_login_codes (
                id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE app_password_reset_codes (
                id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE app_account_delete_codes (
                id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE app_invitations (
                id INTEGER PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE app_state_links (
                token TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
            CREATE TABLE app_auth_login_limits (
                subject_hash TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _stamp(days):
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def _run_cleanup(self):
        with patch.object(rove_report_worker, "DB_PATH", self.db_path), patch.object(
            rove_report_worker, "AUTH_RETENTION_GRACE_DAYS", 30
        ):
            return rove_report_worker.cleanup_auth_artifacts()

    def test_active_artifacts_survive_and_old_artifacts_are_removed(self):
        conn = sqlite3.connect(self.db_path)
        old = self._stamp(-31)
        future = self._stamp(1)
        conn.execute("INSERT INTO app_sessions VALUES (1, 1, ?, NULL)", (future,))
        conn.execute("INSERT INTO app_sessions VALUES (2, 1, ?, NULL)", (old,))
        conn.execute("INSERT INTO app_session_pins VALUES (1)")
        conn.execute("INSERT INTO app_session_pins VALUES (2)")
        for table in (
            "app_login_codes",
            "app_password_reset_codes",
            "app_account_delete_codes",
        ):
            conn.execute(f"INSERT INTO {table} VALUES (1, ?, NULL)", (future,))
            conn.execute(f"INSERT INTO {table} VALUES (2, ?, NULL)", (old,))
        conn.execute("INSERT INTO app_invitations VALUES (1, 'active@example.com', ?, NULL)", (future,))
        conn.execute("INSERT INTO app_invitations VALUES (2, 'old@example.com', ?, NULL)", (old,))
        conn.execute("INSERT INTO app_state_links VALUES ('active', 'active', ?, ?)", (future, future))
        conn.execute("INSERT INTO app_state_links VALUES ('old', 'revoked', ?, ?)", (old, old))
        conn.execute("INSERT INTO app_auth_login_limits VALUES ('active', ?)", (future,))
        conn.execute("INSERT INTO app_auth_login_limits VALUES ('old', ?)", (old,))
        conn.commit()
        conn.close()

        result = self._run_cleanup()
        self.assertGreater(result["sessions"], 0)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_sessions WHERE id=1").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_sessions WHERE id=2").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_session_pins WHERE session_id=1").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_session_pins WHERE session_id=2").fetchone()[0], 0)
        for table in (
            "app_login_codes",
            "app_password_reset_codes",
            "app_account_delete_codes",
        ):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id=1").fetchone()[0], 1)
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id=2").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_invitations WHERE email='active@example.com'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_invitations WHERE email='old@example.com'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_state_links WHERE token='active'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_state_links WHERE token='old'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_auth_login_limits WHERE subject_hash='active'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_auth_login_limits WHERE subject_hash='old'").fetchone()[0], 0)
        conn.close()

    def test_consumed_old_codes_and_invitations_are_removed(self):
        old = self._stamp(-31)
        conn = sqlite3.connect(self.db_path)
        for table in (
            "app_login_codes",
            "app_password_reset_codes",
            "app_account_delete_codes",
        ):
            conn.execute(f"INSERT INTO {table} VALUES (1, ?, ?)", (self._stamp(1), old))
        conn.execute("INSERT INTO app_invitations VALUES (1, 'used@example.com', ?, ?)", (self._stamp(1), old))
        conn.commit()
        conn.close()

        self._run_cleanup()

        conn = sqlite3.connect(self.db_path)
        for table in (
            "app_login_codes",
            "app_password_reset_codes",
            "app_account_delete_codes",
        ):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_invitations").fetchone()[0], 0)
        conn.close()

    def test_account_delete_targets_exact_owned_invitation_email(self):
        source = Path(rove_app_api.__file__).read_text(encoding="utf-8")
        self.assertIn("DELETE FROM app_invitations WHERE email = ?", source)
        self.assertIn("emails = [normalize_email(row[\"email\"])", source)


if __name__ == "__main__":
    unittest.main()
