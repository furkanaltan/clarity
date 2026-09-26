import sqlite3
import os
import stat
import tempfile
import time
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
        old_dir = public_dir / "old-link"; old_dir.mkdir(parents=True)
        old_path = old_dir / "index.html"; old_path.write_text("old", encoding="utf-8")
        self.conn.execute(
            "UPDATE report_links SET html_path = ? WHERE token = 'old-1'", (str(old_path),)
        )
        self.conn.commit()
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
        self.assertTrue(old_dir.exists())
        self.assertEqual(stat.S_IMODE(Path(result["path"]).stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(Path(result["path"]).parent.stat().st_mode), 0o755)

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

    def test_failed_web_registration_removes_new_public_directory(self):
        template = Path(self.tmp.name) / "report.html"
        public_dir = Path(self.tmp.name) / "public"
        template.write_text("<html></html>", encoding="utf-8")

        class FailingConnection:
            def __init__(self, connection, mode):
                self.connection = connection
                self.mode = mode

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def execute(self, sql, params=()):
                if self.mode == "insert" and "INSERT INTO report_links" in sql:
                    raise sqlite3.IntegrityError("test_insert_failure")
                return self.connection.execute(sql, params)

            def commit(self):
                if self.mode == "commit":
                    raise sqlite3.OperationalError("test_commit_failure")
                return self.connection.commit()

        real_connect = sqlite3.connect

        def connect(path, *args, **kwargs):
            connection = real_connect(path, *args, **kwargs)
            return FailingConnection(connection, connect.mode)

        for mode in ("insert", "commit"):
            connect.mode = mode
            with patch.object(web_renderer, "DB_PATH", self.db), \
                 patch.object(web_renderer, "TEMPLATE_PATH", template), \
                 patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir), \
                 patch.object(web_renderer, "ensure_report_links_table"), \
                 patch.object(web_renderer.sqlite3, "connect", side_effect=connect), \
                 patch.object(web_renderer, "render_template", return_value="<html>new</html>"):
                with self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                    web_renderer.build_web_report(1, "2026-08", {"meta": {}})
            self.assertEqual(list(public_dir.iterdir()) if public_dir.exists() else [], [])

    def test_orphan_scan_keeps_fresh_and_referenced_directories(self):
        public_dir = Path(self.tmp.name) / "public"
        fresh_dir = public_dir / "fresh"; fresh_dir.mkdir(parents=True)
        old_dir = public_dir / "old"; old_dir.mkdir()
        referenced_dir = public_dir / "referenced"; referenced_dir.mkdir()
        for report_dir in (fresh_dir, old_dir, referenced_dir):
            (report_dir / "index.html").write_text("report", encoding="utf-8")
        old_timestamp = time.time() - (web_renderer.REPORT_ORPHAN_GRACE_DAYS + 1) * 86400
        os.utime(old_dir, (old_timestamp, old_timestamp))
        self.conn.execute(
            "UPDATE report_links SET html_path = ? WHERE token = 'old-1'",
            (str(referenced_dir / "index.html"),),
        )
        self.conn.commit()
        with patch.object(web_renderer, "DB_PATH", self.db), patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir):
            self.assertEqual(web_renderer.cleanup_orphan_reports(), 1)
        self.assertTrue(fresh_dir.exists())
        self.assertFalse(old_dir.exists())
        self.assertTrue(referenced_dir.exists())

    def test_new_report_cleanup_never_removes_directory_outside_root(self):
        public_dir = Path(self.tmp.name) / "public"
        outside_dir = Path(self.tmp.name) / "outside"
        outside_dir.mkdir(parents=True)
        (outside_dir / "index.html").write_text("keep", encoding="utf-8")
        with patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir):
            web_renderer.remove_unregistered_report_dir(outside_dir)
        self.assertTrue(outside_dir.exists())

    def test_cleanup_keeps_valid_active_report_and_removes_expired_active_report(self):
        public_dir = Path(self.tmp.name) / "public"
        valid_dir = public_dir / "valid"; valid_dir.mkdir(parents=True)
        expired_dir = public_dir / "expired"; expired_dir.mkdir()
        (valid_dir / "index.html").write_text("valid", encoding="utf-8")
        (expired_dir / "index.html").write_text("expired", encoding="utf-8")
        self.conn.executemany(
            "UPDATE report_links SET html_path = ?, expires_at = ? WHERE token = ?",
            [
                (str(valid_dir / "index.html"), "2026-10-02 00:00:00", "old-1"),
                (str(expired_dir / "index.html"), "2026-09-01 00:00:00", "old-2"),
            ],
        )
        self.conn.commit()

        with patch.object(web_renderer, "DB_PATH", self.db), patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir):
            removed = web_renderer.cleanup_expired_reports(web_renderer.datetime(2026, 9, 2))

        self.assertEqual(removed, 1)
        self.assertTrue(valid_dir.exists())
        self.assertFalse(expired_dir.exists())
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-1'").fetchone()[0], "active")
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-2'").fetchone()[0], "expired")

    def test_cleanup_removes_superseded_report_after_its_existing_expiry(self):
        public_dir = Path(self.tmp.name) / "public"
        report_dir = public_dir / "superseded"; report_dir.mkdir(parents=True)
        html_path = report_dir / "index.html"; html_path.write_text("old", encoding="utf-8")
        self.conn.execute(
            "UPDATE report_links SET status = 'superseded', html_path = ?, expires_at = ? WHERE token = 'old-1'",
            (str(html_path), "2026-09-01 00:00:00"),
        )
        self.conn.commit()

        with patch.object(web_renderer, "DB_PATH", self.db), patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir):
            removed = web_renderer.cleanup_expired_reports(web_renderer.datetime(2026, 9, 2))

        self.assertEqual(removed, 1)
        self.assertFalse(report_dir.exists())
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-1'").fetchone()[0], "expired")

    def test_cleanup_delete_failure_remains_retryable(self):
        public_dir = Path(self.tmp.name) / "public"
        report_dir = public_dir / "retry"; report_dir.mkdir(parents=True)
        html_path = report_dir / "index.html"; html_path.write_text("old", encoding="utf-8")
        self.conn.execute(
            "UPDATE report_links SET status = 'superseded', html_path = ?, expires_at = ? WHERE token = 'old-1'",
            (str(html_path), "2026-09-01 00:00:00"),
        )
        self.conn.commit()

        with patch.object(web_renderer, "DB_PATH", self.db), patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir), \
             patch.object(web_renderer.shutil, "rmtree", side_effect=PermissionError("denied")):
            self.assertEqual(web_renderer.cleanup_expired_reports(web_renderer.datetime(2026, 9, 2)), 0)

        self.assertTrue(report_dir.exists())
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-1'").fetchone()[0], "superseded")
        with patch.object(web_renderer, "DB_PATH", self.db), patch.object(web_renderer, "PUBLIC_REPORT_DIR", public_dir):
            self.assertEqual(web_renderer.cleanup_expired_reports(web_renderer.datetime(2026, 9, 2)), 1)
        self.assertFalse(report_dir.exists())
        self.assertEqual(self.conn.execute("SELECT status FROM report_links WHERE token = 'old-1'").fetchone()[0], "expired")


if __name__ == "__main__":
    unittest.main()
