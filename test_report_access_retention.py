import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_account_delete_cleanup as cleanup
import rove_app_api as api


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
                CREATE TABLE users (user_id INTEGER PRIMARY KEY);
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
                INSERT INTO users VALUES (1);
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

    def tearDown(self):
        self.tmp.cleanup()

    def client(self):
        return api.app.test_client()

    def test_valid_report_is_served_through_server_gate(self):
        with patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_REPORT_DIR", self.root):
            response = self.client().get("/v1/public-reports/token-1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"private report")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

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
