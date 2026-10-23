import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_account_delete_cleanup as cleanup
import rove_app_api as api
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session


class ReportAccessRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reports"
        self.report_dir = self.root / "token-1"
        self.report_dir.mkdir(parents=True)
        (self.report_dir / "index.html").write_text("private report", encoding="utf-8")
        self.db_path = Path(self.tmp.name) / "clarity.db"
        future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE users (user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10);
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE report_links (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    report_month TEXT NOT NULL,
                    html_path TEXT NOT NULL,
                    public_url TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT,
                    status TEXT NOT NULL
                );
                INSERT INTO users(user_id) VALUES (1), (2);
                INSERT INTO user_access VALUES (1, 'approved'), (2, 'approved');
                """
            )
            conn.execute(
                """INSERT INTO report_links
                   VALUES ('token-1', 1, '2026-08', ?, '', ?, '', 'active')""",
                (str(self.report_dir / "index.html"), future),
            )
            conn.execute(
                """INSERT INTO report_links
                   VALUES ('expired', 1, '2026-07', ?, '', ?, '', 'active')""",
                (str(self.report_dir / "index.html"), past),
            )
            conn.execute(
                """INSERT INTO report_links
                   VALUES ('revoked', 1, '2026-06', ?, '', ?, '', 'revoked')""",
                (str(self.report_dir / "index.html"), future),
            )
        self.auth_patch = patch.object(api, "AUTH_SECRET", "synthetic-report-access-secret")
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        for user in (1, 2):
            ensure_unlocked_test_session(self.db_path, user, f"report-session-{user}")

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, user=1):
        client = api.app.test_client()
        if user is not None:
            client.set_cookie(api.SESSION_COOKIE_NAME, f"report-session-{user}")
        return client

    def test_valid_report_is_served_through_server_gate(self):
        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            response = self.client().get("/v1/public-reports/token-1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"private report")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_anonymous_and_foreign_sessions_cannot_read_owner_report(self):
        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            for user, expected in ((None, 401), (2, 410)):
                with self.subTest(user=user):
                    response = self.client(user).get("/v1/public-reports/token-1/")
                    self.assertEqual(response.status_code, expected)
                    self.assertNotIn(b"private report", response.data)

    def test_link_token_is_not_a_session_and_query_cannot_override_owner(self):
        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            response = self.client(None).get(
                "/v1/public-reports/token-1/?user_id=1&token=report-session-1",
                headers={"Authorization": "Bearer report-session-1"},
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(self.client(2).get("/v1/public-reports/token-1/?user_id=1").status_code, 410)

    def test_locked_and_revoked_sessions_are_rejected(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE app_session_pins SET unlocked_at=NULL")
        with patch.object(api, "DB_PATH", self.db_path):
            self.assertEqual(self.client().get("/v1/public-reports/token-1/").status_code, 423)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE app_sessions SET revoked_at=CURRENT_TIMESTAMP")
            self.assertEqual(self.client().get("/v1/public-reports/token-1/").status_code, 401)

    def test_expired_and_revoked_reports_are_not_served(self):
        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            expired = self.client().get("/v1/public-reports/expired/")
            revoked = self.client().get("/v1/public-reports/revoked/")
            unknown = self.client().get("/v1/public-reports/unknown/")
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(revoked.status_code, 410)
        self.assertEqual(unknown.status_code, 410)
        self.assertNotIn(b"private report", expired.data)
        self.assertNotIn(b"private report", revoked.data)
        self.assertNotIn(b"private report", unknown.data)

    def test_deleted_account_blocks_report_even_if_file_cleanup_has_not_run(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE account_delete_file_cleanup (id INTEGER PRIMARY KEY, opaque_cleanup_id TEXT UNIQUE, internal_path TEXT, completed_at TEXT)")
            conn.execute("DELETE FROM report_links WHERE token = 'token-1'")
            conn.execute("DELETE FROM users WHERE user_id = 1")
            cleanup.queue_paths_in_conn(conn, (self.root,), [self.report_dir])

        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            response = self.client().get("/v1/public-reports/token-1/")
        self.assertEqual(response.status_code, 410)
        self.assertTrue((self.report_dir / "index.html").is_file())
        with sqlite3.connect(self.db_path) as conn:
            queued = conn.execute(
                "SELECT COUNT(*) FROM account_delete_file_cleanup WHERE completed_at IS NULL"
            ).fetchone()[0]
        self.assertEqual(queued, 1)

    def test_physical_cleanup_marks_persisted_path_complete(self):
        cleanup_db = self.db_path
        with sqlite3.connect(cleanup_db) as conn:
            cleanup.ensure_table(conn)
            cleanup.queue_paths_in_conn(conn, (self.root,), [self.report_dir])
        self.assertEqual(cleanup.retry_paths(cleanup_db, (self.root,)), 1)
        self.assertFalse(self.report_dir.exists())

    def test_nginx_reports_path_uses_api_gate_instead_of_static_alias(self):
        config = Path(__file__).parent.joinpath("deploy", "nginx", "getrove.conf.example").read_text()
        self.assertIn("proxy_pass http://127.0.0.1:5057/v1/public-reports/;", config)
        self.assertNotIn("alias /var/www/reports/;", config)


if __name__ == "__main__":
    unittest.main()
