import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_web_report_renderer as web_renderer
from repair_report_month import repair_report_month, report_repair_counts


class RepairReportMonthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "clarity.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.executescript(
            """
            CREATE TABLE app_accounts (user_id INTEGER, verified_at TEXT);
            CREATE TABLE user_access (user_id INTEGER, status TEXT);
            CREATE TABLE report_jobs (
                id INTEGER PRIMARY KEY, user_id INTEGER, report_month TEXT,
                scheduled_at TEXT, status TEXT, attempts INTEGER,
                last_error TEXT, updated_at TEXT
            );
            CREATE TABLE report_links (
                token TEXT PRIMARY KEY, user_id INTEGER, report_month TEXT,
                html_path TEXT, public_url TEXT, expires_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT
            );
            CREATE TABLE report_snapshots_v2 (
                id INTEGER PRIMARY KEY, user_id INTEGER, report_month TEXT,
                schema_version INTEGER, report_data_json TEXT
            );
            CREATE TABLE expenses (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL);
            INSERT INTO app_accounts VALUES (1, '2026-01-01'), (2, '');
            INSERT INTO user_access VALUES (1, 'approved'), (2, 'approved');
            INSERT INTO report_jobs VALUES
                (1, 1, '2026-08', '2026-09-01 10:00:00', 'sent', 1, '', '2026-09-01'),
                (2, 2, '2026-08', '2026-09-01 10:00:00', 'sent', 1, '', '2026-09-01');
            INSERT INTO report_links VALUES
                ('old-1', 1, '2026-08', '/old/1', '', '2026-10-01', '2026-09-01', 'active'),
                ('old-2', 2, '2026-08', '/old/2', '', '2026-10-01', '2026-09-01', 'active');
            INSERT INTO report_snapshots_v2 VALUES (1, 1, '2026-08', 2, '{}');
            INSERT INTO expenses VALUES (1, 1, 303.0);
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_repair_requeues_verified_app_user_only_and_preserves_truth(self):
        before_expense = self.conn.execute("SELECT * FROM expenses").fetchall()
        before_snapshot = self.conn.execute("SELECT * FROM report_snapshots_v2").fetchall()
        result = repair_report_month(self.conn, "2026-08")
        self.assertEqual(result["requeued_jobs"], 1)
        self.assertEqual(result["financial_rows_changed"], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM report_jobs WHERE id = 1").fetchone()[0], "pending")
        self.assertEqual(self.conn.execute("SELECT status FROM report_jobs WHERE id = 2").fetchone()[0], "sent")
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-1'").fetchone()[0], "active")
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-2'").fetchone()[0], "active")
        self.assertEqual(self.conn.execute("SELECT * FROM expenses").fetchall(), before_expense)
        self.assertEqual(self.conn.execute("SELECT * FROM report_snapshots_v2").fetchall(), before_snapshot)

    def test_processing_job_blocks_repair(self):
        self.conn.execute("UPDATE report_jobs SET status = 'processing' WHERE id = 1")
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError, "report_worker_still_processing"):
            repair_report_month(self.conn, "2026-08")
        self.assertEqual(report_repair_counts(self.conn, "2026-08")["processing_jobs"], 1)

    def test_successful_web_build_atomically_replaces_same_user_month_link(self):
        template = Path(self.tmp.name) / "report.html"
        public_dir = Path(self.tmp.name) / "public"
        template.write_text("<html></html>", encoding="utf-8")
        with patch.object(web_renderer, "DB_PATH", self.db), \
             patch.object(web_renderer, "TEMPLATE_PATH", template), \
             patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir), \
             patch.object(web_renderer, "PUBLIC_REPORT_BASE_URL", "https://getrove.de/reports"), \
             patch.object(web_renderer, "render_template", return_value="<html>new</html>"):
            result = web_renderer.build_web_report(1, "2026-08", {"meta": {}})

        with sqlite3.connect(self.db) as conn:
            old_status = conn.execute(
                "SELECT status FROM report_links WHERE token = 'old-1'"
            ).fetchone()[0]
            other_status = conn.execute(
                "SELECT status FROM report_links WHERE token = 'old-2'"
            ).fetchone()[0]
            new_status = conn.execute(
                "SELECT status FROM report_links WHERE token = ?", (result["token"],)
            ).fetchone()[0]
        self.assertEqual(old_status, "superseded")
        self.assertEqual(other_status, "active")
        self.assertEqual(new_status, "active")

    def test_failed_web_build_keeps_previous_link_active(self):
        template = Path(self.tmp.name) / "report.html"
        template.write_text("<html></html>", encoding="utf-8")
        with patch.object(web_renderer, "DB_PATH", self.db), \
             patch.object(web_renderer, "TEMPLATE_PATH", template), \
             patch.object(web_renderer, "PUBLIC_REPORT_DIR", Path(self.tmp.name) / "public"), \
             patch.object(web_renderer, "render_template", side_effect=RuntimeError("render_failed")):
            with self.assertRaisesRegex(RuntimeError, "render_failed"):
                web_renderer.build_web_report(1, "2026-08", {"meta": {}})

        status = self.conn.execute(
            "SELECT status FROM report_links WHERE token = 'old-1'"
        ).fetchone()[0]
        self.assertEqual(status, "active")


if __name__ == "__main__":
    unittest.main()
