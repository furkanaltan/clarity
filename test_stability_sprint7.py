import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import rove_app_api as api
import rove_account_delete_cleanup as cleanup
import rove_report_worker as worker


class DeleteCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "clarity.db"
        sqlite3.connect(self.db_path).close()
        self.state = self.root / "state"; self.reports = self.root / "reports"
        self.archive = self.root / "archive"; self.public = self.root / "public"
        for path in (self.state, self.reports, self.archive, self.public): path.mkdir()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path), patch.object(api, "PUBLIC_APP_STATE_DIR", self.state),
            patch.object(api, "REPORTS_DIR", self.reports), patch.object(api, "REPORTS_ARCHIVE_DIR", self.archive),
            patch.object(api, "PUBLIC_REPORT_DIR", self.public),
            patch.object(worker, "DB_PATH", self.db_path), patch.object(
                worker, "account_delete_cleanup_roots", return_value=(self.state, self.public, self.reports, self.archive)
            ),
        ]
        for patcher in self.patchers: patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers): patcher.stop()
        self.temp.cleanup()

    def records(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT internal_path, attempts, last_error, completed_at "
                "FROM account_delete_file_cleanup ORDER BY id"
            ).fetchall()

    def test_retry_record_success_missing_and_idempotency(self):
        target = self.reports / "rove_report_1_2026-07.pdf"; target.write_text("private")
        with patch.object(api, "_remove_cleanup_path", return_value="PermissionError"):
            api.queue_account_cleanup_failures([target])
        api.queue_account_cleanup_failures([target])
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(api.retry_account_delete_file_cleanup(), 1)
        self.assertFalse(target.exists())
        self.assertIsNotNone(self.records()[0][3])
        self.assertEqual(api.retry_account_delete_file_cleanup(), 0)

    def test_allowlist_traversal_and_foreign_file_are_blocked(self):
        own = self.reports / "rove_report_1_2026-07.pdf"; own.write_text("a")
        foreign = self.reports / "rove_report_2_2026-07.pdf"; foreign.write_text("b")
        outside = self.root / "outside.pdf"; outside.write_text("x")
        self.assertIsNone(api._remove_cleanup_path(own))
        self.assertTrue(foreign.exists())
        self.assertEqual(api._remove_cleanup_path(self.reports / ".." / "outside.pdf"), "path_not_allowed")
        self.assertTrue(outside.exists())

    def test_missing_file_is_successful(self):
        missing = self.state / "gone.json"
        api.queue_account_cleanup_failures([missing])
        self.assertEqual(api.retry_account_delete_file_cleanup(), 1)
        self.assertIsNotNone(self.records()[0][3])

    def test_failed_record_does_not_block_later_success(self):
        failed = self.reports / "locked.pdf"; failed.write_text("a")
        successful = self.reports / "ready.pdf"; successful.write_text("b")
        api.queue_account_cleanup_failures([failed, successful])
        remove = cleanup.remove_path

        def retry(path, roots):
            return "PermissionError" if path == failed else remove(path, roots)

        with patch.object(cleanup, "remove_path", side_effect=retry):
            self.assertEqual(api.retry_account_delete_file_cleanup(), 1)
        rows = self.records()
        self.assertEqual(rows[0][1:3], (1, "PermissionError"))
        self.assertIsNone(rows[0][3])
        self.assertFalse(successful.exists())
        self.assertIsNotNone(rows[1][3])

    def test_batch_limit_leaves_remaining_record_for_next_maintenance_run(self):
        paths = [self.state / f"state-{index}.json" for index in range(21)]
        api.queue_account_cleanup_failures(paths)
        self.assertEqual(api.retry_account_delete_file_cleanup(), 20)
        self.assertEqual(sum(row[3] is None for row in self.records()), 1)
        self.assertEqual(api.retry_account_delete_file_cleanup(), 1)
        self.assertEqual(sum(row[3] is None for row in self.records()), 0)

    def test_report_maintenance_processes_pending_cleanup_automatically(self):
        pending = self.reports / "restart-recovery.pdf"; pending.write_text("private")
        api.queue_account_cleanup_failures([pending])
        with patch.object(
            worker, "report_renderer_module", return_value=SimpleNamespace(cleanup_expired_reports=lambda: 0)
        ), patch.object(
            worker, "report_engine_module", return_value=SimpleNamespace(archive_old_reports=lambda: 0)
        ):
            result = worker.maintain_archives()
        self.assertEqual(result["account_delete_cleanup_completed"], 1)
        self.assertFalse(pending.exists())
        self.assertEqual(api.retry_account_delete_file_cleanup(), 0)


if __name__ == "__main__":
    unittest.main()
